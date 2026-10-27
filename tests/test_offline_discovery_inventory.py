"""Contratos 0.2.0 para descoberta e inventário exclusivamente offline."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import offline_analyzer
from sql_classifier import MAX_BATCH_CHARS, MAX_LINE_CHARS


SCADA = "SCADA"
OTHER = "OutroBanco"


class FailingStream:
    def __init__(self, lines, error):
        self._lines = iter(lines)
        self._error = error
        self.read_count = 0

    def readline(self, size=-1):
        self.read_count += 1
        try:
            return next(self._lines)
        except StopIteration:
            raise self._error

    def read(self, *args, **kwargs):
        raise AssertionError("A análise tentou carregar o arquivo inteiro.")


class GeneratedBatches:
    def __init__(self, count):
        self.count = count
        self.index = 0
        self.max_requested = 0

    def readline(self, size=-1):
        self.max_requested = max(self.max_requested, size)
        if self.index >= self.count * 2:
            return ""
        line = "INSERT INTO [SCADA].[dbo].[T] VALUES (1)\n" if self.index % 2 == 0 else "GO\n"
        self.index += 1
        return line

    def read(self, *args, **kwargs):
        raise AssertionError("A análise tentou carregar o arquivo inteiro.")


class InteractiveOutput(io.StringIO):
    def isatty(self):
        return True


class FakeClock:
    def __init__(self):
        self.current = 0.0
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.current


class DiscoveryTests(unittest.TestCase):
    def discover(self, text, **kwargs):
        return offline_analyzer.discover_database_stream(io.StringIO(text), **kwargs)

    def test_create_database_is_strong_evidence(self):
        report = self.discover("CREATE DATABASE [SCADA]\n")
        self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.DETERMINADO)
        self.assertEqual(report.banco_candidato, SCADA)
        self.assertEqual(report.confianca, "evidência estrutural consistente")
        self.assertEqual(report.cobertura, offline_analyzer.Coverage.COMPLETA)

    def test_master_context_does_not_override_created_database(self):
        text = "USE [master]\nGO\nCREATE DATABASE [SCADA]\nGO\nALTER DATABASE [SCADA] SET COMPATIBILITY_LEVEL = 150\nGO\n"
        report = self.discover(text)
        self.assertEqual(report.banco_candidato, SCADA)
        self.assertEqual(report.bancos_candidatos, (SCADA,))

    def test_isolated_administrative_use_does_not_determine_destination(self):
        for database in ("master", "tempdb", "model", "msdb"):
            with self.subTest(database=database):
                report = self.discover(f"USE [{database}]\nGO\n")
                self.assertEqual(
                    report.status,
                    offline_analyzer.DiscoveryStatus.NAO_DETERMINADO,
                )
                self.assertIsNone(report.banco_candidato)
                self.assertEqual(report.bancos_candidatos, ())
                self.assertEqual(report.confianca, "evidência insuficiente")

    def test_multiple_administrative_use_statements_remain_undetermined(self):
        report = self.discover("USE [master]\nGO\nUSE [tempdb]\nGO\n")
        self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.NAO_DETERMINADO)
        self.assertIsNone(report.banco_candidato)
        self.assertEqual(report.bancos_candidatos, ())

    def test_administrative_use_followed_by_user_use_selects_only_user_database(self):
        report = self.discover("USE [master]\nGO\nUSE [SCADA]\nGO\n")
        self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.DETERMINADO)
        self.assertEqual(report.banco_candidato, SCADA)
        self.assertEqual(report.bancos_candidatos, (SCADA,))
        self.assertEqual(report.confianca, "contexto USE isolado")

    def test_administrative_name_from_qualified_insert_remains_strong_evidence(self):
        report = self.discover("INSERT INTO [master].[dbo].[T] VALUES (1)\n")
        self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.DETERMINADO)
        self.assertEqual(report.banco_candidato, "master")
        self.assertEqual(report.bancos_candidatos, ("master",))
        self.assertEqual(report.confianca, "referência de banco qualificada")

    def test_administrative_use_filter_is_case_insensitive(self):
        isolated = self.discover("USE [MaStEr]\nGO\n")
        mixed = self.discover("USE [MASTER]\nGO\nUSE [ScAdA]\nGO\n")
        self.assertEqual(isolated.status, offline_analyzer.DiscoveryStatus.NAO_DETERMINADO)
        self.assertEqual(mixed.status, offline_analyzer.DiscoveryStatus.DETERMINADO)
        self.assertEqual(mixed.banco_candidato, "ScAdA")

    def test_create_and_alter_corroborate_user_database(self):
        report = self.discover(
            "CREATE DATABASE [SCADA]\nGO\n"
            "ALTER DATABASE [SCADA] SET COMPATIBILITY_LEVEL = 150\nGO\n"
        )
        self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.DETERMINADO)
        self.assertEqual(report.banco_candidato, SCADA)
        self.assertEqual(report.confianca, "evidência estrutural consistente")

    def test_use_and_qualified_insert_are_structural_evidence(self):
        use = self.discover("USE [SCADA]\nGO\n")
        insert = self.discover("INSERT INTO [SCADA].[dbo].[Tabela] VALUES (1)\n")
        self.assertEqual(use.banco_candidato, SCADA)
        self.assertEqual(insert.banco_candidato, SCADA)

    def test_comments_and_strings_do_not_create_false_candidates(self):
        text = "-- CREATE DATABASE [Falso]\n/* USE [Falso2] */\nINSERT INTO [SCADA].[dbo].[T] VALUES (N'ALTER DATABASE [Falso3]')\n"
        report = self.discover(text)
        self.assertEqual(report.bancos_candidatos, (SCADA,))

    def test_go_inside_string_comment_and_identifier_is_not_a_boundary(self):
        text = "INSERT INTO [SCADA].[dbo].[GO] VALUES (N'\nGO\n') /*\nGO\n*/\nGO\n"
        report = self.discover(text)
        self.assertEqual(report.banco_candidato, SCADA)
        self.assertEqual(report.lotes_analisados, 1)

    def test_conflicting_structural_candidates_are_ambiguous(self):
        report = self.discover("CREATE DATABASE [SCADA]\nGO\nALTER DATABASE [OutroBanco] SET READ_WRITE\n")
        self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.AMBIGUO)
        self.assertIsNone(report.banco_candidato)
        self.assertEqual(set(report.bancos_candidatos), {SCADA, OTHER})

    def test_mixed_database_batch_is_not_used_as_discovery_evidence(self):
        report = self.discover(
            "CREATE DATABASE [SCADA]\nINSERT INTO [SCADA].[dbo].[T] VALUES (1)\n"
        )
        self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.NAO_DETERMINADO)

    def test_unsafe_database_batches_do_not_supply_discovery_evidence(self):
        batches = (
            "CREATE DATABASE [SCADA]\nGRANT CONTROL TO [Usuario]\nGO\n",
            "ALTER DATABASE [SCADA] SET\nGO\n",
            "CREATE DATABASE [SCADA] SELECT 1\nGO\n",
        )
        for text in batches:
            with self.subTest(first_line=text.splitlines()[0]):
                report = self.discover(text)
                self.assertEqual(
                    report.status,
                    offline_analyzer.DiscoveryStatus.NAO_DETERMINADO,
                )
                self.assertIsNone(report.banco_candidato)
                self.assertEqual(report.bancos_candidatos, ())

    def test_control_characters_are_never_public_database_candidates(self):
        names = (
            "SCADA\nLINHA_FORJADA",
            "SCADA\x1b[31m",
            "SCADA\x9b31m",
        )
        for name in names:
            with self.subTest(name=repr(name)):
                report = self.discover(f"CREATE DATABASE [{name}]\n")
                self.assertEqual(
                    report.status,
                    offline_analyzer.DiscoveryStatus.NAO_DETERMINADO,
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    offline_analyzer._print_discovery_report(report)
                rendered = output.getvalue()
                self.assertNotIn("LINHA_FORJADA", rendered)
                self.assertNotIn("\x1b", rendered)
                self.assertNotIn("\x9b", rendered)

    def test_empty_and_evidence_free_files_are_not_determined(self):
        for text in ("", "SELECT N'SCADA'\n"):
            with self.subTest(text=text):
                report = self.discover(text)
                self.assertEqual(report.status, offline_analyzer.DiscoveryStatus.NAO_DETERMINADO)
                self.assertIsNone(report.banco_candidato)

    def test_batch_limit_makes_coverage_preliminary_without_claiming_uniqueness(self):
        text = "CREATE DATABASE [SCADA]\nGO\nCREATE DATABASE [OutroBanco]\nGO\n"
        report = self.discover(text, max_batches=1)
        self.assertEqual(report.banco_candidato, SCADA)
        self.assertEqual(report.cobertura, offline_analyzer.Coverage.PRELIMINAR)
        self.assertFalse(report.processamento_completo)

        eof_report = self.discover("CREATE DATABASE [SCADA]\n", max_batches=1)
        self.assertEqual(eof_report.cobertura, offline_analyzer.Coverage.COMPLETA)

    def test_discovery_is_incremental_and_handles_interruptions(self):
        stream = FailingStream(["CREATE DATABASE [SCADA]\n", "GO\n"], KeyboardInterrupt("privado"))
        report = offline_analyzer.discover_database_stream(stream)
        self.assertEqual(report.cobertura, offline_analyzer.Coverage.PARCIAL)
        self.assertEqual(report.motivo_interrupcao, "Análise interrompida pelo usuário.")
        self.assertNotIn("privado", repr(report))
        self.assertGreater(stream.read_count, 0)

    def test_utf16_file_with_bom_is_opened_incrementally(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sql"
            path.write_text("CREATE DATABASE [SCADA]\n", encoding="utf-16")
            report = offline_analyzer.discover_database_file(str(path))
        self.assertEqual(report.banco_candidato, SCADA)

    def test_close_failure_keeps_discovered_candidate_but_marks_partial(self):
        context = MagicMock()
        context.__enter__.return_value = io.StringIO("CREATE DATABASE [SCADA]\n")
        context.__exit__.side_effect = OSError("privado")
        with patch("builtins.open", return_value=context):
            report = offline_analyzer.discover_database_file("privado.sql")
        self.assertEqual(report.banco_candidato, SCADA)
        self.assertEqual(report.cobertura, offline_analyzer.Coverage.PARCIAL)
        self.assertNotIn("privado", repr(report))


class InventoryTests(unittest.TestCase):
    def inventory(self, text):
        return offline_analyzer.inventory_stream(io.StringIO(text))

    def test_inventory_separates_batches_commands_and_families(self):
        text = (
            "INSERT INTO [SCADA].[dbo].[T] VALUES (1); SET IDENTITY_INSERT [dbo].[T] ON; SET ANSI_NULLS ON\nGO\n"
            "CREATE TABLE [dbo].[T] ([id] INT)\nGO\n"
            "CREATE VIEW [dbo].[v] AS SELECT [id] FROM [dbo].[T]\nGO\n"
            "ALTER DATABASE [SCADA] SET READ_WRITE\nGO\n"
        )
        report = self.inventory(text)
        self.assertTrue(report.processamento_completo)
        self.assertEqual(report.lotes, 4)
        self.assertEqual(report.comandos, 6)
        self.assertEqual(report.comandos_reconhecidos, 6)
        self.assertEqual(report.inserts, 1)
        self.assertEqual(report.identity_insert, 1)
        self.assertEqual(report.session_set, 1)
        self.assertEqual(report.ddl_reconhecido, 3)
        self.assertEqual(report.alter_database, 1)
        self.assertEqual(report.views, 1)
        self.assertEqual(report.lotes_nao_classificaveis, 0)

    def test_recognized_but_refused_batches_do_not_stop_inventory(self):
        text = "CREATE DATABASE [SCADA]\nGO\nCREATE VIEW [dbo].[v] AS SELECT 1 AS [x]\nGO\nINSERT INTO [SCADA].[dbo].[T] VALUES (1)\n"
        report = self.inventory(text)
        self.assertTrue(report.processamento_completo)
        self.assertEqual(report.lotes, 3)
        self.assertEqual(report.comandos, 3)
        self.assertEqual(report.create_database, 1)
        self.assertEqual(report.views, 1)
        self.assertEqual(report.inserts, 1)

    def test_unclassifiable_batch_is_recorded_without_inventing_commands(self):
        report = self.inventory("COMANDO_DESCONHECIDO x; OUTRO y\nGO\nINSERT INTO [SCADA].[dbo].[T] VALUES (1)\n")
        self.assertTrue(report.processamento_completo)
        self.assertEqual(report.lotes, 2)
        self.assertEqual(report.lotes_nao_classificaveis, 1)
        self.assertEqual(report.comandos, 1)
        self.assertEqual(report.comandos_reconhecidos, 1)

    def test_mixed_database_and_data_batch_does_not_invent_a_count(self):
        report = self.inventory(
            "CREATE DATABASE [SCADA]\nINSERT INTO [SCADA].[dbo].[T] VALUES (1)\n"
        )
        self.assertTrue(report.processamento_completo)
        self.assertEqual(report.lotes, 1)
        self.assertEqual(report.lotes_nao_classificaveis, 1)
        self.assertEqual(report.comandos, 0)
        self.assertEqual(report.create_database, 0)

    def test_superficial_families_and_unsafe_administration_are_unclassifiable(self):
        cases = {
            "two_selects": "SELECT 1\nSELECT 2\nGO\n",
            "select_update": "SELECT 1\nUPDATE [dbo].[T] SET [x] = 2\nGO\n",
            "database_grant": "CREATE DATABASE [SCADA]\nGRANT CONTROL TO [Usuario]\nGO\n",
            "incomplete_alter_database": "ALTER DATABASE [SCADA] SET\nGO\n",
            "database_select": "CREATE DATABASE [SCADA] SELECT 1\nGO\n",
            "two_execs": "EXEC [dbo].[RotinaA]\nEXEC [dbo].[RotinaB]\nGO\n",
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                report = self.inventory(text)
                self.assertTrue(report.processamento_completo)
                self.assertEqual(report.lotes, 1)
                self.assertEqual(report.lotes_nao_classificaveis, 1)
                self.assertEqual(report.comandos, 0)
                self.assertEqual(report.comandos_reconhecidos, 0)
                self.assertEqual(report.outras_familias, 0)
                self.assertEqual(report.create_database, 0)
                self.assertEqual(report.alter_database, 0)
                self.assertEqual(report.bancos_candidatos, ())

    def test_unclassifiable_batch_does_not_stop_at_safe_go_boundary(self):
        text = (
            "SELECT 1\nSELECT 2\nGO\n"
            "INSERT INTO [SCADA].[dbo].[T] VALUES (1)\nGO\n"
        )
        report = self.inventory(text)
        self.assertTrue(report.processamento_completo)
        self.assertEqual(report.lotes, 2)
        self.assertEqual(report.lotes_nao_classificaveis, 1)
        self.assertEqual(report.comandos, 1)
        self.assertEqual(report.inserts, 1)
        self.assertEqual(report.bancos_candidatos, (SCADA,))

    def test_multiple_commands_in_one_batch_are_counted_as_commands(self):
        report = self.inventory("INSERT INTO [dbo].[T] VALUES (1); INSERT INTO [dbo].[T] VALUES (2)\n")
        self.assertEqual(report.lotes, 1)
        self.assertEqual(report.comandos, 2)
        self.assertEqual(report.inserts, 2)

    def test_go_in_strings_and_comments_does_not_inflate_batches(self):
        text = "INSERT INTO [dbo].[T] VALUES (N'GO') /* GO */\nGO -- separador\n"
        report = self.inventory(text)
        self.assertEqual(report.lotes, 1)
        self.assertEqual(report.comandos, 1)

    def test_read_failure_preserves_only_prior_valid_batches(self):
        stream = FailingStream(["INSERT INTO [dbo].[T] VALUES (1)\n", "GO\n"], OSError("privado"))
        report = offline_analyzer.inventory_stream(stream)
        self.assertFalse(report.processamento_completo)
        self.assertEqual(report.cobertura, offline_analyzer.Coverage.PARCIAL)
        self.assertEqual(report.lotes, 1)
        self.assertEqual(report.inserts, 1)
        self.assertEqual(report.motivo_interrupcao, "Falha de leitura do arquivo.")
        self.assertNotIn("privado", repr(report))

    def test_line_batch_and_lexical_limits_degrade_explicitly(self):
        cases = (
            "x" * (MAX_LINE_CHARS + 1) + "\n",
            ("-- " + "x" * 60_000 + "\n") * (MAX_BATCH_CHARS // 60_001 + 2),
            "/* comentário não encerrado\n",
        )
        for text in cases:
            with self.subTest(size=len(text)):
                report = self.inventory(text)
                self.assertFalse(report.processamento_completo)
                self.assertEqual(report.cobertura, offline_analyzer.Coverage.PARCIAL)

    def test_inventory_is_incremental_with_bounded_batch_memory(self):
        stream = GeneratedBatches(2_000)
        report = offline_analyzer.inventory_stream(stream)
        self.assertTrue(report.processamento_completo)
        self.assertEqual(report.lotes, 2_000)
        self.assertEqual(report.inserts, 2_000)
        self.assertEqual(stream.max_requested, MAX_LINE_CHARS + 1)

    def test_candidates_are_reported_without_exposing_sql_values(self):
        private = "SEGREDO_NAO_EXIBIR"
        report = self.inventory(f"INSERT INTO [SCADA].[dbo].[T] VALUES (N'{private}')\n")
        self.assertEqual(report.bancos_candidatos, (SCADA,))
        self.assertNotIn(private, repr(report))


class NewCliTests(unittest.TestCase):
    def _context(self, text):
        context = MagicMock()
        context.__enter__.return_value = io.StringIO(text)
        context.__exit__.return_value = False
        return context

    def test_absent_database_selects_discovery_mode(self):
        output = io.StringIO()
        with patch("builtins.open", return_value=self._context("CREATE DATABASE [SCADA]\n")), contextlib.redirect_stdout(output):
            code = offline_analyzer.main(["--file", "fixture.sql"])
        self.assertEqual(code, 0)
        self.assertIn("Modo: descoberta de banco", output.getvalue())
        self.assertIn("Banco candidato: SCADA", output.getvalue())
        self.assertIn("Conexão com SQL Server: nenhuma", output.getvalue())

    def test_inventory_cli_and_invalid_database_combination(self):
        output = io.StringIO()
        with patch("builtins.open", return_value=self._context("CREATE VIEW [dbo].[v] AS SELECT 1 AS [x]\n")), contextlib.redirect_stdout(output):
            code = offline_analyzer.main(["--file", "fixture.sql", "--inventory"])
        self.assertEqual(code, 0)
        self.assertIn("Modo: inventário offline", output.getvalue())
        self.assertIn("Views reconhecidas: 1", output.getvalue())

        stderr = io.StringIO()
        with patch("builtins.open", side_effect=AssertionError("não deve abrir")), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                offline_analyzer.main(["--file", "privado.sql", "--inventory", "--database", "SCADA"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(
            stderr.getvalue().splitlines()[-1],
            "Erro: --inventory e --database são incompatíveis.",
        )
        self.assertNotIn("privado.sql", stderr.getvalue())

    def test_discovery_exit_codes_distinguish_ambiguous_and_undetermined(self):
        for text, expected in (("CREATE DATABASE [A]\nGO\nCREATE DATABASE [B]\n", 3), ("SELECT 1\n", 4)):
            with self.subTest(expected=expected), patch("builtins.open", return_value=self._context(text)), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(offline_analyzer.main(["--file", "fixture.sql"]), expected)

    def test_cli_administrative_use_status_and_exit_codes(self):
        cases = (
            (
                "USE [master]\nGO\n",
                4,
                "Banco candidato: banco não determinado",
            ),
            (
                "USE [master]\nGO\nUSE [SCADA]\nGO\n",
                0,
                "Banco candidato: SCADA",
            ),
        )
        for text, expected_code, expected_output in cases:
            with self.subTest(expected_code=expected_code):
                output = io.StringIO()
                with (
                    patch("builtins.open", return_value=self._context(text)),
                    contextlib.redirect_stdout(output),
                ):
                    code = offline_analyzer.main(["--file", "fixture.sql"])
                self.assertEqual(code, expected_code)
                self.assertIn(expected_output, output.getvalue())

    def test_offline_modules_have_no_driver_or_execution_coupling(self):
        root = Path(__file__).resolve().parents[1]
        source = "\n".join(
            (root / "src" / name).read_text(encoding="utf-8")
            for name in ("offline_analyzer.py", "offline_inspection.py")
        )
        for forbidden in ("pyodbc", "cursor.execute", ".commit(", ".rollback(", "os.environ"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class InventoryProgressTests(unittest.TestCase):
    def complete_report(self, *, lines=0, batches=0, inserts=0):
        return offline_analyzer.InventoryReport(
            status=offline_analyzer.InventoryStatus.CONCLUIDO,
            cobertura=offline_analyzer.Coverage.COMPLETA,
            linhas_fisicas=lines,
            lotes=batches,
            comandos=inserts,
            comandos_reconhecidos=inserts,
            inserts=inserts,
            identity_insert=0,
            session_set=0,
            use=0,
            ddl_reconhecido=0,
            create_database=0,
            alter_database=0,
            views=0,
            outras_familias=0,
            lotes_nao_classificaveis=0,
            bancos_candidatos=(),
            processamento_completo=True,
            motivo_interrupcao=None,
        )

    def partial_report(self, *, interrupted=False):
        message = (
            "Análise interrompida pelo usuário."
            if interrupted else "Falha de leitura do arquivo."
        )
        status = (
            offline_analyzer.InventoryStatus.INTERROMPIDO
            if interrupted else offline_analyzer.InventoryStatus.PARCIAL
        )
        return offline_analyzer.InventoryReport(
            status=status,
            cobertura=offline_analyzer.Coverage.PARCIAL,
            linhas_fisicas=1_234,
            lotes=56,
            comandos=55,
            comandos_reconhecidos=55,
            inserts=55,
            identity_insert=0,
            session_set=0,
            use=0,
            ddl_reconhecido=0,
            create_database=0,
            alter_database=0,
            views=0,
            outras_familias=0,
            lotes_nao_classificaveis=1,
            bancos_candidatos=(),
            processamento_completo=False,
            motivo_interrupcao=message,
        )

    def snapshot(self, *, lines, batches, position, total, complete=False):
        return offline_analyzer.InventoryProgressSnapshot(
            linhas_fisicas=lines,
            lotes=batches,
            processamento_completo=complete,
            posicao_bytes_estimada=position,
            tamanho_bytes=total,
        )

    def test_reporter_starts_throttles_formats_and_never_emits_early_100(self):
        output = InteractiveOutput()
        clock = FakeClock()
        reporter = offline_analyzer.InventoryProgressReporter(
            output,
            clock=clock,
            interval_seconds=5.0,
        )
        reporter.start()
        private = "SEGREDO_SQL_NAO_EXIBIR"

        clock.current = 4.9
        reporter.update(self.snapshot(lines=999, batches=127, position=49, total=100))
        clock.current = 5.0
        reporter.update(self.snapshot(lines=1_000, batches=128, position=50, total=100))
        clock.current = 6.0
        reporter.update(self.snapshot(lines=2_000, batches=256, position=75, total=100))
        clock.current = 10.0
        reporter.update(self.snapshot(lines=3_000, batches=384, position=100, total=100))

        rendered_before_eof = output.getvalue()
        self.assertIn("Inventário offline iniciado.", rendered_before_eof)
        self.assertIn("50% estimado", rendered_before_eof)
        self.assertIn("linhas: 1.000", rendered_before_eof)
        self.assertIn("lotes: 128", rendered_before_eof)
        self.assertIn("decorrido: 00:00:05", rendered_before_eof)
        self.assertIn("99% estimado", rendered_before_eof)
        self.assertNotIn("100%", rendered_before_eof)
        self.assertNotIn(private, rendered_before_eof)
        self.assertEqual(rendered_before_eof.count("Progresso"), 2)

        clock.current = 10.1
        reporter.update(self.snapshot(
            lines=3_001,
            batches=385,
            position=100,
            total=100,
            complete=True,
        ))
        self.assertIn("100%", output.getvalue())
        self.assertEqual(output.getvalue().count("Progresso"), 3)

    def test_percentage_is_clamped_between_zero_and_one_hundred(self):
        output = InteractiveOutput()
        clock = FakeClock()
        reporter = offline_analyzer.InventoryProgressReporter(output, clock=clock)
        reporter.start()
        clock.current = 5.0
        reporter.update(self.snapshot(lines=1, batches=128, position=-10, total=100))
        clock.current = 10.0
        reporter.update(self.snapshot(lines=2, batches=256, position=10_000, total=100))
        rendered = output.getvalue()
        self.assertIn("0% estimado", rendered)
        self.assertIn("99% estimado", rendered)
        self.assertNotIn("100%", rendered)

    def test_inventory_progress_sampling_is_bounded_and_preserves_reports(self):
        snapshots = []
        with_progress = offline_analyzer.inventory_stream(
            GeneratedBatches(300),
            on_progress=snapshots.append,
        )
        without_progress = offline_analyzer.inventory_stream(GeneratedBatches(300))
        self.assertEqual(with_progress, without_progress)
        self.assertEqual(with_progress.lotes, 300)
        self.assertEqual(with_progress.inserts, 300)
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(
            [snapshot.lotes for snapshot in snapshots],
            [offline_analyzer.PROGRESS_BATCH_INTERVAL, 256, 300],
        )
        self.assertFalse(snapshots[0].processamento_completo)
        self.assertTrue(snapshots[-1].processamento_completo)
        self.assertLess(len(snapshots), 10)

    def test_empty_and_small_streams_only_publish_final_snapshot(self):
        for text, expected_lines, expected_batches in (
            ("", 0, 0),
            ("INSERT INTO [dbo].[T] VALUES (1)\n", 1, 1),
        ):
            with self.subTest(text=bool(text)):
                snapshots = []
                report = offline_analyzer.inventory_stream(
                    io.StringIO(text),
                    on_progress=snapshots.append,
                )
                self.assertTrue(report.processamento_completo)
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].linhas_fisicas, expected_lines)
                self.assertEqual(snapshots[0].lotes, expected_batches)
                self.assertTrue(snapshots[0].processamento_completo)

    def test_file_progress_uses_binary_position_as_an_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.sql"
            path.write_text("INSERT INTO [dbo].[T] VALUES (1)\n", encoding="utf-16")
            snapshots = []
            report = offline_analyzer.inventory_file(
                str(path),
                on_progress=snapshots.append,
            )
            expected_size = path.stat().st_size
        self.assertTrue(report.processamento_completo)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].tamanho_bytes, expected_size)
        self.assertEqual(snapshots[0].posicao_bytes_estimada, expected_size)
        source = (Path(__file__).resolve().parents[1] / "src" / "offline_inspection.py").read_text(encoding="utf-8")
        self.assertNotIn("stream.tell()", source)

    def test_cli_progress_is_interactive_stderr_only_and_redirection_is_stable(self):
        report = self.complete_report(lines=2, batches=1, inserts=1)

        def interactive_inventory(path, *, on_progress=None):
            self.assertIsNotNone(on_progress)
            on_progress(self.snapshot(
                lines=2,
                batches=1,
                position=200,
                total=200,
                complete=True,
            ))
            return report

        progress_output = InteractiveOutput()
        final_output = io.StringIO()
        with (
            patch.object(offline_analyzer, "inventory_file", side_effect=interactive_inventory),
            patch.object(offline_analyzer.sys, "stderr", progress_output),
            contextlib.redirect_stdout(final_output),
        ):
            code = offline_analyzer.main(["--file", "CAMINHO_PRIVADO.sql", "--inventory"])
        self.assertEqual(code, 0)
        self.assertIn("Inventário offline iniciado.", progress_output.getvalue())
        self.assertIn("100%", progress_output.getvalue())
        self.assertNotIn("CAMINHO_PRIVADO", progress_output.getvalue())
        self.assertIn("Modo: inventário offline", final_output.getvalue())
        self.assertNotIn("Inventário offline iniciado", final_output.getvalue())

        def redirected_inventory(path, *, on_progress=None):
            self.assertIsNone(on_progress)
            return report

        redirected_error = io.StringIO()
        with (
            patch.object(offline_analyzer, "inventory_file", side_effect=redirected_inventory),
            patch.object(offline_analyzer.sys, "stderr", redirected_error),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            redirected_code = offline_analyzer.main(
                ["--file", "CAMINHO_PRIVADO.sql", "--inventory"],
            )
        self.assertEqual(redirected_code, 0)
        self.assertEqual(redirected_error.getvalue(), "")

    def test_interrupt_and_read_failure_keep_codes_and_final_partial_report(self):
        cases = ((self.partial_report(interrupted=True), 130), (self.partial_report(), 3))
        for report, expected_code in cases:
            with self.subTest(status=report.status):
                progress_output = InteractiveOutput()
                final_output = io.StringIO()
                with (
                    patch.object(offline_analyzer, "inventory_file", return_value=report),
                    patch.object(offline_analyzer.sys, "stderr", progress_output),
                    contextlib.redirect_stdout(final_output),
                ):
                    code = offline_analyzer.main(["--file", "privado.sql", "--inventory"])
                self.assertEqual(code, expected_code)
                self.assertIn("Inventário offline iniciado.", progress_output.getvalue())
                self.assertNotIn("100%", progress_output.getvalue())
                self.assertIn("1.234", progress_output.getvalue())
                self.assertIn("Status:", final_output.getvalue())
                self.assertIn("Processamento completo: não", final_output.getvalue())
                self.assertNotIn("privado.sql", progress_output.getvalue())


if __name__ == "__main__":
    unittest.main()
