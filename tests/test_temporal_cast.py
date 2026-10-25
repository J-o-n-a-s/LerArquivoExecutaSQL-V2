"""Fase 2B-R1: CAST temporal restrito, sempre com dados sintéticos."""

import codecs
import unittest

from sql_classifier import MAX_BATCH_CHARS, MAX_LINE_CHARS, MAX_STATEMENT_CHARS, SQLCommandReader
from tests._application_harness import PRIVATE, Scenario


def insert(expression):
    return f"INSERT INTO [dbo].[TemporalSintetico] ([valor]) VALUES ({expression})"


class TemporalCASTTests(unittest.TestCase):
    def assert_closed(self, scenario):
        self.assertEqual(scenario.cursor.close_attempts, 1)
        self.assertEqual(scenario.connection.close_attempts, 1)
        self.assertEqual(scenario.file.close_attempts, 1)

    def assert_sql(self, text, expected=None, *, payload=None):
        scenario = Scenario(text=text, payload=payload)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(scenario.cursor.statements, [text] if expected is None else expected)
        self.assertEqual(scenario.connection.commit_attempts, len(scenario.cursor.statements))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertEqual(scenario.connect.call_count, 1)
        self.assertNotIn(PRIVATE, result.output)
        self.assert_closed(scenario)
        return scenario, result

    def assert_refused(self, text, *, confirmed=()):
        scenario = Scenario(text=text)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.cursor.statements, list(confirmed))
        self.assertEqual(scenario.connection.commit_attempts, len(confirmed))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertIn("SQLClassificationError", result.output)
        self.assertNotIn("Processo finalizado", result.output)
        self.assertNotIn(PRIVATE, result.output)
        self.assert_closed(scenario)
        return result

    def test_datetime2_without_precision(self):
        self.assert_sql(insert("CAST('2001-02-03T04:05:06.1234567' AS DATETIME2)"))

    def test_datetime2_precisions_zero_through_seven(self):
        for precision in range(8):
            with self.subTest(precision=precision):
                self.assert_sql(insert(f"CAST('2001-02-03T04:05:06' AS DATETIME2({precision}))"))

    def test_date_without_precision(self):
        self.assert_sql(insert("CAST('2001-02-03' AS DATE)"))

    def test_time_without_precision(self):
        self.assert_sql(insert("CAST('04:05:06.1234567' AS TIME)"))

    def test_time_precisions_zero_through_seven(self):
        for precision in range(8):
            with self.subTest(precision=precision):
                self.assert_sql(insert(f"CAST('04:05:06' AS TIME({precision}))"))

    def test_unicode_literal(self):
        self.assert_sql(insert("CAST(N'2001-02-03T04:05:06.1234567' AS DATETIME2(7))"))

    def test_lowercase_unicode_prefix(self):
        self.assert_sql(insert("CAST(n'2001-02-03' AS DATE)"))

    def test_escaped_quotes_remain_verbatim(self):
        self.assert_sql(insert("CAST(N'conteúdo ''escapado''' AS DATE)"))

    def test_lowercase_keywords(self):
        self.assert_sql(insert("cast('04:05:06' as time(7))").lower())

    def test_mixed_case_keywords(self):
        self.assert_sql(insert("cAsT(N'2001-02-03' aS dAtE)"))

    def test_comments_and_spaces_remain_verbatim(self):
        expression = "CaSt /* fora */ ( N'2001-02-03' /* literal */ AS /* tipo */ DaTeTiMe2 /* p */ ( /* limite */ 7 ) )"
        self.assert_sql(insert(expression))

    def test_line_comment_inside_cast(self):
        self.assert_sql(insert("CAST(-- operando\nN'04:05:06' AS -- tipo\nTIME(7))"))

    def test_nested_comments_inside_cast(self):
        self.assert_sql(insert("CAST(/* externo /* interno */ fim */ '2001-02-03' AS DATE)"))

    def test_multiple_cast_values_count_as_individual_values(self):
        text = "INSERT INTO [t] ([a], [b], [c]) VALUES (CAST('2001-02-03' AS DATE), CAST(N'04:05:06' AS TIME(7)), CAST('2001-02-03T04:05:06' AS DATETIME2(0)))"
        self.assert_sql(text)

    def test_cast_and_simple_scalars(self):
        self.assert_sql("INSERT INTO [t] ([a], [b], [c], [d], [e], [f]) VALUES (CAST('2001-02-03' AS DATE), -2, NULL, DEFAULT, N'ação', 0xAF)")

    def test_insert_without_into(self):
        self.assert_sql(insert("CAST('2001-02-03' AS DATE)").replace("INSERT INTO", "INSERT", 1))

    def test_multiline_insert_and_cast(self):
        self.assert_sql("INSERT INTO [t]\n([a])\nVALUES (CAST(\nN'2001-02-03T04:05:06'\nAS\nDATETIME2(\n7\n)\n))")

    def test_multiple_insert_commands_preserve_order(self):
        first = insert("CAST('2001-02-03' AS DATE)") + ";"
        second = insert("CAST('04:05:06' AS TIME(0))")
        self.assert_sql(first + "\n" + second, [first, second])

    def test_multiple_rows_preserve_value_width(self):
        self.assert_sql("INSERT INTO [t] ([a], [b]) VALUES (1, CAST('2001-02-03' AS DATE)), (2, CAST('04:05:06' AS TIME))")

    def test_cast_text_inside_literal_is_not_evaluated(self):
        self.assert_sql(insert("N'CAST(GETDATE() AS INVALID); EXEC [p]; GO'"))

    def test_cast_text_inside_comments_is_ignored(self):
        text = insert("CAST('2001-02-03' AS DATE)")
        self.assert_sql("/* CAST(GETDATE() AS INVALID) */\n" + text + "\n-- CAST(SELECT 1 AS DATE)", [text])

    def test_multiline_literal_can_contain_go(self):
        self.assert_sql(insert("CAST(N'primeira\nGO\núltima' AS DATE)"))

    def test_semicolon_and_sql_keywords_inside_cast_literal_are_preserved(self):
        self.assert_sql(insert("CAST(N'2001; DROP TABLE [sintetico]; INSERT; GO' AS DATE)"))

    def test_unicode_identifiers_and_mojibake_are_preserved(self):
        text = 'INSERT INTO "ação".[t]]漢字] ([v]) VALUES (CAST(N\'ÿþ 漢字\' AS DATE))'
        self.assert_sql(text)

    def test_utf16_bom_endianness_preserves_cast(self):
        text = insert("CAST(N'2001-02-03' AS DATE)")
        for bom, encoding in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(encoding=encoding):
                self.assert_sql("", [text], payload=bom + text.encode(encoding))

    def test_semantically_invalid_date_is_left_to_sql_server(self):
        self.assert_sql(insert("CAST(N'conteúdo sem validação de data' AS DATE)"))

    def test_empty_literal_is_not_interpreted_in_python(self):
        self.assert_sql(insert("CAST('' AS DATETIME2)"))

    def test_unauthorized_types_are_refused(self):
        for sql_type in ("DATETIME", "SMALLDATETIME", "DATETIMEOFFSET", "VARCHAR", "INT", "CUSTOM_TYPE", "DATE2", "TIME2", "DATETIME2X"):
            with self.subTest(sql_type=sql_type):
                self.assert_refused(insert(f"CAST('2001-02-03' AS {sql_type})"))

    def test_date_cannot_have_precision(self):
        for precision in ("0", "7"):
            with self.subTest(precision=precision):
                self.assert_refused(insert(f"CAST('2001-02-03' AS DATE({precision}))"))

    def test_invalid_datetime2_precision_is_refused(self):
        for precision in ("-1", "8", "10", "7.0", "1e0", "0x7", "'7'", "NULL", "+7", "07", "00", "", "7,0", "7+0", "CAST('7' AS INT)"):
            with self.subTest(precision=precision):
                self.assert_refused(insert(f"CAST('2001-02-03' AS DATETIME2({precision}))"))

    def test_invalid_time_precision_is_refused(self):
        for precision in ("-1", "8", "7.0", "7e0", "0x7", "'7'", "NULL", "+7", "07", "", "7,0"):
            with self.subTest(precision=precision):
                self.assert_refused(insert(f"CAST('04:05:06' AS TIME({precision}))"))

    def test_quoted_or_qualified_type_is_not_in_contract(self):
        for sql_type in ("[DATE]", '"TIME"', "sys.DATETIME2", "dbo.DATE"):
            with self.subTest(sql_type=sql_type):
                self.assert_refused(insert(f"CAST('2001-02-03' AS {sql_type})"))

    def test_missing_operand_is_refused(self):
        self.assert_refused(insert("CAST(AS DATE)"))

    def test_non_string_operands_are_outside_minimum_contract(self):
        for operand in ("NULL", "DEFAULT", "1", "-1", "0x00", "[coluna]"):
            with self.subTest(operand=operand):
                self.assert_refused(insert(f"CAST({operand} AS DATE)"))

    def test_extra_parentheses_around_operand_are_not_supported(self):
        self.assert_refused(insert("CAST(('2001-02-03') AS DATE)"))

    def test_getdate_operand_is_refused(self):
        self.assert_refused(insert("CAST(GETDATE() AS DATE)"))

    def test_other_function_operand_is_refused(self):
        self.assert_refused(insert("CAST(SYSDATETIME() AS DATETIME2)"))

    def test_convert_remains_unsupported(self):
        self.assert_refused(insert("CONVERT(DATE, '2001-02-03')"))

    def test_nested_cast_is_refused(self):
        self.assert_refused(insert("CAST(CAST('2001-02-03' AS DATE) AS DATETIME2)"))

    def test_deeply_nested_cast_fails_without_recursion(self):
        expression = "CAST(" * 100 + "'2001-02-03' AS DATE)" + " AS DATE)" * 99
        self.assert_refused(insert(expression))

    def test_compound_operand_is_refused(self):
        self.assert_refused(insert("CAST('2001-' + '02-03' AS DATE)"))

    def test_expression_after_cast_is_refused(self):
        self.assert_refused(insert("CAST('2001-02-03' AS DATE) + 1"))

    def test_subquery_operand_is_refused(self):
        self.assert_refused(insert("CAST((SELECT '2001-02-03') AS DATE)"))

    def test_dynamic_sql_operand_is_refused(self):
        self.assert_refused(insert("CAST(EXEC(N'SELECT 1') AS DATE)"))

    def test_missing_as_is_refused(self):
        self.assert_refused(insert("CAST('2001-02-03' DATE)"))

    def test_incomplete_cast_is_refused(self):
        for suffix in ("CAST(", "CAST('2001-02-03' AS", "CAST('2001-02-03' AS DATE", "CAST('2001-02-03' AS TIME(7)"):
            with self.subTest(suffix=suffix):
                self.assert_refused("INSERT INTO [t] VALUES (" + suffix)

    def test_cast_keyword_boundary_is_exact(self):
        self.assert_refused(insert("CASTING('2001-02-03' AS DATE)"))

    def test_unterminated_operand_does_not_expose_value(self):
        self.assert_refused("INSERT INTO [t] VALUES (CAST(N'" + PRIVATE)

    def test_cast_is_one_value_for_column_count(self):
        self.assert_refused("INSERT INTO [t] ([a], [b]) VALUES (CAST('2001-02-03' AS DATE))")

    def test_extra_precision_parentheses_are_refused(self):
        self.assert_refused(insert("CAST('2001-02-03' AS DATETIME2((7)))"))

    def test_precision_cannot_be_an_unbounded_integer_conversion(self):
        self.assert_refused(insert("CAST('2001-02-03' AS TIME(" + "9" * 5000 + "))"))

    def test_unknown_command_after_cast_prevents_whole_batch_execution(self):
        self.assert_refused(insert("CAST('2001-02-03' AS DATE)") + ";\nEXEC [dbo].[p]")

    def test_invalid_second_insert_prevents_valid_first_insert_execution(self):
        first = insert("CAST('2001-02-03' AS DATE)")
        second = insert("CAST(GETDATE() AS DATE)")
        self.assert_refused(first + ";\n" + second)

    def test_previous_batch_stays_confirmed_when_new_cast_is_invalid(self):
        first = insert("CAST('2001-02-03' AS DATE)")
        result = self.assert_refused(first + "\nGO\n" + insert("CAST('x' AS TIME(8))"), confirmed=[first])
        self.assertIn("Importação parcialmente concluída", result.output)

    def test_ddl_mixed_with_cast_data_remains_refused(self):
        self.assert_refused(insert("CAST('2001-02-03' AS DATE)") + ";\nCREATE TABLE [t] ([v] INT)")

    def test_cast_in_procedure_body_is_never_imported(self):
        text = insert("CAST('2001-02-03' AS DATE)")
        self.assert_sql("CREATE PROC [dbo].[p] AS\n" + text + "\nGO\n" + text, [text])

    def test_line_limit_still_applies_to_cast(self):
        self.assert_refused(insert("CAST(N'" + "x" * MAX_LINE_CHARS + "' AS DATE)"))

    def test_statement_limit_still_applies_to_cast(self):
        chunk = "x" * 8192 + "\n"
        self.assert_refused(insert("CAST(N'" + chunk * (MAX_STATEMENT_CHARS // len(chunk) + 1) + "' AS DATE)"))

    def test_batch_limit_still_applies_before_any_cast_execution(self):
        text = insert("CAST('2001-02-03' AS DATE)") + "\n"
        self.assert_refused(text * (MAX_BATCH_CHARS // len(text) + 1))

    def test_comment_depth_limit_inside_cast_is_preserved(self):
        self.assert_refused(insert("CAST(" + "/*" * 33 + "*/" * 33 + "'2001-02-03' AS DATE)"))

    def test_cast_execute_failure_rolls_back_and_closes_resources(self):
        scenario = Scenario(text=insert("CAST('2001-02-03' AS DATE)"), execute_error_at=1)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertEqual(scenario.connection.commit_attempts, 0)
        self.assertIn("ExecuteError", result.output)
        self.assertNotIn(PRIVATE, result.output)
        self.assert_closed(scenario)

    def test_cast_commit_failure_preserves_unknown_confirmation(self):
        scenario = Scenario(text=insert("CAST('2001-02-03' AS DATE)"), commit_error_at=1)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("CommitError", result.output)
        self.assertIn("Resultado de confirmação desconhecido", result.output)
        self.assert_closed(scenario)

    def test_cast_interruption_preserves_cleanup_and_rollback(self):
        scenario = Scenario(text=insert("CAST('2001-02-03' AS DATE)"), execute_error_at=1, execute_error=KeyboardInterrupt(PRIVATE))
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 130)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertEqual(scenario.connection.commit_attempts, 0)
        self.assertNotIn(PRIVATE, result.output)
        self.assert_closed(scenario)

    def test_cast_stream_is_incremental_ordered_and_bounded(self):
        class Stream:
            def __init__(self):
                self.index = 0
                self.limit = 2
                self.calls = 0

            def readline(self, size):
                if not 0 < size <= MAX_LINE_CHARS + 1:
                    raise AssertionError("Leitura sem limite.")
                self.calls += 1
                if self.calls > self.limit:
                    raise AssertionError("Leitura antecipada de lotes futuros.")
                if self.index == 10000:
                    return ""
                self.index += 1
                if self.index % 2 == 0:
                    return "GO\n"
                return f"INSERT INTO [t] VALUES ({self.index}, CAST('2001-02-03' AS DATE))\n"

        stream = Stream()
        commands = iter(SQLCommandReader(stream, target_database="DestinoSintetico"))
        self.assertEqual(next(commands).text, "INSERT INTO [t] VALUES (1, CAST('2001-02-03' AS DATE))")
        self.assertEqual(stream.calls, 2)
        stream.limit = 10001
        count = 1
        for command in commands:
            count += 1
            self.assertEqual(command.text, f"INSERT INTO [t] VALUES ({count * 2 - 1}, CAST('2001-02-03' AS DATE))")
        self.assertEqual(count, 5000)
        self.assertEqual(stream.calls, 10001)


if __name__ == "__main__":
    unittest.main()
