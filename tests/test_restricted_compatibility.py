"""R5B-1: três extensões fechadas, somente dados sintéticos e cursor falso."""

import codecs
import gc
import io
import tracemalloc
import unittest

from sql_classifier import (
    MAX_BATCH_CHARS, MAX_LINE_CHARS, MAX_STATEMENT_CHARS,
    SQLClassificationError, SQLCommandReader,
)
from tests._application_harness import PRIVATE, Scenario


DESTINATION = "DestinoR5Sintetico"
EXTERNAL = "ExternoR5Sintetico"
ZWSP = "\u200b"
INDEX = "CREATE NONCLUSTERED INDEX [ixR5] ON [dbo].[TabelaR5] ([v] ASC)"
OPTION = "OPTIMIZE_FOR_SEQUENTIAL_KEY"
INDEX_OFF = INDEX + " WITH (" + OPTION + "=OFF)"
INSERT = f"INSERT INTO [dbo].[DadosR5] VALUES (N'{PRIVATE}')"
PROC = ZWSP * 2 + "CREATE PROCEDURE [dbo].[ProcR5] AS\n" + INSERT
LAYOUTS = ("MS_DiagramPane1", "MS_DiagramPane2")


def params(name="MS_DiagramPane1", value=None):
    if value is None:
        value = "1" if name == "MS_DiagramPaneCount" else f"N'{PRIVATE}'"
    return {"name": "N'" + name + "'", "value": value,
            "level0type": "N'SCHEMA'", "level0name": "N'dbo'",
            "level1type": "N'VIEW'", "level1name": "N'ViewR5'"}


def prop(arguments=None, procedure="sys.sp_addextendedproperty", keyword="EXEC"):
    arguments = params() if arguments is None else arguments
    return keyword + " " + procedure + " " + ", ".join("@" + k + "=" + v for k, v in arguments.items())


def administration():
    return (INDEX_OFF, PROC, prop(), prop(params("MS_DiagramPane2")), prop(params("MS_DiagramPaneCount")))


class CompatibilityAssertions(unittest.TestCase):
    def observe(self, text, expected=(), *, refused=False, payload=None):
        scenario = Scenario(text=text, payload=payload, target_database=DESTINATION)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1 if refused else 0, result.output)
        self.assertEqual(scenario.cursor.statements, list(expected))
        self.assertEqual(scenario.confirmed, list(expected))
        self.assertEqual(scenario.connection.commit_attempts, len(expected))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertEqual((scenario.file.close_attempts, scenario.cursor.close_attempts, scenario.connection.close_attempts), (1, 1, 1))
        self.assert_private(result)
        if refused:
            self.assertIn("SQLClassificationError", result.output)
        return scenario, result

    def assert_private(self, result):
        for secret in (PRIVATE, DESTINATION, EXTERNAL, "TabelaR5", "DadosR5", "ProcR5", "ViewR5", "ixR5"):
            self.assertNotIn(secret, result.output)

    def refuse(self, text, expected=()):
        return self.observe(text, expected, refused=True)

    def reader(self, text):
        return SQLCommandReader(io.StringIO(text), target_database=DESTINATION)


class CoreCompatibilityTests(CompatibilityAssertions):
    def test_sequential_key_off(self):
        self.observe(INDEX_OFF + "\nGO\n" + INSERT, [INSERT])

    def test_two_initial_zwsp(self):
        self.observe(PROC + "\nGO\n" + INSERT, [INSERT])

    def test_observed_designer_properties(self):
        for name in (*LAYOUTS, "MS_DiagramPaneCount"):
            with self.subTest(name=name):
                self.observe(prop(params(name)) + "\nGO\n" + INSERT, [INSERT])


