"""R5B-2B: contrato fechado para administração de banco, sem autorização."""

import gc
import io
import tracemalloc
import unittest

from sql_classifier import (
    MAX_BATCH_CHARS,
    MAX_COMMENT_DEPTH,
    MAX_LINE_CHARS,
    SQLClassificationError,
    SQLCommandReader,
)
from tests._application_harness import PRIVATE, Scenario


DESTINATION = "DestinoAdminSintetico"
EXTERNAL = "ExternoAdminSintetico"
RECOGNIZED = "Instrução administrativa de banco reconhecida, mas não autorizada para descarte."
INVALID = "Instrução administrativa de banco fora do contrato sintático restrito."
WRONG_TARGET = "Alvo administrativo de banco diferente do destino configurado."
INSERT = f"INSERT INTO [dbo].[DadosAdmin] VALUES (N'{PRIVATE}')"


def create_database(target=DESTINATION):
    return f"""CREATE DATABASE [{target}]
CONTAINMENT = NONE
ON PRIMARY
( NAME = N'NomeDadosPrivado', FILENAME = N'X:\\privado\\dados.mdf', SIZE = 8192 KB, MAXSIZE = UNLIMITED, FILEGROWTH = 65536 KB )
LOG ON
( NAME = N'NomeLogPrivado', FILENAME = N'X:\\privado\\log.ldf', SIZE = 8192 KB, MAXSIZE = 2048 GB, FILEGROWTH = 65536 KB )
WITH CATALOG_COLLATION = DATABASE_DEFAULT"""


ALTER_CLAUSES = (
    "COMPATIBILITY_LEVEL = 150",
    "ANSI_NULL_DEFAULT OFF",
    "ANSI_NULLS OFF",
    "ANSI_PADDING OFF",
    "ANSI_WARNINGS OFF",
    "ARITHABORT OFF",
    "CONCAT_NULL_YIELDS_NULL OFF",
    "NUMERIC_ROUNDABORT OFF",
    "QUOTED_IDENTIFIER OFF",
    "CURSOR_CLOSE_ON_COMMIT OFF",
    "CURSOR_DEFAULT GLOBAL",
    "RECURSIVE_TRIGGERS OFF",
    "AUTO_CLOSE OFF",
    "AUTO_SHRINK OFF",
    "AUTO_UPDATE_STATISTICS ON",
    "AUTO_UPDATE_STATISTICS_ASYNC OFF",
    "DATE_CORRELATION_OPTIMIZATION OFF",
    "PARAMETERIZATION SIMPLE",
    "QUERY_STORE = OFF",
    "ALLOW_SNAPSHOT_ISOLATION OFF",
    "READ_COMMITTED_SNAPSHOT OFF",
    "DISABLE_BROKER",
    "HONOR_BROKER_PRIORITY OFF",
    "TRUSTWORTHY OFF",
    "DB_CHAINING OFF",
    "RECOVERY FULL",
    "PAGE_VERIFY CHECKSUM",
    "TARGET_RECOVERY_TIME = 60 SECONDS",
    "DELAYED_DURABILITY = DISABLED",
    "ACCELERATED_DATABASE_RECOVERY = OFF",
    "MULTI_USER",
    "FILESTREAM ( NON_TRANSACTED_ACCESS = OFF )",
    "READ_WRITE",
)


def alter_database(clause, target=DESTINATION):
    return f"ALTER DATABASE [{target}] SET {clause}"


class DatabaseAdministrationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(len(ALTER_CLAUSES), 33)

    def reader_error(self, text, *, target=DESTINATION):
        reader = SQLCommandReader(io.StringIO(text), target_database=target)
        with self.assertRaises(SQLClassificationError) as caught:
            list(reader)
        return reader, caught.exception

    def application_error(self, text, *, confirmed=()):
        scenario = Scenario(text=text, target_database=DESTINATION)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(scenario.cursor.statements, list(confirmed))
        self.assertEqual(scenario.confirmed, list(confirmed))
        self.assertEqual(scenario.connection.commit_attempts, len(confirmed))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        for private in (PRIVATE, DESTINATION, EXTERNAL, "NomeDadosPrivado", "NomeLogPrivado", "X:\\privado", "DadosAdmin"):
            self.assertNotIn(private, result.output)
        return scenario, result

    def assert_reason(self, text, reason, *, target=DESTINATION):
        reader, error = self.reader_error(text, target=target)
        self.assertEqual(error.reason, reason)
        self.assertEqual(reader.context_state, "invalid")
        self.assertEqual(reader.session_requests, dict.fromkeys(("ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_PADDING")))

    def test_all_34_observed_signatures_are_recognized_but_refused(self):
        statements = (create_database(), *(alter_database(clause) for clause in ALTER_CLAUSES))
        self.assertEqual(len(statements), 34)
        for number, statement in enumerate(statements, 1):
            with self.subTest(signature=number):
                self.assert_reason(statement, RECOGNIZED)

    def test_recognized_and_out_of_contract_are_distinguishable(self):
        self.assert_reason(alter_database("READ_WRITE"), RECOGNIZED)
        self.assert_reason(alter_database("READ_ONLY"), INVALID)

    def test_default_refusal_never_emits_or_confirms_administrative_sql(self):
        for statement in (create_database(), alter_database("READ_WRITE")):
            with self.subTest(kind=statement.split()[0:2]):
                scenario, result = self.application_error(statement)
                self.assertNotIn("execute", scenario.events)
                self.assertNotIn("commit", scenario.events)
                self.assertNotIn("rollback", scenario.events)
                self.assertIn(RECOGNIZED, result.output)

    def test_matching_target_is_recognized_and_divergent_target_is_distinct(self):
        self.assert_reason(alter_database("MULTI_USER"), RECOGNIZED)
        self.assert_reason(alter_database("MULTI_USER", EXTERNAL), WRONG_TARGET)
        self.assert_reason(create_database(EXTERNAL), WRONG_TARGET)

    def test_target_comparison_is_unicode_exact(self):
        for target in (DESTINATION.lower(), DESTINATION + " ", "De\u0301stinoAdminSintetico"):
            with self.subTest(target=target):
                self.assert_reason(alter_database("READ_WRITE", target), WRONG_TARGET)

    def test_external_use_allows_recognition_without_authorizing_or_changing_requests(self):
        text = f"USE [{EXTERNAL}]\nGO\n" + alter_database("READ_WRITE")
        reader, error = self.reader_error(text)
        self.assertEqual(error.reason, RECOGNIZED)
        self.assertEqual(reader.context_state, "invalid")
        self.assertEqual(reader.session_requests, dict.fromkeys(("ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_PADDING")))

    def test_case_comments_whitespace_and_one_optional_semicolon(self):
        statement = """alter /* a */ database /* b */ [DestinoAdminSintetico]
set /* c */ target_recovery_time /* d */ = /* e */ 60 /* f */ seconds /* fim */ ;"""
        self.assert_reason(statement, RECOGNIZED)
        lower_create = create_database().lower().replace("[destinoadminsintetico]", f"[{DESTINATION}]")
        self.assert_reason(lower_create + ";", RECOGNIZED)

    def test_go_inside_string_comment_and_identifier_is_not_a_separator(self):
        string_go = create_database().replace("X:\\privado\\dados.mdf", "X:\\privado\\linha\nGO\ndados.mdf")
        self.assert_reason(string_go, RECOGNIZED)
        comment_go = alter_database("/* início\nGO\nfim */ READ_WRITE")
        self.assert_reason(comment_go, RECOGNIZED)
        identifier_go = create_database("Destino\nGO\nAdminSintetico")
        self.assert_reason(identifier_go, WRONG_TARGET)

    def test_opaque_names_and_paths_are_never_diagnosed(self):
        statement = create_database().replace("NomeDadosPrivado", PRIVATE).replace("X:\\privado\\dados.mdf", "Z:\\" + PRIVATE + ".mdf")
        _, result = self.application_error(statement)
        self.assertIn(RECOGNIZED, result.output)
        self.assertNotIn(PRIVATE, result.output)

    def test_create_requires_exact_observed_storage_shape(self):
        valid = create_database()
        mutations = (
            valid.replace("CONTAINMENT = NONE", "CONTAINMENT = PARTIAL"),
            valid.replace("ON PRIMARY", "ON [PRIMARY]"),
            valid.replace("MAXSIZE = UNLIMITED", "MAXSIZE = 4096 MB"),
            valid.replace("SIZE = 8192 KB", "SIZE = 8 MB", 1),
            valid.replace("MAXSIZE = 2048 GB", "MAXSIZE = UNLIMITED"),
            valid.replace("FILEGROWTH = 65536 KB", "FILEGROWTH = 64 MB", 1),
            valid.replace("WITH CATALOG_COLLATION = DATABASE_DEFAULT", "COLLATE Latin1_General_CI_AS"),
            valid + " ON [GrupoExtra]",
            valid.replace("LOG ON", "ON [GrupoExtra] (NAME=N'x') LOG ON"),
            valid.replace("FILENAME = N'X:\\privado\\dados.mdf', ", ""),
        )
        for statement in mutations:
            with self.subTest(mutation=statement[-40:]):
                self.assert_reason(statement, INVALID)

    def test_create_numeric_forms_are_ascii_positive_integers_with_exact_units(self):
        valid = create_database()
        for replacement in ("0 KB", "-1 KB", "+1 KB", "1.5 KB", "1e3 KB", "0x20 KB", "１２ KB", "1 TB"):
            with self.subTest(replacement=replacement):
                self.assert_reason(valid.replace("8192 KB", replacement, 1), INVALID)

    def test_alter_rejects_other_values_states_and_expressions(self):
        clauses = (
            "COMPATIBILITY_LEVEL = 160", "ANSI_NULLS ON", "AUTO_UPDATE_STATISTICS OFF",
            "CURSOR_DEFAULT LOCAL", "RECOVERY SIMPLE", "PAGE_VERIFY NONE",
            "TARGET_RECOVERY_TIME = 30 SECONDS", "DELAYED_DURABILITY = ALLOWED",
            "FILESTREAM ( NON_TRANSACTED_ACCESS = FULL )", "QUERY_STORE = ON",
            "ANSI_PADDING = OFF", "READ_WRITE + 1", "READ_COMMITTED_SNAPSHOT @estado",
        )
        for clause in clauses:
            with self.subTest(clause=clause):
                self.assert_reason(alter_database(clause), INVALID)

    def test_alter_rejects_current_unknown_repeated_and_suffixes(self):
        statements = (
            "ALTER DATABASE CURRENT SET READ_WRITE",
            alter_database("READ_ONLY"),
            alter_database("READ_WRITE, MULTI_USER"),
            alter_database("READ_WRITE READ_WRITE"),
            alter_database("READ_WRITE WITH ROLLBACK IMMEDIATE"),
            alter_database("READ_WRITE; READ_WRITE"),
            alter_database("MULTI_USER OPTION"),
            alter_database(""),
        )
        for statement in statements:
            with self.subTest(statement=statement[-50:]):
                self.assert_reason(statement, INVALID)

    def test_only_one_optional_semicolon_is_accepted(self):
        self.assert_reason(alter_database("READ_WRITE") + ";", RECOGNIZED)
        for suffix in (";;", "; ;", "; -- fim\n;", "; SELECT 1"):
            with self.subTest(suffix=suffix):
                self.assert_reason(alter_database("READ_WRITE") + suffix, INVALID)

    def test_administration_followed_by_other_statement_invalidates_whole_batch(self):
        extras = (INSERT, "SET ANSI_NULLS ON", f"USE [{DESTINATION}]", "CREATE TABLE [t] ([v] INT)", alter_database("MULTI_USER"))
        for extra in extras:
            with self.subTest(extra=extra.split()[0]):
                self.assert_reason(alter_database("READ_WRITE") + ";\n" + extra, INVALID)

    def test_insert_before_administration_is_refused_before_any_emission(self):
        scenario, result = self.application_error(INSERT + ";\n" + alter_database("READ_WRITE"))
        self.assertEqual(scenario.cursor.statements, [])
        self.assertNotIn("execute", scenario.events)
        self.assertIn("SQLClassificationError", result.output)

    def test_incomplete_and_unexpected_tokens_are_out_of_contract(self):
        statements = (
            "CREATE DATABASE", f"CREATE DATABASE [{DESTINATION}]", "ALTER DATABASE",
            f"ALTER DATABASE [{DESTINATION}]", f"ALTER DATABASE [{DESTINATION}] SET",
            f"ALTER DATABASE [{DESTINATION}] SET FILESTREAM (",
            f"ALTER DATABASE [{DESTINATION}] SET FILESTREAM (( NON_TRANSACTED_ACCESS = OFF ))",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assert_reason(statement, INVALID)

    def test_limits_remain_enforced(self):
        cases = (
            (" " * (MAX_LINE_CHARS + 1) + alter_database("READ_WRITE"), "Limite de tamanho da linha excedido."),
            ("/*\n" + ("x" * 60_000 + "\n") * 18 + "*/", "Limite de tamanho do lote excedido."),
            ("/*" * (MAX_COMMENT_DEPTH + 1) + "x" + "*/" * (MAX_COMMENT_DEPTH + 1), "Limite de comentários aninhados excedido."),
        )
        for text, reason in cases:
            with self.subTest(reason=reason):
                _, error = self.reader_error(text)
                self.assertEqual(error.reason, reason)

    def test_administrative_failure_does_not_read_a_future_batch(self):
        class GuardedStream:
            def __init__(self):
                self.lines = iter((f"USE [{EXTERNAL}]\n", "GO\n", alter_database("READ_WRITE") + "\n", "GO\n", INSERT + "\n"))
                self.calls = 0

            def readline(self, size):
                self.calls += 1
                if self.calls > 4:
                    raise AssertionError("Lote futuro lido antecipadamente.")
                self.assert_size(size)
                return next(self.lines, "")

            @staticmethod
            def assert_size(size):
                if not 0 < size <= MAX_LINE_CHARS + 1:
                    raise AssertionError("Leitura sem limite explícito.")

        stream = GuardedStream()
        reader = SQLCommandReader(stream, target_database=DESTINATION)
        with self.assertRaisesRegex(SQLClassificationError, RECOGNIZED):
            list(reader)
        self.assertEqual(stream.calls, 4)

    def test_previous_insert_commit_is_preserved_before_administrative_failure(self):
        text = INSERT + "\nGO\n" + alter_database("READ_WRITE")
        scenario, result = self.application_error(text, confirmed=(INSERT,))
        self.assertEqual(scenario.cursor.statements, [INSERT])
        self.assertIn("Importação parcialmente concluída", result.output)
        self.assertIn("Comandos confirmados: 1", result.output)

    def test_failure_does_not_publish_session_or_execution_state(self):
        reader = SQLCommandReader(io.StringIO(alter_database("READ_WRITE")), target_database=DESTINATION)
        before = reader.session_requests
        with self.assertRaises(SQLClassificationError):
            next(iter(reader))
        self.assertEqual(reader.session_requests, before)
        self.assertEqual(reader.ignored_lines, 0)
        self.assertEqual(reader.context_state, "invalid")

    def test_repeated_recognition_has_bounded_python_memory(self):
        statement = alter_database("READ_WRITE")
        gc.collect()
        tracemalloc.start()
        try:
            for _ in range(500):
                with self.assertRaises(SQLClassificationError):
                    list(SQLCommandReader(io.StringIO(statement), target_database=DESTINATION))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
