"""R5B-3B-1: envelope estrito de views, sempre recusado por padrão."""

import gc
import io
import tracemalloc
import unittest

from sql_classifier import (
    MAX_BATCH_CHARS,
    MAX_LINE_CHARS,
    MAX_STATEMENT_CHARS,
    SQLClassificationError,
    SQLCommandReader,
)
from tests._application_harness import PRIVATE, Scenario


DESTINATION = "DestinoViewSintetico"
EXTERNAL = "ExternoViewSintetico"
RECOGNIZED = "Definição de view reconhecida, mas exige equivalência externa comprovada."
INVALID = "Definição de view fora do contrato sintático restrito."
WRONG_CONTEXT = "Definição de view fora do contexto de banco de destino autorizado."


def view(body="SELECT [v] FROM [dbo].[OrigemSintetica]", target="[dbo].[ViewSintetica]"):
    return f"CREATE VIEW {target}\nAS\n{body}"


class ViewRecognitionTests(unittest.TestCase):
    def reader_error(self, text, *, target=DESTINATION):
        reader = SQLCommandReader(io.StringIO(text), target_database=target)
        with self.assertRaises(SQLClassificationError) as caught:
            list(reader)
        return reader, caught.exception

    def assert_reason(self, text, reason=RECOGNIZED, *, target=DESTINATION):
        reader, error = self.reader_error(text, target=target)
        self.assertEqual(error.reason, reason)
        self.assertEqual(reader.context_state, "invalid")
        return reader

    def test_simple_select_is_specifically_recognized_but_refused(self):
        reader = self.assert_reason(view())
        self.assertEqual(reader.ignored_lines, 0)

    def test_cte_join_group_union_subqueries_and_many_selects(self):
        bodies = (
            "WITH [c] AS (SELECT [v] FROM [dbo].[t]) SELECT [v] FROM [c]",
            "SELECT [a].[v], COUNT(*) FROM [dbo].[a] AS [a] JOIN [dbo].[b] AS [b] ON [a].[id]=[b].[id] GROUP BY [a].[v]",
            "SELECT [v] FROM [dbo].[a] UNION SELECT [v] FROM [dbo].[b]",
            "SELECT (SELECT (SELECT [v] FROM [dbo].[c]) FROM [dbo].[b]) FROM [dbo].[a]",
            "WITH [a] AS (SELECT [v] FROM [dbo].[t]), [b] AS (SELECT [v] FROM [a]) SELECT [v] FROM [b] UNION ALL SELECT [v] FROM [a]",
        )
        for body in bodies:
            with self.subTest(body=body[:30]):
                self.assert_reason(view(body))

    def test_body_references_with_one_two_and_three_parts(self):
        for reference in ("[obj]", "[dbo].[obj]", "[OutroBanco].[dbo].[obj]"):
            with self.subTest(parts=reference.count(".") + 1):
                self.assert_reason(view("SELECT [v] FROM " + reference))

    def test_delimited_targets_escapes_case_whitespace_and_crlf(self):
        text = "cReAtE /* envelope */ ViEw\r\n[sch]]ema] . [vi]]ew]\r\n-- divisão\r\nAs\r\nsElEcT [v] FROM [dbo].[t]"
        self.assert_reason(text)

    def test_comments_strings_identifiers_and_go_are_lexical(self):
        bodies = (
            "SELECT N'linha\nGO\nfim; INSERT' AS [texto] FROM [dbo].[t]",
            "SELECT [linha\nGO\nfim] FROM [dbo].[t]",
            "SELECT [v] /* início\nGO\nINSERT\nfim */ FROM [dbo].[t]",
            "SELECT [INSERT], N'EXEC; CREATE VIEW' FROM [dbo].[t] -- GO",
        )
        for body in bodies:
            with self.subTest(body=body[:25]):
                self.assert_reason(view(body))

    def test_optional_terminal_semicolon_at_go_or_eof(self):
        self.assert_reason(view() + ";")
        reader = self.assert_reason(view() + ";\nGO\n")
        self.assertEqual(reader.ignored_lines, 1)
        self.assert_reason(view("SELECT N';' AS [texto] FROM [dbo].[t]"))

    def test_missing_as_or_body_is_out_of_contract(self):
        for text in ("CREATE VIEW [dbo].[v] SELECT 1", "CREATE VIEW [dbo].[v] AS", "CREATE VIEW [dbo].[v]"):
            with self.subTest(text=text):
                self.assert_reason(text, INVALID)

    def test_unbalanced_or_unterminated_lexical_structures_are_refused(self):
        for text in (
            view("SELECT ([v] FROM [dbo].[t]"),
            view("SELECT [v]) FROM [dbo].[t]"),
            view("SELECT N'aberto"),
            view("SELECT [aberto"),
            view("SELECT 1 /* aberto"),
        ):
            with self.subTest(text=text[-30:]):
                self.assert_reason(text, INVALID)

    def test_unobserved_view_verbs_headers_and_target_shapes_are_refused(self):
        statements = (
            "ALTER VIEW [dbo].[v] AS SELECT 1",
            "CREATE OR ALTER VIEW [dbo].[v] AS SELECT 1",
            "CREATE VIEW [dbo].[v] ([a]) AS SELECT 1",
            "CREATE VIEW [dbo].[v] WITH SCHEMABINDING AS SELECT 1",
            "CREATE VIEW [dbo].[v] WITH ENCRYPTION AS SELECT 1",
            "CREATE VIEW [dbo].[v] WITH VIEW_METADATA AS SELECT 1",
            "CREATE VIEW dbo.[v] AS SELECT 1",
            "CREATE VIEW [dbo].v AS SELECT 1",
            "CREATE VIEW [v] AS SELECT 1",
            "CREATE VIEW [db].[dbo].[v] AS SELECT 1",
        )
        for statement in statements:
            with self.subTest(statement=statement[:40]):
                self.assert_reason(statement, INVALID)

    def test_body_must_start_with_select_or_cte_with(self):
        for body in ("VALUES (1)", "FROM [dbo].[t]", "EXEC [dbo].[p]", "BEGIN SELECT 1 END"):
            with self.subTest(body=body):
                self.assert_reason(view(body), INVALID)

    def test_data_control_ddl_and_dynamic_sql_cannot_be_absorbed(self):
        bodies = (
            "INSERT INTO [dbo].[t] VALUES (1)",
            "SELECT 1; INSERT INTO [dbo].[t] VALUES (1)",
            "SELECT 1; SET ANSI_NULLS ON",
            f"SELECT 1; USE [{DESTINATION}]",
            "SELECT 1; CREATE TABLE [t] ([v] int)",
            "SELECT 1; ALTER TABLE [t] ADD [v] int",
            "SELECT 1; DROP TABLE [t]",
            "SELECT 1; EXEC(N'SELECT 1')",
            "SELECT 1 EXEC [dbo].[p]",
            "SELECT [v] INTO [dbo].[copia] FROM [dbo].[t]",
            "SELECT 1 GO SELECT 2",
            "SELECT 1 SELECT 2",
        )
        for body in bodies:
            with self.subTest(body=body[:35]):
                self.assert_reason(view(body), INVALID)

    def test_nonterminal_or_repeated_semicolon_is_refused(self):
        for body in ("SELECT 1; SELECT 2", "SELECT 1;;", "SELECT (N';');;", "SELECT (1; 2)"):
            with self.subTest(body=body):
                self.assert_reason(view(body), INVALID)

    def test_context_is_checked_without_changing_use_behavior(self):
        self.assert_reason(f"USE [{EXTERNAL}]\nGO\n" + view(), WRONG_CONTEXT)
        self.assert_reason(f"USE [{DESTINATION}]\nGO\n" + view())

    def test_session_requests_are_preserved_and_no_set_is_synthesized(self):
        text = "SET ANSI_NULLS ON\nGO\nSET QUOTED_IDENTIFIER ON\nGO\n" + view()
        reader, error = self.reader_error(text)
        self.assertEqual(error.reason, RECOGNIZED)
        self.assertEqual(reader.session_requests, {
            "ANSI_NULLS": "ON", "QUOTED_IDENTIFIER": "ON", "ANSI_PADDING": None,
        })

    def test_refusal_never_executes_commits_rolls_back_or_ignores_view(self):
        scenario = Scenario(text=view(), target_database=DESTINATION)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(scenario.cursor.statements, [])
        self.assertEqual(scenario.confirmed, [])
        self.assertEqual(scenario.connection.commit_attempts, 0)
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertNotIn("execute", scenario.events)
        self.assertIn(RECOGNIZED, result.output)
        self.assertEqual(result.namespace["leitor_sql"].ignored_lines, 0)

    def test_diagnostic_is_fixed_and_private_free(self):
        secret = PRIVATE + "_VIEW"
        scenario = Scenario(text=view(f"SELECT N'{secret}' AS [segredo]", f"[dbo].[{secret}]") , target_database=DESTINATION)
        result = scenario.run()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(RECOGNIZED, result.output)
        self.assertNotIn(secret, result.output)

    def test_failure_does_not_read_a_future_batch(self):
        class GuardedStream:
            def __init__(self):
                self.lines = iter((view() + "\n", "GO\n", "INSERT INTO [dbo].[t] VALUES (1)\n", "GO\n"))
                self.calls = 0

            def readline(self, size):
                self.calls += 1
                if self.calls > 2:
                    raise AssertionError("Lote futuro lido antecipadamente.")
                if not 0 < size <= MAX_LINE_CHARS + 1:
                    raise AssertionError("Leitura sem limite explícito.")
                return next(self.lines, "")

        stream = GuardedStream()
        with self.assertRaisesRegex(SQLClassificationError, RECOGNIZED):
            list(SQLCommandReader(stream, target_database=DESTINATION))
        self.assertEqual(stream.calls, 2)

    def test_physical_and_statement_limits_remain_enforced(self):
        cases = (
            (view("SELECT N'" + "x" * MAX_LINE_CHARS + "'"), "Limite de tamanho da linha excedido."),
            (view("SELECT 1\n" + ("/*" + "x" * 60_000 + "*/\n") * (MAX_BATCH_CHARS // 60_000 + 1)), "Limite de tamanho do lote excedido."),
            (view("SELECT N'" + ("x" * 8192 + "\n") * (MAX_STATEMENT_CHARS // 8192 + 1) + "'"), "Limite de tamanho do comando excedido."),
        )
        for text, reason in cases:
            with self.subTest(reason=reason):
                _, error = self.reader_error(text)
                self.assertEqual(error.reason, reason)

    def test_repeated_refusal_has_bounded_python_memory(self):
        statement = view("SELECT (SELECT [v] FROM [dbo].[t])")
        gc.collect()
        tracemalloc.start()
        try:
            for _ in range(300):
                with self.assertRaises(SQLClassificationError):
                    list(SQLCommandReader(io.StringIO(statement), target_database=DESTINATION))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_users_roles_and_fulltext_remain_outside_this_contract(self):
        for statement in (
            "CREATE USER [u] FOR LOGIN [u] WITH DEFAULT_SCHEMA=[dbo]",
            "ALTER ROLE [r] ADD MEMBER [u]",
            "IF FULLTEXTSERVICEPROPERTY('IsFullTextInstalled')=1 BEGIN EXEC [db].[sys].[sp_fulltext_database] @action='enable' END",
        ):
            with self.subTest(statement=statement[:25]):
                _, error = self.reader_error(statement)
                self.assertNotEqual(error.reason, RECOGNIZED)


if __name__ == "__main__":
    unittest.main()