class IndexCompatibilityTests(CompatibilityAssertions):
    def test_on_remains_refused(self):
        self.refuse(INDEX + f" WITH ({OPTION}=ON)")

    def test_case_comments_and_multiline(self):
        self.observe(INDEX.lower() + "\nWiTh (optimize_for_sequential_key /* opção */ = -- valor\noFf);")

    def test_all_previous_options_can_coexist(self):
        self.observe(INDEX + " WITH (PAD_INDEX=OFF, STATISTICS_NORECOMPUTE=OFF, SORT_IN_TEMPDB=OFF, ONLINE=OFF, ALLOW_ROW_LOCKS=ON, ALLOW_PAGE_LOCKS=ON, DROP_EXISTING=OFF, IGNORE_DUP_KEY=OFF, FILLFACTOR=90, " + OPTION + "=OFF) ON [PRIMARY]")

    def test_repeated_option_is_refused_case_insensitively(self):
        self.refuse(INDEX + f" WITH ({OPTION}=OFF, {OPTION.lower()}=OFF)")

    def test_malformed_values(self):
        for value in ("", "0", "FALSE", "'OFF'", "[OFF]", '"OFF"', "OF", "OFF OFF", "(OFF)", "OFF,", "NULL"):
            with self.subTest(value=value):
                self.refuse(INDEX + f" WITH ({OPTION}={value})")

    def test_expressions_and_variables(self):
        for value in ("@estado", "OFF+1", "CASE WHEN 1=1 THEN OFF END", "OFF; SELECT 1"):
            with self.subTest(value=value):
                self.refuse(INDEX + f" WITH ({OPTION}={value})")

    def test_unknown_options_remain_refused(self):
        for value in ("RESUMABLE=OFF", "MAXDOP=1", "DATA_COMPRESSION=PAGE", OPTION + "_EXTRA=OFF"):
            with self.subTest(value=value):
                self.refuse(INDEX + f" WITH ({OPTION}=OFF, {value})")

    def test_special_indexes_remain_refused(self):
        for kind in ("XML", "SPATIAL", "COLUMNSTORE", "CLUSTERED COLUMNSTORE", "FULLTEXT"):
            with self.subTest(kind=kind):
                self.refuse(INDEX_OFF.replace("NONCLUSTERED", kind))

    def test_include_filter_and_partition_remain_refused(self):
        for suffix in (" INCLUDE ([w])", " WHERE [v]>0", " ON [ps] ([v])"):
            with self.subTest(suffix=suffix):
                self.refuse(INDEX_OFF + suffix)

    def test_additional_statement_invalidates_entire_batch(self):
        for extra in (INSERT, "SET ANSI_NULLS ON", f"USE [{DESTINATION}]", INDEX_OFF, "EXEC [dbo].[p]"):
            with self.subTest(extra=extra):
                self.refuse(INDEX_OFF + ";\n" + extra)
                self.refuse(extra + ";\n" + INDEX_OFF)


