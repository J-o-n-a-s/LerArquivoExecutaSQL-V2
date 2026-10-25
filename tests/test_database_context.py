"""R2: contexto e qualificadores, exclusivamente com SQL e conexão sintéticos."""

import codecs
import io
import unittest

from sql_classifier import MAX_BATCH_CHARS, MAX_LINE_CHARS, SQLClassificationError, SQLCommandReader
from tests._application_harness import PRIVATE, Scenario


DESTINATION = "DestinoSintetico"
EXTERNAL = "ExternoSintetico"
INSERT = "INSERT INTO [dbo].[TabelaSintetica] VALUES (1)"


def qualified(database):
    return f"[{database}].[dbo].[TabelaSintetica]"


class DatabaseContextTests(unittest.TestCase):
    def run_sql(self, text, *, expected=(), refused=False, target=DESTINATION, payload=None):
        scenario = Scenario(text=text, target_database=target, payload=payload)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1 if refused else 0, result.output)
        self.assertEqual(scenario.cursor.statements, list(expected))
        self.assertEqual(scenario.confirmed, list(expected))
        self.assertEqual(scenario.connection.commit_attempts, len(expected))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertEqual(scenario.connect.call_count, 1)
        self.assertEqual((scenario.cursor.close_attempts, scenario.connection.close_attempts, scenario.file.close_attempts), (1, 1, 1))
        self.assertIn(f"Database={target};", scenario.connect.call_args.args[0])
        self.assertEqual(result.namespace["database"], target)
        for secret in (DESTINATION, EXTERNAL, target, PRIVATE, "TabelaSintetica"):
            self.assertNotIn(secret, result.output)
        if refused:
            self.assertIn("SQLClassificationError", result.output)
            self.assertNotIn("Processo finalizado", result.output)
        return scenario, result

    def reader(self, text, target=DESTINATION):
        return SQLCommandReader(io.StringIO(text), target_database=target)

    def test_use_matching_destination_is_control_only(self):
        self.run_sql(f"USE [{DESTINATION}]\nGO\n{INSERT}", expected=[INSERT])

    def test_use_external_without_data_is_control_only(self):
        self.run_sql(f"USE [{EXTERNAL}]\nGO")

    def test_external_target_external_sequence(self):
        ddl = "CREATE TABLE [dbo].[PreparacaoSintetica] ([v] INT)"
        text = f"USE [{EXTERNAL}]\nGO\n{ddl}\nGO\nUSE [{DESTINATION}]\nGO\n{INSERT}\nGO\nUSE [{EXTERNAL}]\nGO"
        self.run_sql(text, expected=[INSERT])

    def test_use_at_eof_does_not_require_go(self):
        self.run_sql(f"USE {DESTINATION}")

    def test_use_and_insert_in_same_batch_are_refused(self):
        self.run_sql(f"USE [{DESTINATION}];\n{INSERT}", refused=True)

    def test_use_optional_semicolon_and_comments(self):
        self.run_sql(f"/* início */ USE /* nome */ [{DESTINATION}] /* fim */ ; -- fim\nGO\n{INSERT}", expected=[INSERT])

    def test_use_keyword_case_does_not_change_name(self):
        for keyword in ("use", "uSe"):
            with self.subTest(keyword=keyword):
                self.run_sql(f"{keyword} [{DESTINATION}]\nGO\n{INSERT}", expected=[INSERT])

    def test_use_inside_comments_does_not_change_context(self):
        self.run_sql(f"-- USE [{EXTERNAL}]\n/* USE [{EXTERNAL}] */\n{INSERT}", expected=[INSERT])

    def test_use_inside_string_does_not_change_context(self):
        text = f"INSERT INTO [dbo].[TabelaSintetica] VALUES (N'USE [{EXTERNAL}]; GO')"
        self.run_sql(text, expected=[text])

    def test_bracket_and_bare_names_compare_decoded_identifiers(self):
        for name in (DESTINATION, f"[{DESTINATION}]", f'"{DESTINATION}"'):
            with self.subTest(name=name):
                self.run_sql(f"USE {name}\nGO\n{INSERT}", expected=[INSERT])

    def test_bracket_escape_in_use_and_target(self):
        target = "Destino]Sintetico"
        text = "INSERT INTO [Destino]]Sintetico].[dbo].[TabelaSintetica] VALUES (1)"
        self.run_sql(f"USE [Destino]]Sintetico]\nGO\n{text}", target=target, expected=[text])

    def test_double_quote_escape_in_use_and_target(self):
        target = 'Destino"Sintetico'
        text = 'INSERT INTO "Destino""Sintetico".[dbo].[TabelaSintetica] VALUES (1)'
        self.run_sql('USE "Destino""Sintetico"\nGO\n' + text, target=target, expected=[text])

    def test_unicode_database_name_is_preserved(self):
        target = "DestinoAção漢字"
        text = f"INSERT INTO [{target}].[dbo].[TabelaSintetica] VALUES (N'ação')"
        self.run_sql(f"USE [{target}]\nGO\n{text}", target=target, expected=[text])

    def test_database_name_comparison_is_case_sensitive(self):
        self.run_sql(f"USE [{DESTINATION.lower()}]\nGO\n{INSERT}", refused=True)

    def test_unicode_normalization_is_not_assumed(self):
        self.run_sql("USE [Destinó]\nGO\n" + INSERT, target="Destino\u0301", refused=True)

    def test_identifier_trailing_space_is_not_trimmed(self):
        self.run_sql(f"USE [{DESTINATION} ]\nGO\n{INSERT}", refused=True)

    def test_empty_and_invalid_use_identifiers_are_refused(self):
        for name in ("", "[]", '""', "'DestinoSintetico'", "123", "@banco", ".dbo", "[aberto", "SELECT"):
            with self.subTest(name=name):
                self.run_sql(f"USE {name}", refused=True)

    def test_use_cannot_have_qualified_name(self):
        for name in (f"{DESTINATION}.dbo", f"[{DESTINATION}].[dbo]", f"[{DESTINATION}]..[t]"):
            with self.subTest(name=name):
                self.run_sql(f"USE {name}", refused=True)

    def test_two_use_commands_in_one_batch_are_ambiguous(self):
        self.run_sql(f"USE [{DESTINATION}]; USE [{EXTERNAL}];", refused=True)

    def test_use_extra_semicolon_is_not_silently_skipped(self):
        self.run_sql(f"USE [{DESTINATION}];;", refused=True)

    def test_semicolon_inside_identifier_is_not_a_separator(self):
        self.run_sql(f"USE [{DESTINATION};USE {EXTERNAL}]\nGO\n{INSERT}", refused=True)

    def test_external_data_context_is_not_silently_discarded(self):
        self.run_sql(f"USE [{EXTERNAL}]\nGO\n{INSERT}", refused=True)

    def test_external_context_blocks_identity_insert(self):
        self.run_sql(f"USE [{EXTERNAL}]\nGO\nSET IDENTITY_INSERT [dbo].[TabelaSintetica] ON", refused=True)

    def test_external_context_blocks_even_explicit_target_qualifier(self):
        text = f"INSERT INTO {qualified(DESTINATION)} VALUES (1)"
        self.run_sql(f"USE [{EXTERNAL}]\nGO\n{text}", refused=True)

    def test_external_context_only_keeps_existing_ddl_discard_policy(self):
        self.run_sql(f"USE [{EXTERNAL}]\nGO\nCREATE TABLE [dbo].[PreparacaoSintetica] ([v] INT)\nGO\nALTER TABLE [dbo].[PreparacaoSintetica] ADD [v2] INT\nGO\nCREATE PROC [dbo].[RotinaSintetica] AS SELECT 1")

    def test_unknown_external_commands_are_not_discarded(self):
        for command in ("SET NOCOUNT ON", "EXEC [dbo].[RotinaSintetica]", "CREATE VIEW [dbo].[v] AS SELECT 1"):
            with self.subTest(command=command):
                self.run_sql(f"USE [{EXTERNAL}]\nGO\n{command}", refused=True)

    def test_unknown_command_after_target_use_is_refused(self):
        self.run_sql(f"USE [{DESTINATION}]\nGO\nEXEC [dbo].[RotinaSintetica]", refused=True)

    def test_one_part_insert_uses_configured_destination(self):
        text = "INSERT INTO [TabelaSintetica] VALUES (1)"
        self.run_sql(text, expected=[text])

    def test_two_part_insert_is_schema_and_table(self):
        text = f"INSERT INTO [{EXTERNAL}].[TabelaSintetica] VALUES (1)"
        self.run_sql(text, expected=[text])

    def test_three_part_insert_for_destination_is_preserved(self):
        text = f"INSERT INTO {qualified(DESTINATION)} VALUES (1)"
        self.run_sql(text, expected=[text])

    def test_three_part_insert_for_other_database_is_refused(self):
        self.run_sql(f"INSERT INTO {qualified(EXTERNAL)} VALUES (1)", refused=True)

    def test_three_part_insert_name_case_mismatch_is_refused(self):
        self.run_sql(f"INSERT INTO {qualified(DESTINATION.lower())} VALUES (1)", refused=True)

    def test_three_part_identity_for_destination_is_preserved(self):
        for state in ("ON", "OFF"):
            with self.subTest(state=state):
                text = f"SET IDENTITY_INSERT {qualified(DESTINATION)} {state}"
                self.run_sql(text, expected=[text])

    def test_three_part_identity_for_other_database_is_refused(self):
        for state in ("ON", "OFF"):
            with self.subTest(state=state):
                self.run_sql(f"SET IDENTITY_INSERT {qualified(EXTERNAL)} {state}", refused=True)

    def test_two_part_identity_is_schema_and_table(self):
        text = f"SET IDENTITY_INSERT [{EXTERNAL}].[TabelaSintetica] ON"
        self.run_sql(text, expected=[text])

    def test_go_retains_target_context_for_multiple_batches(self):
        self.run_sql(f"USE [{DESTINATION}]\nGO\n{INSERT}\nGO\n{INSERT}", expected=[INSERT, INSERT])

    def test_insert_before_any_use_is_explicitly_supported(self):
        self.run_sql(f"{INSERT}\nGO\nUSE [{DESTINATION}]\nGO\n{INSERT}", expected=[INSERT, INSERT])

    def test_no_use_preserves_commit_order(self):
        first = "SET IDENTITY_INSERT [dbo].[TabelaSintetica] ON;"
        last = "SET IDENTITY_INSERT [dbo].[TabelaSintetica] OFF;"
        self.run_sql(f"{first}\n{INSERT};\n{last}", expected=[first, INSERT + ";", last])

    def test_insert_then_use_in_same_batch_is_refused_before_execute(self):
        self.run_sql(f"{INSERT};\nUSE [{DESTINATION}]", refused=True)

    def test_allowed_then_foreign_target_refuses_whole_batch(self):
        self.run_sql(f"{INSERT};\nINSERT INTO {qualified(EXTERNAL)} VALUES (1)", refused=True)

    def test_allowed_then_foreign_identity_refuses_whole_batch(self):
        self.run_sql(f"{INSERT};\nSET IDENTITY_INSERT {qualified(EXTERNAL)} ON", refused=True)

    def test_previous_commits_remain_after_context_refusal(self):
        _, result = self.run_sql(f"{INSERT}\nGO\nUSE [{EXTERNAL}]\nGO\n{INSERT}", expected=[INSERT], refused=True)
        self.assertIn("Importação parcialmente concluída", result.output)
        self.assertIn("Comandos confirmados: 1", result.output)
        self.assertIn("Classificação SQL recusada na linha 5:", result.output)

    def test_utf16_bom_endianness_preserves_context(self):
        text = f"USE [{DESTINATION}]\r\nGO\r\n{INSERT}"
        for bom, encoding in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(encoding=encoding):
                self.run_sql("", expected=[INSERT], payload=bom + text.encode(encoding))

    def test_comments_between_qualifier_tokens_do_not_bypass_check(self):
        self.run_sql(f"INSERT /* x */ INTO [{EXTERNAL}] /* a */ . /* b */ [dbo] . [TabelaSintetica] VALUES (1)", refused=True)

    def test_mixed_qualifier_delimiters_are_checked(self):
        self.run_sql(f'INSERT INTO "{EXTERNAL}".[dbo].TabelaSintetica VALUES (1)', refused=True)

    def test_bare_foreign_database_qualifier_is_checked(self):
        self.run_sql(f"INSERT INTO {EXTERNAL}.dbo.TabelaSintetica VALUES (1)", refused=True)

    def test_four_part_and_omitted_parts_remain_refused(self):
        for target in (f"[servidor].{qualified(DESTINATION)}", f"[{DESTINATION}]..[TabelaSintetica]", f".[dbo].[TabelaSintetica]", f"[{DESTINATION}].[dbo]."):
            with self.subTest(target=target):
                self.run_sql(f"INSERT INTO {target} VALUES (1)", refused=True)
                self.run_sql(f"SET IDENTITY_INSERT {target} ON", refused=True)

    def test_dot_inside_one_identifier_is_not_database_qualifier(self):
        text = f"INSERT INTO [{EXTERNAL}.dbo.TabelaSintetica] VALUES (1)"
        self.run_sql(text, expected=[text])

    def test_dot_inside_database_identifier_compares_as_one_name(self):
        target = "Destino.Sintetico"
        text = f"INSERT INTO [{target}].[dbo].[TabelaSintetica] VALUES (1)"
        self.run_sql(f"USE [{target}]\nGO\n{text}", target=target, expected=[text])

    def test_bracket_escape_cannot_disguise_external_database(self):
        self.run_sql(f"INSERT INTO [{DESTINATION}]]].[dbo].[TabelaSintetica] VALUES (1)", refused=True)

    def test_malformed_delimiters_do_not_release_valid_command(self):
        self.run_sql(f"{INSERT};\nUSE [{DESTINATION}", refused=True)

    def test_split_keyword_is_not_reassembled(self):
        self.run_sql(f"U/**/SE [{DESTINATION}]", refused=True)

    def test_use_text_in_routine_does_not_change_context(self):
        self.run_sql(f"CREATE PROC [dbo].[RotinaSintetica] AS USE [{EXTERNAL}]\nGO\n{INSERT}", expected=[INSERT])

    def test_use_after_alter_table_cannot_be_swallowed_as_ddl(self):
        self.run_sql(f"ALTER TABLE [dbo].[PreparacaoSintetica] ADD [v] INT\nUSE [{EXTERNAL}]\nGO\n{INSERT}", refused=True)

    def test_use_in_routine_header_is_ambiguous_and_refused(self):
        self.run_sql(f"CREATE PROC [dbo].[RotinaSintetica]\nUSE [{EXTERNAL}]\nAS SELECT 1\nGO\n{INSERT}", refused=True)

    def test_database_reference_comes_from_configuration(self):
        for target in ("DestinoAlternativoUm", "DestinoAlternativoDois"):
            with self.subTest(target=target):
                text = f"INSERT INTO {qualified(target)} VALUES (1)"
                self.run_sql(f"USE [{target}]\nGO\n{text}", target=target, expected=[text])

    def test_context_states_distinguish_implicit_target_and_external(self):
        reader = self.reader(f"{INSERT}\nGO\nUSE [{DESTINATION}]\nGO\n{INSERT}\nGO\nUSE [{EXTERNAL}]")
        self.assertEqual(reader.context_state, "implicit")
        commands = iter(reader)
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(reader.context_state, "implicit")
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(reader.context_state, "target")
        self.assertEqual(list(commands), [])
        self.assertEqual(reader.context_state, "external")

    def test_invalid_use_marks_context_invalid(self):
        reader = self.reader("USE []")
        with self.assertRaises(SQLClassificationError):
            list(reader)
        self.assertEqual(reader.context_state, "invalid")

    def test_missing_destination_is_unknown_and_refused(self):
        reader = SQLCommandReader(io.StringIO(INSERT))
        self.assertEqual(reader.context_state, "unknown")
        with self.assertRaises(SQLClassificationError):
            list(reader)
        self.assertEqual(reader.line_number, 0)

    def test_invalid_configuration_is_refused_without_reading(self):
        for target in ("", " ", 1, "Destino;Database=Externo", "Destino\x00", "{Destino}"):
            with self.subTest(target=target):
                reader = self.reader(INSERT, target=target)
                with self.assertRaises(SQLClassificationError):
                    list(reader)
                self.assertEqual(reader.line_number, 0)

    def test_reader_cannot_resume_after_refused_batch(self):
        reader = self.reader(f"INSERT INTO {qualified(EXTERNAL)} VALUES (1)\nGO\n{INSERT}")
        with self.assertRaises(SQLClassificationError):
            list(reader)
        with self.assertRaises(SQLClassificationError):
            list(reader)
        self.assertEqual(reader.context_state, "invalid")

    def test_context_control_does_not_read_future_batch_early(self):
        class Stream:
            def __init__(self):
                self.lines = iter((f"USE [{DESTINATION}]\n", "GO\n", INSERT + "\n", "GO\n", INSERT + "\n", "GO\n"))
                self.calls = 0
                self.allowed = 4

            def readline(self, size):
                if not 0 < size <= MAX_LINE_CHARS + 1:
                    raise AssertionError("Leitura deve ser limitada.")
                self.calls += 1
                if self.calls > self.allowed:
                    raise AssertionError("Lote futuro lido antecipadamente.")
                return next(self.lines, "")

        stream = Stream()
        commands = iter(SQLCommandReader(stream, target_database=DESTINATION))
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(stream.calls, 4)
        stream.allowed = 7
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(list(commands), [])

    def test_use_line_limit_is_preserved(self):
        self.run_sql("USE [" + "x" * MAX_LINE_CHARS + "]", refused=True)

    def test_use_does_not_bypass_batch_limit(self):
        line = "/*" + "x" * 60000 + "*/\n"
        self.run_sql(f"USE [{DESTINATION}]\n" + line * (MAX_BATCH_CHARS // len(line) + 1), refused=True)

    def test_cast_contract_and_literal_preservation_survive_use(self):
        text = f"INSERT INTO {qualified(DESTINATION)} VALUES (CAST(N'2001-02-03' AS DATE))"
        self.run_sql(f"USE [{DESTINATION}]\nGO\n{text}", expected=[text])


if __name__ == "__main__":
    unittest.main()
