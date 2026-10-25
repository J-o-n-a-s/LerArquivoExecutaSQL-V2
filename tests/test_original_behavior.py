"""Os 44 casos históricos, sob o contrato conservador atual."""

import codecs
import unittest

from tests._original_harness import OriginalReader


INSERT = "INSERT INTO [dbo].[Sintetico] ([id], [nome]) VALUES (1, N'exemplo')"
IDENTITY_ON = "SET IDENTITY_INSERT [dbo].[Sintetico] ON"
IDENTITY_OFF = "SET IDENTITY_INSERT [dbo].[Sintetico] OFF"


class OriginalBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = OriginalReader()

    def assert_observation(self, text, statements, total, rejected, *, code=0):
        result = self.reader.observe_text(text)
        self.assertEqual(result.exit_code, code)
        self.assertEqual(result.statements, tuple(statements))
        self.assertEqual(result.total_lines, total)
        self.assertEqual(result.accepted_lines, len(statements))
        self.assertEqual(result.rejected_lines, rejected)
        self.assertEqual(result.commits, len(statements))
        # Linhas físicas e comandos não são equivalentes com multiline ou
        # vários comandos por linha. Os três contadores são checados acima.
        return result

    def test_insert_with_into_is_accepted(self):
        self.assert_observation(INSERT, [INSERT], 1, 0)

    def test_insert_without_into_is_accepted(self):
        statement = INSERT.replace("INSERT INTO", "INSERT", 1)
        self.assert_observation(statement, [statement], 1, 0)

    def test_insert_with_semicolon_is_accepted(self):
        self.assert_observation(INSERT + ";", [INSERT + ";"], 1, 0)

    def test_insert_without_into_with_semicolon_is_accepted(self):
        text = INSERT.replace("INSERT INTO", "INSERT", 1) + ";"
        self.assert_observation(text, [text], 1, 0)

    def test_insert_with_lf_is_accepted(self):
        self.assert_observation(INSERT + "\n", [INSERT], 1, 0)

    def test_insert_with_crlf_is_accepted(self):
        self.assert_observation(INSERT + "\r\n", [INSERT], 1, 0)

    def test_insert_with_surrounding_spaces_and_tabs_is_stripped(self):
        self.assert_observation(" \t" + INSERT + " \t\n", [INSERT], 1, 0)

    def test_insert_with_comma_is_incomplete_and_refused(self):
        self.assert_observation(INSERT + ",\n", [], 1, 0, code=1)

    def test_insert_with_inline_comment_is_accepted(self):
        self.assert_observation(INSERT + " -- comentário sintético\n", [INSERT], 1, 0)

    def test_insert_with_go_on_same_line_is_rejected(self):
        self.assert_observation(INSERT + " GO\n", [], 1, 0, code=1)

    def test_lowercase_insert_is_accepted(self):
        text = INSERT.replace("INSERT", "insert", 1)
        self.assert_observation(text, [text], 1, 0)

    def test_mixed_case_insert_is_accepted(self):
        text = INSERT.replace("INSERT", "Insert", 1)
        self.assert_observation(text, [text], 1, 0)

    def test_lowercase_into_and_values_are_accepted(self):
        statement = INSERT.replace("INTO", "into").replace("VALUES", "values")
        self.assert_observation(statement, [statement], 1, 0)

    def test_insert_prefix_without_keyword_boundary_is_refused(self):
        statement = "INSERTION sintético (1)"
        self.assert_observation(statement, [], 1, 0, code=1)

    def test_invalid_insert_without_body_is_refused(self):
        self.assert_observation("INSERT)", [], 1, 0, code=1)

    def test_multiline_insert_emits_only_the_complete_command(self):
        header = "INSERT INTO [dbo].[Sintetico] ([id], [nome])"
        text = header + "\nVALUES (1, N'exemplo')\n"
        self.assert_observation(text, [text.rstrip()], 2, 0)

    def test_multiline_insert_combines_all_fragments(self):
        text = "INSERT INTO [dbo].[Sintetico]\n([id])\nVALUES (1)\n"
        self.assert_observation(text, [text.rstrip()], 3, 0)

    def test_identity_insert_on_is_accepted(self):
        self.assert_observation(IDENTITY_ON, [IDENTITY_ON], 1, 0)

    def test_identity_insert_off_is_accepted(self):
        self.assert_observation(IDENTITY_OFF, [IDENTITY_OFF], 1, 0)

    def test_identity_insert_accepts_semicolon(self):
        self.assert_observation(IDENTITY_ON + ";", [IDENTITY_ON + ";"], 1, 0)

    def test_lowercase_identity_insert_is_accepted(self):
        self.assert_observation(IDENTITY_ON.lower(), [IDENTITY_ON.lower()], 1, 0)

    def test_mixed_case_identity_insert_is_accepted(self):
        text = IDENTITY_OFF.replace("SET", "Set", 1)
        self.assert_observation(text, [text], 1, 0)

    def test_identity_insert_lowercase_tail_is_accepted(self):
        statement = "SET IDentity_insert [dbo].[Sintetico] on"
        self.assert_observation(statement, [statement], 1, 0)

    def test_unrelated_set_id_prefix_is_refused(self):
        statement = "SET ID_INEXISTENTE"
        self.assert_observation(statement, [], 1, 0, code=1)

    def test_go_is_rejected(self):
        self.assert_observation("GO\n", [], 1, 1)

    def test_go_with_whitespace_is_rejected(self):
        self.assert_observation(" \tGO \t\r\n", [], 1, 1)

    def test_lowercase_and_mixed_case_go_are_rejected(self):
        self.assert_observation("go\nGo\ngO\n", [], 3, 3)

    def test_go_with_semicolon_or_comment_is_rejected(self):
        self.assert_observation("GO;\n", [], 1, 0, code=1)
        self.assert_observation("GO -- sintético\n", [], 1, 1)

    def test_line_comment_is_rejected(self):
        self.assert_observation("-- " + INSERT + "\n", [], 1, 1)

    def test_single_line_block_comment_is_rejected(self):
        self.assert_observation("/* " + INSERT + " */\n", [], 1, 1)

    def test_insert_inside_multiline_block_comment_is_ignored(self):
        self.assert_observation("/*\n" + INSERT + "\n*/\n", [], 3, 3)

    def test_empty_and_whitespace_lines_are_rejected(self):
        self.assert_observation("\n \t\n\r\n", [], 3, 3)

    def test_empty_input_has_zero_counters(self):
        self.assert_observation("", [], 0, 0)

    def test_mojibake_prefix_is_not_silently_removed(self):
        self.assert_observation("ÿþ" + INSERT, [], 1, 0, code=1)

    def test_mojibake_inside_literal_is_preserved(self):
        statement = "INSERT INTO [dbo].[Sintetico] ([nome]) VALUES (N'ÿþ')"
        self.assert_observation(statement, [statement], 1, 0)

    def test_utf16_little_endian_bom_and_unicode(self):
        statement = "INSERT INTO [dbo].[Sintetico] ([nome]) VALUES (N'ação 漢字')"
        payload = codecs.BOM_UTF16_LE + (statement + "\r\nGO\r\n").encode("utf-16-le")
        result = self.reader.observe_bytes(payload)
        self.assertEqual(result.statements, (statement,))
        self.assertEqual((result.total_lines, result.accepted_lines, result.rejected_lines), (2, 1, 1))
        self.assertEqual(result.commits, 1)

    def test_utf16_big_endian_bom_and_unicode(self):
        statement = "INSERT INTO [dbo].[Sintetico] ([nome]) VALUES (N'ação 漢字')"
        payload = codecs.BOM_UTF16_BE + (statement + "\n").encode("utf-16-be")
        result = self.reader.observe_bytes(payload)
        self.assertEqual(result.statements, (statement,))
        self.assertEqual((result.total_lines, result.accepted_lines, result.rejected_lines), (1, 1, 0))
        self.assertEqual(result.commits, 1)

    def test_utf16_without_bom_is_handled_as_failure(self):
        result = self.reader.observe_bytes(INSERT.encode("utf-16-le"))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.statements, ())
        self.assertEqual(result.commits, 0)
        self.assertRegex(result.output, r"leitura do arquivo: Unicode(?:Decode)?Error\.")

    def test_raw_unicode_bom_in_text_is_not_removed(self):
        self.assert_observation("\ufeff" + INSERT, [], 1, 0, code=1)

    def test_mixed_batch_counts_lines_and_identity_as_insertions(self):
        lines = [IDENTITY_ON, INSERT, "GO", "-- sintético", "", INSERT + ";", INSERT.lower(), IDENTITY_OFF]
        self.assert_observation("\n".join(lines) + "\n", [IDENTITY_ON, INSERT, INSERT + ";", INSERT.lower(), IDENTITY_OFF], 8, 3)

    def test_multiple_statements_on_one_line_are_separate_commands(self):
        combined = INSERT + "; " + INSERT
        self.assert_observation(combined + "\n", [INSERT + ";", INSERT], 1, 0)

    def test_repeated_lines_are_counted_individually(self):
        self.assert_observation((INSERT + "\n") * 3, [INSERT] * 3, 3, 0)

    def test_commit_is_recorded_for_every_accepted_line(self):
        self.assert_observation(IDENTITY_ON + "\nGO\n" + INSERT + "\n" + IDENTITY_OFF, [IDENTITY_ON, INSERT, IDENTITY_OFF], 4, 1)

    def test_logging_enabled_does_not_change_classification(self):
        text = INSERT + "\nGO\n"
        silent = self.reader.observe_text(text)
        verbose = self.reader.observe_text(text, log="S")
        self.assertEqual(verbose.statements, silent.statements)
        self.assertEqual(verbose.commits, silent.commits)
        self.assertEqual(verbose.total_lines, silent.total_lines)
        self.assertEqual(verbose.accepted_lines, silent.accepted_lines)
        self.assertEqual(verbose.rejected_lines, silent.rejected_lines)
        self.assertEqual(silent.messages, ())
        self.assertEqual(verbose.messages, ("Executando a linha -> 1.", "Executando a linha -> 2."))


if __name__ == "__main__":
    unittest.main()