class PrefixCompatibilityTests(CompatibilityAssertions):
    def test_zero_one_two_before_proc_and_procedure(self):
        for count in (0, 1, 2):
            for keyword in ("PROC", "PROCEDURE"):
                with self.subTest(count=count, keyword=keyword):
                    self.observe(ZWSP * count + f"CREATE {keyword} [p] AS SELECT 1")

    def test_surrounding_comments_and_whitespace(self):
        self.observe("-- início\n /* " + ZWSP * 8 + " */ " + ZWSP * 2 + " /* separação */ cReAtE\npRoCeDuRe [p] AS SELECT 1")

    def test_pair_separated_by_one_lf_or_crlf(self):
        for separator in ("\n", "\r\n"):
            with self.subTest(separator=repr(separator)):
                text = ZWSP + separator + ZWSP + "CREATE PROC [p] AS SELECT 1\nGO\n" + INSERT
                self.observe(text, [INSERT])
                # Também cobrir CRLF bruto, antes da normalização do harness.
                self.assertEqual([command.text for command in self.reader(text)], [INSERT])

    def test_other_pair_separators_are_refused(self):
        for separator in (" ", "\n\n", "\r\n\r\n", "\t", "/* separação */", "-- separação\n"):
            with self.subTest(separator=separator):
                self.refuse(ZWSP + separator + ZWSP + "CREATE PROC [p] AS SELECT 1")

    def test_three_or_more_refused(self):
        for count in (3, 4, 16):
            with self.subTest(count=count):
                self.refuse(ZWSP * count + "CREATE PROC [p] AS SELECT 1")

    def test_internal_unexpected_prefixes(self):
        for text in ("CRE" + ZWSP * 2 + "ATE PROC [p] AS SELECT 1", "CREATE " + ZWSP * 2 + "PROC [p] AS SELECT 1", "CREATE PROC " + ZWSP * 2 + "[p] AS SELECT 1", "CREATE PROC [p] " + ZWSP * 2 + " AS SELECT 1"):
            with self.subTest(text=text):
                self.refuse(text)

    def test_prefix_does_not_authorize_other_commands(self):
        for text in (INSERT, f"USE [{DESTINATION}]", "SET ANSI_NULLS ON", "SET IDENTITY_INSERT [t] ON", prop(), "GO", "IF 1=1 SELECT 1"):
            with self.subTest(text=text):
                self.refuse(ZWSP * 2 + text)

    def test_prefix_does_not_authorize_other_ddl(self):
        for text in (INDEX_OFF, "CREATE TABLE [t] ([v] INT)", "CREATE VIEW [v] AS SELECT 1 AS [a]", "CREATE FUNCTION [f]() RETURNS INT AS RETURN 1", "CREATE TRIGGER [tr] ON [t] AFTER INSERT AS SELECT 1", "CREATE DATABASE [db]"):
            with self.subTest(text=text):
                self.refuse(ZWSP * 2 + text)

    def test_prefixed_alter_and_create_or_alter_refused(self):
        for text in ("ALTER PROC [p] AS SELECT 1", "CREATE OR ALTER PROC [p] AS SELECT 1"):
            with self.subTest(text=text):
                self.refuse(ZWSP * 2 + text)
                self.observe(text)  # Suporte anterior sem o prefixo permanece.

    def test_incomplete_procedures_refused(self):
        for text in ("", "CREATE", "CREATE PROC", "CREATE PROC [p]", "CREATE PROC [p] AS", "CREATE PROC [p]\n" + INSERT + "\nAS SELECT 1"):
            with self.subTest(text=text):
                self.refuse(ZWSP * 2 + text)

    def test_insert_set_use_in_body_have_no_effect(self):
        routine = PROC + f"; SET ANSI_NULLS OFF; USE [{EXTERNAL}];"
        reader = self.reader(routine + "\nGO\n" + INSERT)
        self.assertEqual([c.text for c in reader], [INSERT])
        self.assertEqual(reader.context_state, "implicit")
        self.assertEqual(reader.session_requests, dict.fromkeys(("ANSI_NULLS", "ANSI_PADDING", "QUOTED_IDENTIFIER")))

    def test_go_inside_strings_comments_and_identifiers(self):
        routine = ZWSP * 2 + "CREATE PROC [p] AS SELECT N'\nGO\n'; /*\nGO\n*/ SELECT [\nGO\n]; -- GO\n" + INSERT
        self.observe(routine + "\nGO\n" + INSERT, [INSERT])

    def test_body_is_opaque_until_eof_even_after_end(self):
        self.observe(ZWSP * 2 + "CREATE PROC [p] AS BEGIN SELECT 1 END;\n" + INSERT)

    def test_prefix_is_not_removed_from_data_literals_or_identifiers(self):
        data = f"INSERT INTO [Dados{ZWSP * 2}R5] VALUES (N'a{ZWSP * 3}b', 'ÿþ', N'c''d')"
        self.observe(PROC + "\nGO\n" + data, [data])

    def test_other_invisible_prefixes_refused(self):
        for char in ("\ufeff", "\u200c", "\u200d"):
            with self.subTest(char=repr(char)):
                self.refuse(ZWSP + char + "CREATE PROC [p] AS SELECT 1")


