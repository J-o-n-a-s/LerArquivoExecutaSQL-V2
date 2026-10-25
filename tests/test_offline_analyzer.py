"""Fase 2C-2: contratos do modo de análise offline estrito."""

import ast
import contextlib
import importlib
import io
import runpy
import sys
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch

from sql_classifier import MAX_BATCH_CHARS, MAX_LINE_CHARS, MAX_STATEMENT_CHARS


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "src" / "offline_analyzer.py"
DESTINATION = "DestinoAnaliseSintetico"
EXTERNAL = "ExternoAnaliseSintetico"
PRIVATE = "SEGREDO_OFFLINE_NAO_EXIBIR"
INSERT = f"INSERT INTO [dbo].[Sintetico] ([valor]) VALUES (N'{PRIVATE}')"
IDENTITY_ON = "SET IDENTITY_INSERT [dbo].[Sintetico] ON"
IDENTITY_OFF = "SET IDENTITY_INSERT [dbo].[Sintetico] OFF"
SESSION_SET = "SET ANSI_NULLS ON"
DATABASE_REFUSAL = "Instrução administrativa de banco reconhecida, mas não autorizada para descarte."
VIEW_REFUSAL = "Definição de view reconhecida, mas exige equivalência externa comprovada."


def create_database():
    return f"""CREATE DATABASE [{DESTINATION}]
CONTAINMENT = NONE
ON PRIMARY
( NAME = N'{PRIVATE}', FILENAME = N'X:\\privado\\dados.mdf', SIZE = 8192 KB, MAXSIZE = UNLIMITED, FILEGROWTH = 65536 KB )
LOG ON
( NAME = N'LogPrivado', FILENAME = N'X:\\privado\\log.ldf', SIZE = 8192 KB, MAXSIZE = 2048 GB, FILEGROWTH = 65536 KB )
WITH CATALOG_COLLATION = DATABASE_DEFAULT"""


class FailingStream:
    def __init__(self, lines, error):
        self.lines = iter(lines)
        self.error = error
        self.read_count = 0

    def readline(self, size=-1):
        self.read_count += 1
        try:
            return next(self.lines)
        except StopIteration:
            raise self.error


class StreamingCommands:
    def __init__(self, count):
        self.count = count
        self.index = 0
        self.max_requested = 0

    def readline(self, size=-1):
        self.max_requested = max(self.max_requested, size)
        if self.index >= self.count * 2:
            return ""
        line = INSERT + "\n" if self.index % 2 == 0 else "GO\n"
        self.index += 1
        return line

    def read(self, *args, **kwargs):
        raise AssertionError("O analisador tentou carregar o stream inteiro.")


