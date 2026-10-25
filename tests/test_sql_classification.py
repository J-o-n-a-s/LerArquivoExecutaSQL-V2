"""Contrato da Fase 2B exercitado contra o programa real, sem banco."""

import codecs
import unittest

from tests._application_harness import PRIVATE, STATEMENT, Scenario


INSERT = "INSERT INTO [dbo].[Sintetico] ([id]) VALUES (1)"


class SQLClassificationTests(unittest.TestCase):
    def assert_commands(self, text, expected, *, payload=None, target_database=None):
        scenario = Scenario(text=text, payload=payload, target_database=target_database)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(scenario.cursor.statements, expected)
        self.assertEqual(scenario.connection.commit_attempts, len(expected))
        self.assertEqual(scenario.connect.call_count, 1)
        self.assertEqual((scenario.cursor.close_attempts, scenario.connection.close_attempts, scenario.file.close_attempts), (1, 1, 1))
        self.assertNotIn(PRIVATE, result.output)
        return scenario, result

    def assert_refused(self, text, *, confirmed=()):
        scenario = Scenario(text=text)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.cursor.statements, list(confirmed))
        self.assertEqual(scenario.connection.commit_attempts, len(confirmed))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertEqual((scenario.cursor.close_attempts, scenario.connection.close_attempts, scenario.file.close_attempts), (1, 1, 1))
        self.assertIn("SQLClassificationError", result.output)
        self.assertNotIn("Processo finalizado", result.output)
        self.assertNotIn(PRIVATE, result.output)
        return result

    def test_insert_with_into(self):
        self.assert_commands(INSERT, [INSERT])

    def test_insert_without_into(self):
        text = INSERT.replace("INSERT INTO", "INSERT")
        self.assert_commands(text, [text])

    def test_optional_semicolon(self):
        self.assert_commands(INSERT + ";", [INSERT + ";"])

    def test_lowercase_keywords(self):
        text = INSERT.lower()
        self.assert_commands(text, [text])

    def test_mixed_case_keywords(self):
        text = INSERT.replace("INSERT INTO", "iNsErT iNtO").replace("VALUES", "vAlUeS")
        self.assert_commands(text, [text])

    def test_insertion_is_not_insert(self):
        self.assert_refused("INSERTION [dbo].[Sintetico] VALUES (1)")

    def test_set_id_inexistente_is_not_identity_insert(self):
        self.assert_refused("SET ID_INEXISTENTE")

    def test_keyword_boundaries_in_values(self):
        self.assert_refused("INSERT INTO [dbo].[Sintetico] VALUESx (1)")

    def test_identity_state_requires_exact_word(self):
        self.assert_refused("SET IDENTITY_INSERT [dbo].[Sintetico] ONWARD")

    def test_multiline_insert(self):
        text = "INSERT INTO [dbo].[Sintetico]\n([id], [nome])\nVALUES (1, N'ação')"
        self.assert_commands(text, [text])

    def test_incomplete_multiline_header_is_never_sent(self):
        self.assert_refused("INSERT INTO [dbo].[Sintetico] ([id])\n")

    def test_incomplete_values_at_eof_is_never_sent(self):
        self.assert_refused("INSERT INTO [dbo].[Sintetico]\nVALUES (1,\n")

    def test_valid_insert_before_incomplete_command_in_same_batch_is_not_sent(self):
        self.assert_refused(INSERT + ";\nINSERT INTO [dbo].[Sintetico] VALUES (")

    def test_line_comment_is_ignored(self):
        self.assert_commands("-- " + INSERT + "\n", [])

    def test_block_comment_does_not_emit_inner_insert(self):
        self.assert_commands("/*\n" + INSERT + "\n*/\n", [])

    def test_nested_block_comments(self):
        self.assert_commands("/* externo\n/* interno */\n" + INSERT + "\n*/\n" + INSERT, [INSERT])

    def test_internal_comments_are_preserved(self):
        text = "INSERT /* sintético */ INTO [dbo].[Sintetico] VALUES (-- valor\n1)"
        self.assert_commands(text, [text])

    def test_trailing_line_comment_is_not_sent(self):
        self.assert_commands(INSERT + " -- comentário\n", [INSERT])

    def test_insert_and_semicolon_in_literal_are_not_commands(self):
        text = "INSERT INTO [dbo].[Sintetico] VALUES (N'INSERT INTO t VALUES (1); GO')"
        self.assert_commands(text, [text])

    def test_insert_inside_select_string_is_not_executed(self):
        self.assert_refused("SELECT N'" + INSERT + "'")

    def test_go_inside_multiline_literal_is_not_separator(self):
        text = "INSERT INTO [dbo].[Sintetico] VALUES (N'linha\nGO\nINSERT\nfim')"
        self.assert_commands(text + "\nGO\n", [text])

    def test_go_in_block_comment_is_not_separator(self):
        self.assert_commands("/*\nGO\n" + INSERT + "\n*/\n" + INSERT, [INSERT])

    def test_go_isolated_case_and_comments(self):
        self.assert_commands(INSERT + "\n go -- lote\n/* separador */ Go /* fim */\n" + INSERT, [INSERT, INSERT])

    def test_go_with_repetition_is_refused(self):
        self.assert_refused(INSERT + "\nGO 2\n")

    def test_go_with_semicolon_is_refused(self):
        self.assert_refused(INSERT + "\nGO;\n")

    def test_identity_insert_survives_go_in_same_connection(self):
        on = "set identity_insert [dbo].[Sintetico] on;"
        off = "SeT IdEnTiTy_InSeRt [dbo].[Sintetico] OfF"
        self.assert_commands(on + "\nGO\n" + INSERT + "\nGO\n" + off, [on, INSERT, off])

    def test_escaped_single_quotes(self):
        text = "INSERT INTO [dbo].[Sintetico] VALUES (N'ação ''INSERT; GO''')"
        self.assert_commands(text, [text])

    def test_bracket_identifiers_and_escaped_closing_bracket(self):
        text = "INSERT INTO [banco].[esquema].[GO]]INSERT] ([a]]b]) VALUES (1)"
        self.assert_commands(text, [text], target_database="banco")

    def test_double_quoted_identifiers(self):
        text = 'INSERT INTO "dbo"."GO;INSERT" ("a""b") VALUES (1)'
        self.assert_commands(text, [text])

    def test_unicode_identifiers_and_literal_mojibake_are_preserved(self):
        text = "INSERT INTO [ação漢字] ([nome]) VALUES (N'ÿþ ação 漢字')"
        self.assert_commands(text, [text])

    def test_utf16_little_endian_bom(self):
        text = "INSERT INTO [ação] VALUES (N'ÿþ漢字');"
        self.assert_commands("", [text], payload=codecs.BOM_UTF16_LE + text.encode("utf-16-le"))

    def test_utf16_big_endian_bom(self):
        text = "INSERT INTO [ação] VALUES (N'ÿþ漢字');"
        self.assert_commands("", [text], payload=codecs.BOM_UTF16_BE + text.encode("utf-16-be"))

    def test_empty_input(self):
        self.assert_commands("", [])

    def test_whitespace_and_comments_only(self):
        self.assert_commands(" \n-- nada\n/* /* interno */ fim */\n", [])

    def test_multiple_inserts_keep_order_without_duplication(self):
        commands = [f"INSERT INTO [t] VALUES ({number});" for number in range(5)]
        self.assert_commands(" ".join(commands), commands)

    def test_multiple_rows_and_supported_scalar_literals(self):
        text = "INSERT INTO [t] VALUES (-1, +2.5e-2, 0xAF, NULL, DEFAULT, N'x'), (3, 4, 0x00, null, default, 'y');"
        self.assert_commands(text, [text])

    def test_values_count_must_match_columns(self):
        self.assert_refused("INSERT INTO [t] ([a], [b]) VALUES (1)")

    def test_unknown_statement_is_not_silently_discarded(self):
        self.assert_refused("UPDATE [t] SET [a] = 1")

    def test_session_set_is_not_silently_discarded(self):
        # R3 autoriza este ON explicitamente; exigir seu encaminhamento, sem
        # descarte silencioso, antes do INSERT que depende da sessão.
        text = "SET QUOTED_IDENTIFIER ON"
        self.assert_commands(text + "\n" + INSERT, [text, INSERT])

    def test_insert_select_is_not_supported(self):
        self.assert_refused("INSERT INTO [t] SELECT 1")

    def test_expression_is_not_supported(self):
        self.assert_refused("INSERT INTO [t] VALUES (GETDATE())")

    def test_create_procedure_body_is_ignored_until_go(self):
        routine = "CREATE PROCEDURE [dbo].[p] AS\nBEGIN\n" + INSERT + "\nEND\nGO\n"
        self.assert_commands(routine + INSERT, [INSERT])

    def test_alter_procedure_body_is_ignored(self):
        self.assert_commands("ALTER PROC [dbo].[p] AS\n" + INSERT + "\nGO\n", [])

    def test_create_trigger_body_is_never_imported(self):
        self.assert_commands("CREATE TRIGGER [dbo].[tr] ON [dbo].[t] AFTER INSERT AS\n" + INSERT + "\nGO\n", [])

    def test_create_or_alter_function_body_is_never_imported(self):
        self.assert_commands("CREATE OR ALTER FUNCTION [dbo].[f]() RETURNS INT AS\nBEGIN\n" + INSERT + "\nRETURN 1\nEND\nGO\n", [])

    def test_procedure_header_cannot_hide_data_before_as(self):
        self.assert_refused("CREATE PROCEDURE [dbo].[p]\n" + INSERT + ";\nSELECT 1 AS [x]\nGO\n")

    def test_function_header_cannot_hide_data_before_as(self):
        self.assert_refused("CREATE FUNCTION [dbo].[f]() RETURNS INT\n" + INSERT + ";\nSELECT 1 AS [x]\nGO\n")

    def test_routine_without_body_is_incomplete(self):
        self.assert_refused("CREATE PROCEDURE [dbo].[p] AS\n")

    def test_create_table_alone_can_be_ignored(self):
        self.assert_commands("CREATE TABLE [dbo].[t] ([id] INT, [s] NVARCHAR(10) DEFAULT N'INSERT; GO');\nGO\n" + INSERT, [INSERT])

    def test_alter_table_alone_can_be_ignored(self):
        self.assert_commands("ALTER TABLE [dbo].[t] ADD [c] INT;\nGO\n" + INSERT, [INSERT])

    def test_create_table_and_data_in_same_batch_are_refused(self):
        self.assert_refused("CREATE TABLE [t] ([id] INT);\n" + INSERT)

    def test_alter_table_and_data_in_same_batch_are_refused(self):
        self.assert_refused("ALTER TABLE [t] ADD [c] INT\n" + INSERT)

    def test_data_then_ddl_is_refused_before_first_execute(self):
        self.assert_refused(INSERT + ";\nCREATE TABLE [t] ([id] INT)")

    def test_unsupported_statement_after_data_is_refused_before_execute(self):
        self.assert_refused(INSERT + ";\nEXEC [dbo].[p]")

    def test_prior_batch_remains_confirmed_if_later_batch_is_invalid(self):
        result = self.assert_refused(INSERT + "\nGO\n" + INSERT + ";\nDELETE FROM [t]", confirmed=[INSERT])
        self.assertIn("Importação parcialmente concluída", result.output)

    def test_unterminated_string_is_refused(self):
        self.assert_refused("INSERT INTO [t] VALUES (N'" + PRIVATE)

    def test_unterminated_block_comment_is_refused(self):
        self.assert_refused(INSERT + "\n/* " + PRIVATE)

    def test_unterminated_bracket_identifier_is_refused(self):
        self.assert_refused("INSERT INTO [" + PRIVATE + " VALUES (1)")

    def test_line_limit_is_enforced_before_execute(self):
        self.assert_refused("INSERT INTO [t] VALUES (N'" + "x" * 65536 + "')")

    def test_statement_limit_is_enforced_before_execute(self):
        self.assert_refused("INSERT INTO [t] VALUES (N'" + ("x" * 8192 + "\n") * 33 + "')")

    def test_batch_limit_is_enforced_before_first_execute(self):
        self.assert_refused((INSERT + "\n") * 24000)

    def test_comment_nesting_limit_is_enforced(self):
        self.assert_refused("/*" * 33 + " */" * 33)

    def test_ddl_nesting_limit_is_enforced(self):
        self.assert_refused("CREATE TABLE [t] (" + "(" * 64 + "1" + ")" * 65 + ")")

    def test_line_at_limit_is_supported(self):
        prefix, suffix = "INSERT INTO [t] VALUES (N'", "')"
        text = prefix + "x" * (65536 - len(prefix) - len(suffix)) + suffix
        self.assert_commands(text, [text])

    def test_statement_at_limit_is_supported(self):
        prefix, suffix = "INSERT INTO [t] VALUES (N'", "')"
        body = ("x" * 60000 + "\n") * 4
        text = prefix + body + "x" * (262144 - len(prefix) - len(suffix) - len(body)) + suffix
        self.assert_commands(text, [text])

    def test_batch_at_limit_is_supported(self):
        remaining = 1048576 - len(INSERT) - 1
        full_lines, tail = divmod(remaining, 60001)
        text = INSERT + "\n" + (" " * 60000 + "\n") * full_lines + " " * tail
        self.assert_commands(text, [INSERT])

    def test_comment_nesting_at_limit_is_supported(self):
        self.assert_commands("/*" * 32 + " */" * 32 + "\n" + INSERT, [INSERT])

    def test_ddl_nesting_at_limit_is_supported(self):
        text = "CREATE TABLE [t] ([v] INT DEFAULT " + "(" * 63 + "1" + ")" * 63 + ")"
        self.assert_commands(text, [])

    def test_first_command_requires_only_its_validated_batch(self):
        from sql_classifier import SQLCommandReader

        class GuardedStream:
            def __init__(self):
                self.lines = iter((INSERT + "\n", "GO\n", INSERT + "\n", "GO\n"))
                self.calls = 0
                self.allowed = 2

            def readline(self, size):
                self.calls += 1
                if self.calls > self.allowed:
                    raise AssertionError("O leitor consumiu um lote futuro antecipadamente.")
                return next(self.lines, "")

        stream = GuardedStream()
        commands = iter(SQLCommandReader(stream, target_database="DestinoSintetico"))
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(stream.calls, 2)
        stream.allowed = 5
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(list(commands), [])

    def test_large_synthetic_stream_uses_bounded_reads_and_releases_batches(self):
        class IncrementalStream:
            def __init__(self):
                self.lines = iter(line for _ in range(30000) for line in (INSERT + "\n", "GO\n"))
                self.read_calls = 0

            def readline(self, size=-1):
                if not 0 < size <= 65537:
                    raise AssertionError("A leitura deve ser limitada.")
                self.read_calls += 1
                return next(self.lines, "")

            def close(self):
                pass

        stream = IncrementalStream()
        scenario = Scenario()
        scenario.file.stream = stream
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(scenario.cursor.statements), 30000)
        self.assertTrue(all(statement == INSERT for statement in scenario.cursor.statements))
        self.assertEqual(scenario.connection.commit_attempts, 30000)
        self.assertEqual(stream.read_calls, 60001)
        self.assertEqual(scenario.file.close_attempts, 1)


if __name__ == "__main__":
    unittest.main()