class DesignerCompatibilityTests(CompatibilityAssertions):
    def test_layout_values_plain_unicode_and_empty_strings(self):
        for name in LAYOUTS:
            for value in ("'layout'", "N'ação 漢字'", "N''"):
                with self.subTest(name=name, value=value):
                    self.observe(prop(params(name, value)))

    def test_count_accepts_only_one_ascii_digit(self):
        for digit in "0123456789":
            with self.subTest(digit=digit):
                self.observe(prop(params("MS_DiagramPaneCount", digit)))

    def test_unobserved_property_names_are_refused(self):
        for name in ("MS_DiagramPane", "MS_DiagramPane0", "MS_DiagramPane3", "MS_DiagramPane4", "MS_DiagramPane99", "MS_Anything", "ms_diagrampane1", "MS_DiagramPane1 ", "MS_DiagramPaneCountX"):
            with self.subTest(name=name):
                self.refuse(prop(params(name)))

    def test_numeric_values_are_for_count_only(self):
        for name in (*LAYOUTS, "MS_Description", "MS_Other"):
            with self.subTest(name=name):
                self.refuse(prop(params(name, "1")))

    def test_count_rejects_other_numeric_formats(self):
        for value in ("10", "01", "+1", "-1", "1.0", "1.", ".1", "1e0", "0x1", "١", "１", "1+0", "(1)", "9" * 5000):
            with self.subTest(value=value[:20]):
                self.refuse(prop(params("MS_DiagramPaneCount", value)))

    def test_count_rejects_strings_null_and_identifiers(self):
        for value in ("'1'", "N'1'", "NULL", "DEFAULT", "[1]", "@count"):
            with self.subTest(value=value):
                self.refuse(prop(params("MS_DiagramPaneCount", value)))

    def test_layout_rejects_null_variables_expressions_and_concatenation(self):
        for value in ("NULL", "@layout", "N'a'+N'b'", "CONCAT(N'a',N'b')", "CAST(N'a' AS NVARCHAR(10))", "1"):
            with self.subTest(value=value):
                self.refuse(prop(params(value=value)))

    def test_every_named_parameter_is_required(self):
        for missing in params():
            with self.subTest(missing=missing):
                arguments = params(); del arguments[missing]
                self.refuse(prop(arguments))

    def test_reordered_parameters_including_numeric_before_name(self):
        for name in (*LAYOUTS, "MS_DiagramPaneCount"):
            arguments = params(name)
            arguments = {"value": arguments.pop("value")} | dict(reversed(list(arguments.items())))
            with self.subTest(name=name):
                self.observe(prop(arguments))

    def test_parameter_names_remain_exact_and_adjacent(self):
        for replacement in ("@NAME", "@ name", "@/* split */name", "@[name]", "@name_extra"):
            with self.subTest(replacement=replacement):
                self.refuse(prop().replace("@name", replacement, 1))

    def test_duplicate_and_unknown_parameters(self):
        for suffix in (", @value=N'outro'", ", @name=N'MS_Description'", ", @unknown=N'x'"):
            with self.subTest(suffix=suffix):
                self.refuse(prop() + suffix)

    def test_only_schema_view_scope(self):
        for key, value in (("level0type", "N'USER'"), ("level0type", "N'schema'"), ("level1type", "N'TABLE'"), ("level1type", "N'view'"), ("level1type", "N'PROCEDURE'")):
            with self.subTest(key=key, value=value):
                arguments = params(); arguments[key] = value
                self.refuse(prop(arguments))

    def test_level2_is_refused_even_for_column(self):
        self.refuse(prop(params() | {"level2type": "N'COLUMN'", "level2name": "N'v'"}))
        self.refuse(prop(params() | {"level2type": "N'COLUMN'"}))

    def test_scope_names_require_nonblank_strings(self):
        for key in ("level0name", "level1name"):
            for value in ("N''", "N'  '", "NULL", "1", "@objeto"):
                with self.subTest(key=key, value=value):
                    arguments = params(); arguments[key] = value
                    self.refuse(prop(arguments))

    def test_identifier_delimiters_escapes_and_unicode(self):
        arguments = params() | {"level0name": "N'schéma'", "level1name": "N'v''漢字]]\"'"}
        self.observe(prop(arguments, procedure='[sys]."sp_addextendedproperty"', keyword="eXeCuTe"))
        for procedure in ('[sy]]s].[sp_addextendedproperty]', '"sy""s".sp_addextendedproperty'):
            with self.subTest(procedure=procedure):
                self.refuse(prop(arguments, procedure=procedure))

    def test_procedure_name_case_and_qualification_are_exact(self):
        for procedure in ("sp_addextendedproperty", "dbo.sp_addextendedproperty", "Sys.sp_addextendedproperty", "sys.SP_ADDEXTENDEDPROPERTY", "[db].sys.sp_addextendedproperty", "sys.sp_addextendedproperty_extra", "sys.sp_updateextendedproperty", "sys.sp_dropextendedproperty"):
            with self.subTest(procedure=procedure):
                self.refuse(prop(procedure=procedure))

    def test_comments_and_keyword_case(self):
        text = prop(keyword="eXeC").replace("=", " /* igual */ = /* valor */ ").replace(", ", ", -- próximo\n")
        self.observe(text + "; -- fim")

    def test_positional_arguments_and_return_assignment_refused(self):
        self.refuse("EXEC sys.sp_addextendedproperty N'MS_DiagramPane1', N'layout'")
        self.refuse("EXEC @r=" + prop()[5:])

    def test_dynamic_exec_and_similar_calls_refused(self):
        for text in ("EXEC(N'" + prop().replace("'", "''") + "')", "EXEC @procedimento", "EXEC sys.sp_executesql N'SELECT 1'"):
            with self.subTest(text=text):
                self.refuse(text)

    def test_commands_inside_layout_string_are_never_interpreted(self):
        value = "N'\nGO\nINSERT; SET ANSI_NULLS OFF; USE [outro]; EXEC(N''x''); " + ZWSP * 3 + "'"
        self.observe(prop(params(value=value)) + "\nGO\n" + INSERT, [INSERT])

    def test_additional_commands_invalidate_entire_batch(self):
        for extra in (INSERT, "SET ANSI_NULLS ON", f"USE [{DESTINATION}]", prop(), "DELETE FROM [t]"):
            with self.subTest(extra=extra):
                self.refuse(prop() + ";\n" + extra)
                self.refuse(extra + ";\n" + prop())

    def test_description_table_and_column_contract_is_preserved(self):
        for column in (False, True):
            for value in ("NULL", "N'descrição'"):
                arguments = params("MS_Description", value) | {"level1type": "N'TABLE'"}
                if column:
                    arguments |= {"level2type": "N'COLUMN'", "level2name": "N'v'"}
                with self.subTest(column=column, value=value):
                    self.observe(prop(arguments))

    def test_description_view_and_numeric_remain_refused(self):
        self.refuse(prop(params("MS_Description")))
        self.refuse(prop(params("MS_Description", "1") | {"level1type": "N'TABLE'"}))