class OfflineAnalyzerTests(unittest.TestCase):
    def module(self):
        return importlib.import_module("offline_analyzer")

    def analyze(self, text, *, target=DESTINATION):
        return self.module().analyze_stream(io.StringIO(text), target_database=target)

    def assert_counts(self, report, *, lines=0, ignored=0, commands=0,
                      inserts=0, identity=0, session=0):
        self.assertEqual(report.linhas_fisicas_lidas, lines)
        self.assertEqual(report.linhas_ignoradas, ignored)
        self.assertEqual(report.comandos_classificados, commands)
        self.assertEqual(report.inserts_classificados, inserts)
        self.assertEqual(report.identity_insert_classificados, identity)
        self.assertEqual(report.session_set_classificados, session)
        self.assertEqual(commands, inserts + identity + session)

    def assert_completed(self, report):
        offline = self.module()
        self.assertEqual(report.status, offline.AnalysisStatus.CONCLUIDO)
        self.assertTrue(report.processamento_completo)
        self.assertIsNone(report.linha_primeira_recusa)
        self.assertIsNone(report.motivo_recusa)
        self.assertIsNone(report.motivo_interrupcao)

    def assert_refused(self, report, *, line=None, reason=None):
        offline = self.module()
        self.assertEqual(report.status, offline.AnalysisStatus.RECUSADO)
        self.assertFalse(report.processamento_completo)
        self.assertIsNone(report.motivo_interrupcao)
        if line is not None:
            self.assertEqual(report.linha_primeira_recusa, line)
        if reason is not None:
            self.assertEqual(report.motivo_recusa, reason)

    def test_report_is_typed_immutable_and_classification_only(self):
        offline = self.module()
        expected = {
            "status", "linhas_fisicas_lidas", "linhas_ignoradas",
            "comandos_classificados", "inserts_classificados",
            "identity_insert_classificados", "session_set_classificados",
            "linha_primeira_recusa", "motivo_recusa", "motivo_interrupcao",
            "processamento_completo",
        }
        self.assertEqual({field.name for field in fields(offline.OfflineAnalysisReport)}, expected)
        report = offline.analyze_stream(io.StringIO(""), target_database=DESTINATION)
        with self.assertRaises((AttributeError, TypeError)):
            report.comandos_classificados = 1
        self.assertFalse(any("execut" in name or "commit" in name or "persist" in name
                             for name in expected))

    def test_empty_file_completes_with_zero_counts(self):
        report = self.analyze("")
        self.assert_completed(report)
        self.assert_counts(report)

    def test_one_insert_is_one_classified_command(self):
        report = self.analyze(INSERT + "\n")
        self.assert_completed(report)
        self.assert_counts(report, lines=1, commands=1, inserts=1)

    def test_multiple_inserts_are_counted_independently(self):
        report = self.analyze((INSERT + "\nGO\n") * 3)
        self.assert_completed(report)
        self.assert_counts(report, lines=6, ignored=3, commands=3, inserts=3)

    def test_multiple_inserts_on_one_line_are_commands_not_lines(self):
        report = self.analyze(INSERT + "; " + INSERT + "\n")
        self.assert_completed(report)
        self.assert_counts(report, lines=1, commands=2, inserts=2)

    def test_multiline_insert_is_one_command_and_two_lines(self):
        text = "INSERT INTO [dbo].[Sintetico] ([valor])\nVALUES (N'x')\n"
        report = self.analyze(text)
        self.assert_completed(report)
        self.assert_counts(report, lines=2, commands=1, inserts=1)

    def test_multirow_insert_is_not_treated_as_multiple_records(self):
        text = "INSERT INTO [dbo].[Sintetico] ([valor]) VALUES (N'a'), (N'b'), (N'c')\n"
        report = self.analyze(text)
        self.assert_completed(report)
        self.assert_counts(report, lines=1, commands=1, inserts=1)
        self.assertFalse(hasattr(report, "registros_inseridos"))

    def test_identity_insert_on_off_have_a_separate_counter(self):
        report = self.analyze(IDENTITY_ON + "\nGO\n" + IDENTITY_OFF + "\n")
        self.assert_completed(report)
        self.assert_counts(report, lines=3, ignored=1, commands=2, identity=2)

    def test_session_set_has_a_separate_counter(self):
        report = self.analyze(SESSION_SET + "\n")
        self.assert_completed(report)
        self.assert_counts(report, lines=1, commands=1, session=1)

    def test_matching_use_is_metadata_and_preserves_target_context(self):
        report = self.analyze(f"USE [{DESTINATION}]\nGO\n{INSERT}\n")
        self.assert_completed(report)
        self.assert_counts(report, lines=3, ignored=2, commands=1, inserts=1)

    def test_external_use_refuses_following_data(self):
        report = self.analyze(f"USE [{EXTERNAL}]\nGO\n{INSERT}\n")
        self.assert_refused(report, line=3)
        self.assert_counts(report, lines=3, ignored=2)

    def test_go_comments_blank_lines_and_recognized_ddl_are_ignored(self):
        text = "-- comentário\n\nGO\nCREATE TABLE [dbo].[Preparacao] ([v] INT)\nGO\n"
        report = self.analyze(text)
        self.assert_completed(report)
        self.assert_counts(report, lines=5, ignored=5)

    def test_recognized_view_is_refused_not_ignored(self):
        report = self.analyze("CREATE VIEW [dbo].[v] AS SELECT 1 AS [v]\n")
        self.assert_refused(report, line=1, reason=VIEW_REFUSAL)
        self.assert_counts(report, lines=1)

    def test_recognized_create_database_is_refused(self):
        report = self.analyze(create_database())
        self.assert_refused(report, line=1, reason=DATABASE_REFUSAL)
        self.assert_counts(report, lines=7)

    def test_recognized_alter_database_is_refused(self):
        report = self.analyze(f"ALTER DATABASE [{DESTINATION}] SET READ_WRITE\n")
        self.assert_refused(report, line=1, reason=DATABASE_REFUSAL)
        self.assert_counts(report, lines=1)

    def test_other_protected_instructions_remain_refused(self):
        statements = (
            "CREATE USER [u] WITHOUT LOGIN",
            "ALTER ROLE [r] ADD MEMBER [u]",
            "IF FULLTEXTSERVICEPROPERTY('IsFullTextInstalled')=1 EXEC sys.sp_fulltext_database @action='enable'",
        )
        for statement in statements:
            with self.subTest(statement=statement.split()[0:2]):
                report = self.analyze(statement + "\n")
                self.assert_refused(report, line=1)
                self.assert_counts(report, lines=1)

    def test_invalid_syntax_is_refused_with_sanitized_reason(self):
        report = self.analyze("INSERT INTO [dbo].[Segredo] VALUES (\n")
        self.assert_refused(report, line=2)
        self.assertNotIn("Segredo", report.motivo_recusa)
        self.assert_counts(report, lines=1)

    def test_line_limit_is_preserved(self):
        report = self.analyze(" " * (MAX_LINE_CHARS + 1) + "\n")
        self.assert_refused(report, line=1, reason="Limite de tamanho da linha excedido.")
        self.assert_counts(report, lines=1)

    def test_batch_limit_is_preserved(self):
        line = "-- " + "x" * 60_000 + "\n"
        report = self.analyze(line * (MAX_BATCH_CHARS // len(line) + 1))
        self.assert_refused(report, reason="Limite de tamanho do lote excedido.")
        self.assertEqual(report.comandos_classificados, 0)

    def test_command_limit_is_preserved(self):
        chunk = "x" * 8192 + "\n"
        text = "INSERT INTO [dbo].[Sintetico] ([valor]) VALUES (N'" + chunk * (MAX_STATEMENT_CHARS // len(chunk) + 1) + "')"
        report = self.analyze(text)
        self.assert_refused(report, reason="Limite de tamanho do comando excedido.")
        self.assertEqual(report.comandos_classificados, 0)

    def test_invalid_utf16_read_is_interrupted_without_private_details(self):
        offline = self.module()
        error = UnicodeDecodeError("utf-16", b"\x00", 0, 1, PRIVATE)
        report = offline.analyze_stream(FailingStream([], error), target_database=DESTINATION)
        self.assertEqual(report.status, offline.AnalysisStatus.INTERROMPIDO)
        self.assertEqual(report.motivo_interrupcao, "Falha de leitura do arquivo.")
        self.assertNotIn(PRIVATE, repr(report))
        self.assert_counts(report)

    def test_read_failure_preserves_previous_batch_statistics(self):
        offline = self.module()
        stream = FailingStream([INSERT + "\n", "GO\n"], OSError(PRIVATE))
        report = offline.analyze_stream(stream, target_database=DESTINATION)
        self.assertEqual(report.status, offline.AnalysisStatus.INTERROMPIDO)
        self.assertFalse(report.processamento_completo)
        self.assertEqual(report.motivo_interrupcao, "Falha de leitura do arquivo.")
        self.assert_counts(report, lines=2, ignored=1, commands=1, inserts=1)

    def test_keyboard_interrupt_preserves_previous_batch_statistics(self):
        offline = self.module()
        stream = FailingStream([INSERT + "\n", "GO\n"], KeyboardInterrupt(PRIVATE))
        report = offline.analyze_stream(stream, target_database=DESTINATION)
        self.assertEqual(report.status, offline.AnalysisStatus.INTERROMPIDO)
        self.assertEqual(report.motivo_interrupcao, "Análise interrompida pelo usuário.")
        self.assert_counts(report, lines=2, ignored=1, commands=1, inserts=1)

    def test_first_refusal_stops_without_reading_later_batches(self):
        offline = self.module()
        lines = [
            INSERT + "\n", "GO\n",
            "CREATE VIEW [dbo].[v] AS SELECT 1 AS [v]\n", "GO\n",
            INSERT + "\n", "GO\n",
        ]
        stream = FailingStream(lines, AssertionError("não deveria alcançar EOF"))
        report = offline.analyze_stream(stream, target_database=DESTINATION)
        self.assert_refused(report, line=3, reason=VIEW_REFUSAL)
        self.assertEqual(stream.read_count, 4)
        self.assert_counts(report, lines=4, ignored=2, commands=1, inserts=1)

    def test_multiple_analyses_do_not_share_state(self):
        offline = self.module()
        first = offline.analyze_stream(io.StringIO((INSERT + "\n") * 2), target_database=DESTINATION)
        second = offline.analyze_stream(io.StringIO(SESSION_SET + "\n"), target_database=DESTINATION)
        self.assertEqual(first.comandos_classificados, 2)
        self.assertEqual(second.comandos_classificados, 1)
        self.assertEqual(second.inserts_classificados, 0)
        self.assertEqual(second.session_set_classificados, 1)

    def test_command_text_is_never_accessed_or_accumulated(self):
        offline = self.module()

        class Command:
            kind = "INSERT"

            @property
            def text(self):
                raise AssertionError("O analisador acessou texto SQL.")

        class Reader:
            ignored_lines = 0

            def __init__(self, *args, on_line=None, **kwargs):
                on_line(1)

            def __iter__(self):
                yield Command()

        with patch.object(offline, "SQLCommandReader", Reader):
            report = offline.analyze_stream(io.StringIO(PRIVATE), target_database=DESTINATION)
        self.assert_counts(report, lines=1, commands=1, inserts=1)
        self.assertNotIn(PRIVATE, repr(report))

    def test_source_has_no_application_driver_or_execution_coupling(self):
        self.module()
        source = SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("main", imports)
        for forbidden in ("pyodbc", "cursor", ".execute(", ".commit(", ".rollback(", "list(reader)", ".text"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_synthetic_load_is_incremental_and_constant_space_by_contract(self):
        offline = self.module()
        stream = StreamingCommands(2_000)
        report = offline.analyze_stream(stream, target_database=DESTINATION)
        self.assert_completed(report)
        self.assert_counts(report, lines=4_000, ignored=2_000, commands=2_000, inserts=2_000)
        self.assertEqual(stream.max_requested, MAX_LINE_CHARS + 1)
        self.assertFalse(any(isinstance(value, list) for value in report.__dict__.values()))

    def test_importing_module_never_opens_a_file_or_runs_cli(self):
        self.module()
        with patch("builtins.open", side_effect=AssertionError("abriu arquivo no import")):
            namespace = runpy.run_path(str(SOURCE_PATH), run_name="offline_analyzer_import")
        self.assertNotEqual(namespace["__name__"], "__main__")

    def test_cli_requires_explicit_file_and_database(self):
        offline = self.module()
        stderr = io.StringIO()
        with patch("builtins.open", side_effect=AssertionError("abriu arquivo sem argumentos")), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                offline.main([])
        self.assertEqual(stopped.exception.code, 2)

    def test_cli_unknown_argument_never_echoes_its_private_value(self):
        offline = self.module()
        stderr = io.StringIO()
        arguments = ["--file", "fixture.sql", "--database", DESTINATION,
                     "--invalido", PRIVATE]
        with patch("builtins.open", side_effect=AssertionError("abriu arquivo com argumento inválido")), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                offline.main(arguments)
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(stderr.getvalue().splitlines()[-1], "Erro: argumentos inválidos.")
        self.assertNotIn(PRIVATE, stderr.getvalue())

    def test_cli_excess_private_path_is_not_echoed(self):
        offline = self.module()
        private_path = "C:\\privado\\arquivo-confidencial.sql"
        stderr = io.StringIO()
        arguments = ["--file", "fixture.sql", "--database", DESTINATION, private_path]
        with patch("builtins.open", side_effect=AssertionError("abriu arquivo com excedente")), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                offline.main(arguments)
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(stderr.getvalue().splitlines()[-1], "Erro: argumentos inválidos.")
        self.assertNotIn(private_path, stderr.getvalue())

    def test_cli_missing_required_argument_uses_fixed_error(self):
        offline = self.module()
        stderr = io.StringIO()
        with patch("builtins.open", side_effect=AssertionError("abriu arquivo sem database")), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                offline.main(["--file", PRIVATE])
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(stderr.getvalue().splitlines()[-1], "Erro: argumentos inválidos.")
        self.assertNotIn(PRIVATE, stderr.getvalue())

    def test_cli_option_without_value_uses_fixed_error(self):
        offline = self.module()
        stderr = io.StringIO()
        with patch("builtins.open", side_effect=AssertionError("abriu arquivo sem valor")), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                offline.main(["--file", "fixture.sql", "--database"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(stderr.getvalue().splitlines()[-1], "Erro: argumentos inválidos.")

    def test_cli_help_remains_available_without_opening_file(self):
        offline = self.module()
        stdout = io.StringIO()
        with patch("builtins.open", side_effect=AssertionError("abriu arquivo no help")), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as stopped:
                offline.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--file", stdout.getvalue())
        self.assertIn("--database", stdout.getvalue())

    def test_cli_opens_only_requested_file_as_utf16_and_prints_sanitized_counts(self):
        offline = self.module()
        stream = io.StringIO(INSERT + "\n")
        context = MagicMock()
        context.__enter__.return_value = stream
        context.__exit__.return_value = False
        output = io.StringIO()
        with patch("builtins.open", return_value=context) as opened, contextlib.redirect_stdout(output):
            code = offline.main(["--file", PRIVATE + ".sql", "--database", DESTINATION])
        self.assertEqual(code, 0)
        opened.assert_called_once_with(PRIVATE + ".sql", "r", encoding="utf-16")
        rendered = output.getvalue()
        self.assertIn("Comandos classificados: 1", rendered)
        for private in (PRIVATE, DESTINATION, INSERT, "Sintetico"):
            self.assertNotIn(private, rendered)
        for forbidden in ("inseridas", "persistidos", "confirmados", "commit"):
            self.assertNotIn(forbidden, rendered.lower())

    def test_cli_refusal_is_sanitized_and_returns_nonzero(self):
        offline = self.module()
        stream = io.StringIO(f"DELETE FROM [{PRIVATE}]\n")
        context = MagicMock()
        context.__enter__.return_value = stream
        context.__exit__.return_value = False
        output = io.StringIO()
        with patch("builtins.open", return_value=context), contextlib.redirect_stdout(output):
            code = offline.main(["--file", PRIVATE + ".sql", "--database", DESTINATION])
        self.assertEqual(code, 2)
        self.assertIn("Status: recusado", output.getvalue())
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertNotIn(DESTINATION, output.getvalue())

    def test_cli_open_error_does_not_echo_path_or_operating_system_message(self):
        offline = self.module()
        output = io.StringIO()
        with patch("builtins.open", side_effect=OSError(PRIVATE)), contextlib.redirect_stdout(output):
            code = offline.main(["--file", PRIVATE + ".sql", "--database", DESTINATION])
        self.assertEqual(code, 1)
        self.assertIn("Falha de leitura do arquivo", output.getvalue())
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertNotIn(DESTINATION, output.getvalue())

    def test_cli_keyboard_interrupt_keeps_exit_code_130(self):
        offline = self.module()
        stream = FailingStream([], KeyboardInterrupt(PRIVATE))
        context = MagicMock()
        context.__enter__.return_value = stream
        context.__exit__.return_value = False
        output = io.StringIO()
        with patch("builtins.open", return_value=context), contextlib.redirect_stdout(output):
            code = offline.main(["--file", PRIVATE + ".sql", "--database", DESTINATION])
        self.assertEqual(code, 130)
        self.assertNotIn(PRIVATE, output.getvalue())


if __name__ == "__main__":
    unittest.main()