class IntegrationCompatibilityTests(CompatibilityAssertions):
    def test_order_and_no_loss_or_duplicate_insert(self):
        pieces, expected = [], []
        for i, ddl in enumerate(administration()):
            data = f"INSERT INTO [DadosR5] VALUES ({i})"
            pieces.extend((data, ddl)); expected.append(data)
        self.observe("\nGO\n".join(pieces), expected)

    def test_external_metadata_does_not_change_context(self):
        for ddl in administration():
            with self.subTest(ddl=ddl[:40]):
                reader = self.reader(f"USE [{EXTERNAL}]\nGO\n" + ddl)
                self.assertEqual(list(reader), [])
                self.assertEqual(reader.context_state, "external")
                self.observe(f"USE [{EXTERNAL}]\nGO\n{ddl}\nGO\nUSE [{DESTINATION}]\nGO\n{INSERT}", [INSERT])

    def test_external_data_and_session_sets_remain_refused(self):
        for ddl in administration():
            for data in (INSERT, "SET ANSI_NULLS ON", "SET IDENTITY_INSERT [t] ON"):
                with self.subTest(ddl=ddl[:40], data=data):
                    self.refuse(f"USE [{EXTERNAL}]\nGO\n{ddl}\nGO\n{data}")

    def test_external_three_part_target_remains_refused(self):
        self.refuse(INDEX_OFF + f"\nGO\nINSERT INTO [{EXTERNAL}].[dbo].[t] VALUES (1)")

    def test_all_r3_settings_r1_casts_and_identity_remain_in_order(self):
        settings = ["SET " + option + " ON" for option in ("ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_PADDING")]
        identity = "SET IDENTITY_INSERT [DadosR5] ON"
        data = "INSERT INTO [DadosR5] VALUES (CAST(N'2001-02-03' AS DATE), CAST('12:00' AS TIME(7)), CAST(N'2001-02-03' AS DATETIME2(0)))"
        off = "SET IDENTITY_INSERT [DadosR5] OFF"
        text = "\nGO\n".join([f"USE [{DESTINATION}]", *settings, *administration(), identity, data, off])
        self.observe(text, [*settings, identity, data, off])
        reader = self.reader(text); list(reader)
        self.assertEqual(reader.session_requests, {option: "ON" for option in ("ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_PADDING")})
        self.assertEqual(reader.context_state, "target")

    def test_mixed_batch_does_not_record_pending_session_set(self):
        reader = self.reader("SET QUOTED_IDENTIFIER ON;\n" + prop())
        with self.assertRaises(SQLClassificationError):
            list(reader)
        self.assertIsNone(reader.session_requests["QUOTED_IDENTIFIER"])
        self.assertEqual(reader.context_state, "invalid")

    def test_prior_commits_survive_later_refusal(self):
        text = "\nGO\n".join((INSERT, *administration(), INSERT + "; DELETE FROM [t]"))
        _, result = self.refuse(text, [INSERT])
        self.assertIn("Importação parcialmente concluída", result.output)

    def test_execute_commit_and_session_failures_remain_handled(self):
        for option, data, message in (("execute_error_at", INSERT, "revertido por rollback"), ("commit_error_at", INSERT, "Resultado de confirmação desconhecido"), ("execute_error_at", "SET ANSI_NULLS ON", "Estado da configuração de sessão desconhecido")):
            with self.subTest(option=option, data=data):
                text = "\nGO\n".join((*administration(), data, INSERT))
                scenario = Scenario(text=text, target_database=DESTINATION, **{option: 1})
                result = scenario.run()
                self.assertIsNone(result.uncaught)
                self.assertEqual(result.exit_code, 1)
                self.assertEqual(scenario.cursor.statements, [data])
                self.assertEqual(scenario.connection.rollback_attempts, 1)
                self.assertEqual((scenario.file.close_attempts, scenario.cursor.close_attempts, scenario.connection.close_attempts), (1, 1, 1))
                self.assertIn(message, result.output)
                self.assert_private(result)

    def test_read_failure_after_metadata_is_private(self):
        scenario = Scenario(text=INDEX_OFF + "\nGO\n" + INSERT, read_error_after=2, target_database=DESTINATION)
        result = scenario.run()
        self.assertEqual(result.exit_code, 1)
        self.assertIn("OSError", result.output)
        self.assertEqual(scenario.cursor.statements, [])
        self.assert_private(result)

    def test_utf16_both_endiannesses(self):
        text = "\r\nGO\r\n".join((*administration(), INSERT))
        for bom, encoding in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(encoding=encoding):
                self.observe("", [INSERT], payload=bom + text.encode(encoding))

    def test_other_78_families_remain_refused(self):
        for ddl in ("CREATE DATABASE [db]", "ALTER DATABASE [db] SET READ_WRITE", "CREATE VIEW [v] AS SELECT 1 AS [x]", "CREATE USER [u] WITHOUT LOGIN", "ALTER ROLE [r] ADD MEMBER [u]", "IF FULLTEXTSERVICEPROPERTY('IsFullTextInstalled')=1 EXEC sys.sp_fulltext_database @action='enable'"):
            with self.subTest(ddl=ddl):
                self.refuse(ddl)

    def test_reader_cannot_resume_after_bad_metadata(self):
        reader = self.reader(prop() + "; " + INSERT + "\nGO\n" + INSERT)
        for _ in range(2):
            with self.assertRaises(SQLClassificationError):
                list(reader)


class LimitsCompatibilityTests(CompatibilityAssertions):
    def test_line_limit_for_every_new_form(self):
        for ddl in administration():
            with self.subTest(ddl=ddl[:40]):
                _, result = self.refuse(ddl + " " * MAX_LINE_CHARS)
                self.assertIn("Limite de tamanho da linha excedido", result.output)

    def test_batch_limit_for_every_new_form(self):
        padding = ("/*" + "x" * 60000 + "*/\n") * (MAX_BATCH_CHARS // 60000 + 1)
        for ddl in administration():
            with self.subTest(ddl=ddl[:40]):
                _, result = self.refuse(ddl + "\n" + padding)
                self.assertIn("Limite de tamanho do lote excedido", result.output)

    def test_layout_statement_limit(self):
        value = "N'" + ("x" * 8192 + "\n") * (MAX_STATEMENT_CHARS // 8192 + 1) + "'"
        _, result = self.refuse(prop(params(value=value)))
        self.assertIn("Limite de tamanho do comando excedido", result.output)

    def test_count_statement_limit(self):
        text = prop(params("MS_DiagramPaneCount")).replace("@value=1", "@value=\n" + ("/*" + "x" * 8192 + "*/\n") * 33 + "1")
        _, result = self.refuse(text)
        self.assertIn("Limite de tamanho do comando excedido", result.output)

    def test_incomplete_lexical_states(self):
        for ddl in administration():
            for suffix in (" /* aberto", " N'aberto", " [aberto"):
                with self.subTest(ddl=ddl[:40], suffix=suffix):
                    self.refuse(ddl + suffix)

    def test_comment_and_header_depth(self):
        _, result = self.refuse(INDEX_OFF + " /*" * 33 + " */" * 33)
        self.assertIn("Limite de comentários aninhados excedido", result.output)
        _, result = self.refuse(ZWSP * 2 + "CREATE PROC [p] @v " + "(" * 65 + "INT" + ")" * 65 + " AS SELECT 1")
        self.assertIn("Cabeçalho de rotina incompleto ou profundo demais", result.output)

    def test_no_future_batch_read_before_emitting_insert(self):
        lines = [line for ddl in administration() for line in (ddl + "\n", "GO\n")]
        lines += [INSERT + "\n", "GO\n", INSERT + "\n", "GO\n"]
        class Stream:
            calls = 0
            allowed = len(lines) - 2
            def readline(self, size):
                assert 0 < size <= MAX_LINE_CHARS + 1
                assert self.calls < self.allowed
                self.calls += 1
                return lines[self.calls-1] if self.calls <= len(lines) else ""
        stream = Stream()
        commands = iter(SQLCommandReader(stream, target_database=DESTINATION))
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(stream.calls, len(lines)-2)
        stream.allowed = len(lines)+1
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(list(commands), [])

    def test_memory_does_not_grow_with_total_batches(self):
        def measure(repetitions):
            lines = (line for _ in range(repetitions) for ddl in (*administration(), INSERT) for line in (ddl + "\n", "GO\n"))
            class Stream:
                def readline(self, size):
                    assert 0 < size <= MAX_LINE_CHARS + 1
                    return next(lines, "")
            gc.collect()
            tracemalloc.start()
            try:
                count = 0
                for command in SQLCommandReader(Stream(), target_database=DESTINATION):
                    self.assertEqual((command.kind, command.text), ("INSERT", INSERT))
                    count += 1
                self.assertEqual(count, repetitions)
                return tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        small, large = measure(100), measure(1000)
        self.assertLess(large, small + 128 * 1024)


if __name__ == "__main__":
    unittest.main()
