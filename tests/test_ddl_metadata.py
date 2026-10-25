"""R4: descartes estruturais condicionais, nunca DDL/EXEC no executor."""

import codecs
import io
import unittest

from sql_classifier import MAX_BATCH_CHARS, MAX_LINE_CHARS, MAX_STATEMENT_CHARS, SQLClassificationError, SQLCommandReader
from tests._application_harness import PRIVATE, Scenario


DESTINATION = "DestinoR4Sintetico"
EXTERNAL = "ExternoR4Sintetico"
TABLE = "CREATE TABLE [dbo].[PreparacaoR4] ([v] INT)"
INDEX = "CREATE NONCLUSTERED INDEX [ixR4] ON [dbo].[PreparacaoR4] ([v] ASC)"
INSERT = f"INSERT INTO [dbo].[ImportacaoR4] VALUES (N'{PRIVATE}')"
ZWSP = "\u200b"


def property_params(*, column=False):
    params = {"name": "N'MS_Description'", "value": f"N'{PRIVATE}'", "level0type": "N'SCHEMA'", "level0name": "N'dbo'", "level1type": "N'TABLE'", "level1name": "N'PreparacaoR4'"}
    if column:
        params |= {"level2type": "N'COLUMN'", "level2name": "N'v'"}
    return params


def property_call(params=None, *, procedure="sys.sp_addextendedproperty", keyword="EXEC"):
    if params is None:
        params = property_params()
    return keyword + " " + procedure + " " + ", ".join("@" + name + "=" + value for name, value in params.items())


class DDLMetadataTests(unittest.TestCase):
    def observe(self, text, expected=(), *, refused=False, payload=None):
        scenario = Scenario(text=text, payload=payload, target_database=DESTINATION)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1 if refused else 0, result.output)
        self.assertEqual(scenario.cursor.statements, list(expected))
        self.assertEqual(scenario.confirmed, list(expected))
        self.assertEqual(scenario.connection.commit_attempts, len(expected))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertEqual(scenario.connect.call_count, 1)
        self.assertEqual((scenario.cursor.close_attempts, scenario.connection.close_attempts, scenario.file.close_attempts), (1, 1, 1))
        for secret in (PRIVATE, DESTINATION, EXTERNAL, "PreparacaoR4", "ImportacaoR4", "ixR4"):
            self.assertNotIn(secret, result.output)
        if refused:
            self.assertIn("SQLClassificationError", result.output)
            self.assertNotIn("Processo finalizado", result.output)
        return scenario, result

    def refuse(self, text, *, confirmed=()):
        return self.observe(text, confirmed, refused=True)

    def reader(self, text):
        return SQLCommandReader(io.StringIO(text), target_database=DESTINATION)

    def test_original_create_table_without_suffix(self):
        self.observe(TABLE + "\nGO\n" + INSERT, [INSERT])

    def test_create_table_on_primary_at_eof(self):
        self.observe(TABLE + " ON [PRIMARY]")

    def test_create_table_on_and_textimage_on(self):
        self.observe(TABLE + " ON [PRIMARY] TEXTIMAGE_ON [PRIMARY];\nGO\n" + INSERT, [INSERT])

    def test_create_table_textimage_on_alone(self):
        self.observe(TABLE + " TEXTIMAGE_ON [LobSintetico]")

    def test_storage_identifier_forms_and_unicode(self):
        for name in ("PRIMARY", "[Dados漢字]", '[Dados]]R4]', '"Dados""R4"'):
            with self.subTest(name=name):
                self.observe(TABLE + " ON " + name)

    def test_storage_case_comments_and_multiline(self):
        self.observe(TABLE.lower() + "\nOn /* local */ [PRIMARY]\nTeXtImAgE_On -- LOB\n[PRIMARY];")

    def test_unknown_storage_suffixes_are_refused(self):
        for suffix in ("FILESTREAM_ON [PRIMARY]", "WITH (DATA_COMPRESSION = PAGE)", "ON [ps] ([v])", "ON [PRIMARY] WITH (MEMORY_OPTIMIZED = ON)"):
            with self.subTest(suffix=suffix):
                self.refuse(TABLE + " " + suffix)

    def test_storage_requires_single_identifier(self):
        for suffix in ("ON", "TEXTIMAGE_ON", "ON []", "ON 'PRIMARY'", "ON [db].[PRIMARY]", "ON @filegroup"):
            with self.subTest(suffix=suffix):
                self.refuse(TABLE + " " + suffix)

    def test_duplicate_and_reversed_storage_clauses_are_refused(self):
        for suffix in ("ON [PRIMARY] ON [PRIMARY]", "TEXTIMAGE_ON [PRIMARY] ON [PRIMARY]", "ON [PRIMARY] TEXTIMAGE_ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]"):
            with self.subTest(suffix=suffix):
                self.refuse(TABLE + " " + suffix)

    def test_sql_in_table_default_is_never_imported(self):
        self.observe("CREATE TABLE [dbo].[PreparacaoR4] ([v] NVARCHAR(200) DEFAULT N'INSERT; USE; SET; EXEC') ON [PRIMARY]\nGO\n" + INSERT, [INSERT])

    def test_table_suffix_cannot_hide_insert_in_same_batch(self):
        self.refuse(TABLE + " ON [PRIMARY];\n" + INSERT)

    def test_insert_before_table_suffix_refuses_whole_batch(self):
        self.refuse(INSERT + ";\n" + TABLE + " ON [PRIMARY]")

    def test_create_table_rejects_embedded_statements_with_semicolon(self):
        for statement in (
            "INSERT INTO [dbo].[OutraR4] VALUES (1)",
            "DROP TABLE [dbo].[OutraR4]",
        ):
            with self.subTest(statement=statement.split()[0]):
                self.refuse(
                    "CREATE TABLE [dbo].[PreparacaoR4] ([v] INT; "
                    + statement + ")"
                )

    def test_create_table_rejects_embedded_statements_without_semicolon(self):
        for statement in (
            "INSERT INTO [dbo].[OutraR4] VALUES (1)",
            "DROP TABLE [dbo].[OutraR4]",
        ):
            with self.subTest(statement=statement.split()[0]):
                self.refuse(
                    "CREATE TABLE [dbo].[PreparacaoR4] ([v] INT "
                    + statement + ")"
                )

    def test_create_table_preserves_nested_default_check_and_precision(self):
        self.observe(
            "CREATE TABLE [dbo].[PreparacaoR4] ("
            "[nome] NVARCHAR(80) DEFAULT N'INSERT; DROP; GO', "
            "[valor] DECIMAL(10, 2) CHECK (([valor] >= (0))))"
        )

    def test_create_table_does_not_treat_delimited_words_as_statements(self):
        self.observe(
            'CREATE TABLE [dbo].[PreparacaoR4] ('
            '[INSERT; DROP; GO] NVARCHAR(30) DEFAULT N\'DROP; INSERT; GO\', '
            '"DROP TABLE" INT CHECK ("DROP TABLE" >= 0))'
        )

    def test_create_table_does_not_treat_commented_words_as_statements(self):
        self.observe(
            "CREATE TABLE [dbo].[PreparacaoR4] ("
            "[v] INT /* INSERT; DROP TABLE; GO */, "
            "[w] INT -- TRUNCATE TABLE\n"
            "CHECK ([w] >= 0))"
        )

    def test_alter_table_rejects_unseparated_statement_families(self):
        statements = (
            "DROP TABLE [dbo].[OutraR4]",
            "TRUNCATE TABLE [dbo].[OutraR4]",
            "GRANT CONTROL TO [OperadorR4]",
            "REVOKE CONTROL FROM [OperadorR4]",
            "DENY CONTROL TO [OperadorR4]",
            "BACKUP DATABASE [BancoR4] TO DISK=N'arquivo-sintetico'",
            "DBCC CHECKDB",
            "COMMIT",
            "ROLLBACK",
            "WAITFOR DELAY '00:00:01'",
            "DECLARE @valor INT",
            "PRINT N'mensagem-sintetica'",
            "THROW 50000, N'erro-sintetico', 1",
        )
        for statement in statements:
            for separator in ("\n", " "):
                with self.subTest(statement=statement.split()[0], separator=repr(separator)):
                    self.refuse(
                        "ALTER TABLE [dbo].[PreparacaoR4] ADD [x] INT"
                        + separator + statement
                    )

    def test_alter_table_rejects_second_statement_with_semicolon(self):
        for statement in ("DROP TABLE [dbo].[OutraR4]", "COMMIT"):
            with self.subTest(statement=statement.split()[0]):
                self.refuse(
                    "ALTER TABLE [dbo].[PreparacaoR4] ADD [x] INT; "
                    + statement
                )

    def test_alter_table_legitimate_drop_forms_remain_ignored(self):
        for action in (
            "DROP COLUMN [ColunaR4]",
            "DROP CONSTRAINT [RestricaoR4]",
        ):
            with self.subTest(action=action):
                self.observe("ALTER TABLE [dbo].[PreparacaoR4] " + action)

    def test_mixed_ddl_is_fully_validated_before_effect_or_discard(self):
        text = (
            "ALTER TABLE [dbo].[PreparacaoR4] ADD [x] INT\n"
            "DROP TABLE [dbo].[OutraR4]"
        )
        reader = self.reader(text)
        with self.assertRaises(SQLClassificationError) as caught:
            list(reader)
        self.assertEqual(reader.ignored_lines, 0)
        self.assertEqual(
            caught.exception.reason,
            "DDL misturado com outra instrução no mesmo lote.",
        )

        scenario, result = self.refuse(text + " -- " + PRIVATE)
        self.assertEqual(scenario.cursor.statements, [])
        self.assertEqual(scenario.connection.commit_attempts, 0)
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertNotIn(PRIVATE, result.output)

    def test_create_index_without_options_is_ignored(self):
        self.observe(INDEX)

    def test_unique_clustered_index_is_conditionally_ignored(self):
        self.observe("CREATE UNIQUE CLUSTERED INDEX [ixR4] ON [dbo].[PreparacaoR4] ([v] DESC) ON [PRIMARY];")

    def test_index_with_default_type_and_multiple_keys(self):
        self.observe("CREATE INDEX [ixR4] ON [dbo].[PreparacaoR4] ([v], [outro] DESC)")

    def test_index_case_comments_and_escaped_identifiers(self):
        self.observe('cReAtE /* tipo */ NoNcLuStErEd InDeX [ix]]漢字] oN "dbo".[PreparacaoR4] ([v] aSc, [INSERT] dEsC) oN [PRIMARY]')

    def test_index_allowed_boolean_options(self):
        for option in ("PAD_INDEX", "STATISTICS_NORECOMPUTE", "SORT_IN_TEMPDB", "ONLINE", "ALLOW_ROW_LOCKS", "ALLOW_PAGE_LOCKS"):
            for value in ("ON", "OFF"):
                with self.subTest(option=option, value=value):
                    self.observe(INDEX + f" WITH ({option} = {value})")

    def test_index_off_only_options(self):
        self.observe(INDEX + " WITH (DROP_EXISTING=OFF, IGNORE_DUP_KEY=OFF)")

    def test_index_drop_existing_and_ignore_dup_key_on_are_refused(self):
        for option in ("DROP_EXISTING", "IGNORE_DUP_KEY"):
            with self.subTest(option=option):
                self.refuse(INDEX + f" WITH ({option}=ON)")

    def test_index_fillfactor_limits(self):
        for value in ("0", "1", "90", "100"):
            with self.subTest(value=value):
                self.observe(INDEX + f" WITH (FILLFACTOR={value})")

    def test_index_invalid_fillfactor_is_refused(self):
        for value in ("-1", "+1", "101", "1.0", "1e0", "'90'", "@f", "9" * 5000):
            with self.subTest(value=value[:20]):
                _, result = self.refuse(INDEX + f" WITH (FILLFACTOR={value})")
                self.assertIn("FILLFACTOR fora do intervalo inteiro suportado", result.output)

    def test_index_multiple_allowed_options_and_storage(self):
        self.observe(INDEX + " WITH (PAD_INDEX=OFF, STATISTICS_NORECOMPUTE=OFF, SORT_IN_TEMPDB=OFF, DROP_EXISTING=OFF, ONLINE=OFF, ALLOW_ROW_LOCKS=ON, ALLOW_PAGE_LOCKS=ON, FILLFACTOR=90) ON [PRIMARY];")

    def test_index_unknown_option_is_refused(self):
        for option in ("DATA_COMPRESSION=PAGE", "RESUMABLE=ON", "MAXDOP=1", "OPTIMIZE_FOR_SEQUENTIAL_KEY=ON", "DESCONHECIDA=OFF"):
            with self.subTest(option=option):
                self.refuse(INDEX + " WITH (" + option + ")")

    def test_index_duplicate_option_is_refused(self):
        self.refuse(INDEX + " WITH (PAD_INDEX=ON, PAD_INDEX=OFF)")

    def test_index_non_literal_option_value_is_refused(self):
        for value in ("TRUE", "'ON'", "[ON]", "@estado", "ON+1"):
            with self.subTest(value=value):
                self.refuse(INDEX + f" WITH (ONLINE={value})")

    def test_index_filter_include_and_partition_are_refused(self):
        for suffix in ("WHERE [v] > 0", "INCLUDE ([outro])", "ON [ps] ([v])"):
            with self.subTest(suffix=suffix):
                self.refuse(INDEX + " " + suffix)

    def test_index_expression_keys_are_refused(self):
        for keys in ("([v]+1)", "(LOWER([v]))", "()", "([v],)", "(INSERT INTO [t] VALUES (1))"):
            with self.subTest(keys=keys):
                self.refuse("CREATE INDEX [ixR4] ON [dbo].[PreparacaoR4] " + keys)

    def test_special_or_maintenance_indexes_are_refused(self):
        for text in ("CREATE XML INDEX [ixR4] ON [dbo].[PreparacaoR4] ([v])", "CREATE COLUMNSTORE INDEX [ixR4] ON [dbo].[PreparacaoR4] ([v])", "ALTER INDEX [ixR4] ON [dbo].[PreparacaoR4] REBUILD", "DROP INDEX [ixR4] ON [dbo].[PreparacaoR4]"):
            with self.subTest(text=text):
                self.refuse(text)

    def test_index_followed_by_data_is_refused_as_mixed_batch(self):
        self.refuse(INDEX + ";\n" + INSERT)

    def test_multiple_indexes_in_same_batch_are_refused(self):
        self.refuse(INDEX + ";\n" + INDEX)

    def test_create_and_alter_view_remain_refused(self):
        for keyword in ("CREATE", "ALTER", "CREATE OR ALTER"):
            with self.subTest(keyword=keyword):
                self.refuse(keyword + " VIEW [dbo].[vR4] AS SELECT [v] FROM [dbo].[PreparacaoR4]")

    def test_view_with_data_or_dynamic_sql_is_not_silently_ignored(self):
        for body in (INSERT, "SELECT 1;\n" + INSERT, "EXEC(N'SELECT 1')", "WITH [c] AS (SELECT 1 AS [v]) SELECT [v] FROM [c]"):
            with self.subTest(body=body):
                self.refuse("CREATE VIEW [dbo].[vR4] AS\n" + body)

    def test_view_refusal_does_not_release_insert_in_same_batch(self):
        self.refuse(INSERT + ";\nCREATE VIEW [dbo].[vR4] AS SELECT 1 AS [v]")

    def test_users_roles_and_permissions_remain_refused(self):
        for text in ("CREATE USER [uR4] WITHOUT LOGIN", "CREATE ROLE [rR4]", "ALTER USER [uR4] WITH DEFAULT_SCHEMA=[dbo]", "ALTER ROLE [rR4] ADD MEMBER [uR4]", "GRANT SELECT ON [dbo].[PreparacaoR4] TO [uR4]", "DENY INSERT TO [uR4]", "REVOKE SELECT FROM [uR4]", "EXEC sys.sp_addrolemember N'rR4', N'uR4'"):
            with self.subTest(text=text):
                self.refuse(text)

    def test_database_preparation_remains_refused(self):
        for text in (f"CREATE DATABASE [{EXTERNAL}]", f"ALTER DATABASE [{DESTINATION}] SET RECOVERY SIMPLE", f"ALTER DATABASE [{DESTINATION}] SET COMPATIBILITY_LEVEL=150", f"ALTER DATABASE [{DESTINATION}] ADD FILEGROUP [fgR4]"):
            with self.subTest(text=text):
                self.refuse(text)

    def test_database_ddl_in_external_context_is_not_discarded(self):
        self.refuse(f"USE [{EXTERNAL}]\nGO\nALTER DATABASE [{EXTERNAL}] SET READ_WRITE")

    def test_conditional_fulltext_remains_refused(self):
        self.refuse("IF FULLTEXTSERVICEPROPERTY('IsFullTextInstalled')=1 BEGIN EXEC sys.sp_fulltext_database @action='enable' END")

    def test_other_if_and_dynamic_exec_remain_refused(self):
        for text in ("IF 1=1\n" + INSERT, "EXEC(N'" + INSERT.replace("'", "''") + "')", "EXEC sys.sp_executesql N'SELECT 1'"):
            with self.subTest(text=text):
                self.refuse(text)

    def test_description_property_for_table_is_ignored(self):
        self.observe(property_call())

    def test_description_property_for_column_is_ignored(self):
        self.observe(property_call(property_params(column=True)) + ";")

    def test_property_execute_keyword_and_delimiters(self):
        self.observe(property_call(procedure='[sys]."sp_addextendedproperty"', keyword="eXeCuTe"))

    def test_property_named_params_can_change_order(self):
        params = dict(reversed(list(property_params(column=True).items())))
        self.observe(property_call(params))

    def test_property_plain_strings_and_null_value(self):
        params = {name: value[1:] for name, value in property_params().items()}
        params["value"] = "NULL"
        self.observe(property_call(params))

    def test_property_comments_between_tokens(self):
        text = property_call().replace("=", " /* atribuição */ = /* valor */ ").replace(", ", ", -- parâmetro\n")
        self.observe(text)

    def test_property_sql_text_is_only_an_ignored_literal(self):
        params = property_params()
        params["value"] = "N'EXEC(N''INSERT''); USE; SET; GO; " + ZWSP + PRIVATE + "'"
        self.observe(property_call(params) + "\nGO\n" + INSERT, [INSERT])

    def test_only_exact_system_property_procedure_is_accepted(self):
        for procedure in ("sp_addextendedproperty", "dbo.sp_addextendedproperty", "sys.sp_updateextendedproperty", "sys.sp_dropextendedproperty", "Sys.sp_addextendedproperty", "sys.SP_ADDEXTENDEDPROPERTY", "[outro].[sys].[sp_addextendedproperty]", "@procedimento"):
            with self.subTest(procedure=procedure):
                self.refuse(property_call(procedure=procedure))

    def test_only_descriptive_property_name_is_accepted(self):
        for value in ("N'OutraPropriedade'", "N'MS_DiagramPane1'", "N'ms_description'", "NULL", "@nome"):
            with self.subTest(value=value):
                params = property_params(); params["name"] = value
                self.refuse(property_call(params))

    def test_property_requires_all_scope_fields(self):
        for missing in property_params():
            with self.subTest(missing=missing):
                params = property_params(); del params[missing]
                self.refuse(property_call(params))

    def test_property_column_scope_requires_both_fields(self):
        for missing in ("level2type", "level2name"):
            with self.subTest(missing=missing):
                params = property_params(column=True); del params[missing]
                self.refuse(property_call(params))

    def test_property_non_table_or_column_scopes_are_refused(self):
        for name, value in (("level0type", "N'USER'"), ("level1type", "N'VIEW'"), ("level2type", "N'INDEX'")):
            with self.subTest(name=name):
                params = property_params(column=True); params[name] = value
                self.refuse(property_call(params))

    def test_property_scope_names_cannot_be_empty_or_null(self):
        for name in ("level0name", "level1name", "level2name"):
            for value in ("N''", "NULL"):
                with self.subTest(name=name, value=value):
                    params = property_params(column=True); params[name] = value
                    self.refuse(property_call(params))

    def test_property_value_cannot_be_expression_or_variable(self):
        for value in ("@valor", "1", "GETDATE()", "N'a'+N'b'", "CAST(N'a' AS NVARCHAR(10))"):
            with self.subTest(value=value):
                params = property_params(); params["value"] = value
                self.refuse(property_call(params))

    def test_property_unknown_or_duplicate_param_is_refused(self):
        self.refuse(property_call() + ", @desconhecido=N'x'")
        self.refuse(property_call() + ", @value=N'outro'")

    def test_property_param_names_are_exact_and_adjacent(self):
        for replacement in ("@NAME", "@ /* divisão */ name", "@[name]", "@name_extra"):
            with self.subTest(replacement=replacement):
                self.refuse(property_call().replace("@name", replacement, 1))

    def test_property_positional_and_return_assignment_are_refused(self):
        self.refuse("EXEC sys.sp_addextendedproperty N'MS_Description', N'texto'")
        self.refuse("EXEC @resultado = " + property_call()[5:])

    def test_property_followed_by_insert_is_not_silently_swallowed(self):
        self.refuse(property_call() + ";\n" + INSERT)

    def test_insert_then_property_refuses_whole_batch(self):
        self.refuse(INSERT + ";\n" + property_call())

    def test_multiple_property_calls_in_one_batch_are_refused(self):
        self.refuse(property_call() + ";\n" + property_call())

    def test_generic_exec_with_similar_name_is_refused(self):
        self.refuse(property_call(procedure="sys.sp_addextendedproperty_extra"))
        self.refuse("EXEC sys.sp_fulltext_database @action='enable'")

    def test_single_zwsp_before_create_procedure_is_tolerated(self):
        for keyword in ("PROC", "PROCEDURE"):
            with self.subTest(keyword=keyword):
                self.observe(ZWSP + f"CREATE {keyword} [dbo].[pR4] AS\n" + INSERT + "\nGO\n" + INSERT, [INSERT])

    def test_zwsp_prefix_with_surrounding_comments_and_whitespace(self):
        self.observe("-- início\n /* cabeçalho */ " + ZWSP + " /* espaço */ cReAtE\npRoCeDuRe [dbo].[pR4] AS SELECT 1")

    def test_multiple_zwsp_prefixes_are_refused(self):
        # R5B-1 autoriza dois; manter a recusa acima desse limite.
        for count in (3, 4, 16):
            with self.subTest(count=count):
                self.refuse(ZWSP * count + "CREATE PROC [dbo].[pR4] AS SELECT 1")

    def test_zwsp_before_other_statements_cannot_bypass_classifier(self):
        for text in (INSERT, "SET ANSI_NULLS ON", f"USE [{DESTINATION}]", property_call(), "GO", "IF 1=1 SELECT 1", TABLE, INDEX, "ALTER PROC [pR4] AS SELECT 1", "CREATE OR ALTER PROC [pR4] AS SELECT 1", "CREATE TRIGGER [trR4] ON [t] AFTER INSERT AS SELECT 1"):
            with self.subTest(text=text[:35]):
                self.refuse(ZWSP + text)

    def test_zwsp_inside_or_after_keywords_is_refused(self):
        for text in ("CRE" + ZWSP + "ATE PROC [pR4] AS SELECT 1", "CREATE " + ZWSP + "PROCEDURE [pR4] AS SELECT 1", "CREATE PROCEDURE " + ZWSP + "[pR4] AS SELECT 1"):
            with self.subTest(text=text):
                self.refuse(text)

    def test_other_invisible_prefixes_are_refused(self):
        for prefix in ("\ufeff", "\u200c", "\u200d"):
            with self.subTest(prefix=repr(prefix)):
                self.refuse(prefix + "CREATE PROC [pR4] AS SELECT 1")

    def test_zwsp_in_data_identifiers_and_literals_is_preserved(self):
        text = f"INSERT INTO [dbo].[Importacao{ZWSP}R4] ([v{ZWSP}]) VALUES (N'{ZWSP}{PRIVATE}')"
        self.observe(TABLE + " ON [PRIMARY]\nGO\n" + text, [text])

    def test_zwsp_in_comments_is_not_a_prefix_normalization(self):
        self.observe("/* " + ZWSP + " CREATE PROC [p] AS SELECT 1 */\n" + INSERT, [INSERT])

    def test_prefixed_routine_must_have_recognizable_body(self):
        self.refuse(ZWSP + "CREATE PROC [pR4] AS")
        self.refuse(ZWSP + "CREATE PROC [pR4]\n" + INSERT + "\nAS SELECT 1")

    def test_ddl_families_in_external_context_are_only_discarded(self):
        for ddl in (TABLE + " ON [PRIMARY]", INDEX, property_call(), ZWSP + "CREATE PROC [pR4] AS " + INSERT):
            with self.subTest(ddl=ddl[:40]):
                self.observe(f"USE [{EXTERNAL}]\nGO\n{ddl}\nGO\nUSE [{DESTINATION}]\nGO\n{INSERT}", [INSERT])

    def test_external_ddl_does_not_authorize_later_data(self):
        self.refuse(f"USE [{EXTERNAL}]\nGO\n{TABLE} ON [PRIMARY]\nGO\n{INSERT}")

    def test_external_ddl_does_not_authorize_session_set(self):
        self.refuse(f"USE [{EXTERNAL}]\nGO\n{INDEX}\nGO\nSET ANSI_NULLS ON")

    def test_use_mixed_with_ddl_is_refused(self):
        for text in (f"USE [{DESTINATION}];\n{TABLE} ON [PRIMARY]", TABLE + f" ON [PRIMARY];\nUSE [{DESTINATION}]", property_call() + f";\nUSE [{DESTINATION}]"):
            with self.subTest(text=text[:40]):
                self.refuse(text)

    def test_session_set_mixed_with_ddl_is_refused(self):
        for ddl in (TABLE + " ON [PRIMARY]", INDEX, property_call()):
            with self.subTest(ddl=ddl[:40]):
                self.refuse("SET QUOTED_IDENTIFIER ON;\n" + ddl)
                self.refuse(ddl + ";\nSET QUOTED_IDENTIFIER ON")

    def test_session_requests_are_not_changed_by_ignored_sql(self):
        text = ZWSP + "CREATE PROC [pR4] AS SET QUOTED_IDENTIFIER OFF;\nGO\n" + property_call()
        reader = self.reader(text)
        self.assertEqual(list(reader), [])
        self.assertEqual(reader.session_requests, {"ANSI_NULLS": None, "QUOTED_IDENTIFIER": None, "ANSI_PADDING": None})

    def test_session_on_before_ddl_and_cast_remains_executable(self):
        setting = "SET QUOTED_IDENTIFIER ON"
        data = 'INSERT INTO "dbo"."ImportacaoR4" VALUES (CAST(N\'2001-02-03\' AS DATE))'
        self.observe(setting + "\nGO\n" + TABLE + " ON [PRIMARY]\nGO\n" + data, [setting, data])

    def test_foreign_qualified_data_after_ddl_is_refused(self):
        self.refuse(INDEX + f"\nGO\nINSERT INTO [{EXTERNAL}].[dbo].[t] VALUES (1)")

    def test_data_order_without_loss_or_duplication_across_ddl(self):
        first, second = INSERT + ";", INSERT.replace("VALUES", "values")
        text = first + "\nGO\n" + TABLE + " ON [PRIMARY]\nGO\n" + property_call() + "\nGO\n" + INDEX + "\nGO\n" + second
        self.observe(text, [first, second])

    def test_later_unknown_ddl_preserves_prior_commits(self):
        _, result = self.refuse(INSERT + "\nGO\nCREATE VIEW [vR4] AS SELECT 1 AS [v]", confirmed=[INSERT])
        self.assertIn("Importação parcialmente concluída", result.output)
        self.assertIn("Comandos confirmados: 1", result.output)

    def test_later_invalid_storage_preserves_prior_commits(self):
        self.refuse(INSERT + "\nGO\n" + TABLE + " ON [ps] ([v])", confirmed=[INSERT])

    def test_sql_in_ddl_comments_and_strings_is_never_extracted(self):
        self.observe(TABLE + " ON [PRIMARY] -- " + INSERT + "\nGO\n/* " + INDEX + " */\n" + INSERT, [INSERT])

    def test_utf16_bom_endianness_and_unicode(self):
        text = TABLE + " ON [Dados漢字]\r\nGO\r\n" + ZWSP + "CREATE PROC [p漢字] AS SELECT N'ação'\r\nGO\r\n" + INSERT
        for bom, encoding in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(encoding=encoding):
                self.observe("", [INSERT], payload=bom + text.encode(encoding))

    def test_ddl_line_limit_is_preserved(self):
        _, result = self.refuse(TABLE + " ON [" + "x" * MAX_LINE_CHARS + "]")
        self.assertIn("Limite de tamanho da linha excedido", result.output)

    def test_ddl_batch_limit_is_preserved(self):
        _, result = self.refuse(TABLE + " ON [PRIMARY]\n" + ("/*" + "x" * 60000 + "*/\n") * (MAX_BATCH_CHARS // 60000 + 1))
        self.assertIn("Limite de tamanho do lote excedido", result.output)

    def test_metadata_statement_limit_is_enforced(self):
        params = property_params()
        params["value"] = "N'" + ("x" * 8192 + "\n") * (MAX_STATEMENT_CHARS // 8192 + 1) + "'"
        _, result = self.refuse(property_call(params))
        self.assertIn("Limite de tamanho do comando excedido", result.output)

    def test_comment_and_ddl_depth_limits_remain_enforced(self):
        _, result = self.refuse(TABLE + " ON /*" * 33 + "*/" * 33 + " [PRIMARY]")
        self.assertIn("Limite de comentários aninhados excedido", result.output)
        _, result = self.refuse("CREATE TABLE [t] (" + "(" * 64 + "1" + ")" * 65 + ") ON [PRIMARY]")
        self.assertIn("Limite de profundidade estrutural do DDL excedido", result.output)

    def test_incomplete_lexical_states_remain_refused(self):
        for text in (TABLE + " ON [aberto", INDEX + " /* aberto", property_call() + " /* aberto", ZWSP + "CREATE PROC [pR4] AS SELECT N'aberto"):
            with self.subTest(text=text[:40]):
                self.refuse(text)

    def test_reader_does_not_resume_after_ambiguous_metadata(self):
        reader = self.reader(property_call() + "; " + INSERT + "\nGO\n" + INSERT)
        with self.assertRaises(SQLClassificationError):
            list(reader)
        with self.assertRaises(SQLClassificationError):
            list(reader)

    def test_ignored_ddl_does_not_read_past_first_data_batch(self):
        class Stream:
            def __init__(self):
                self.lines = iter((TABLE + " ON [PRIMARY]\n", "GO\n", INSERT + "\n", "GO\n", INSERT + "\n", "GO\n"))
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

    def test_large_ddl_stream_is_incremental_and_never_emits_administration(self):
        class Stream:
            def __init__(self):
                self.lines = (line for _ in range(1000) for line in (TABLE + " ON [PRIMARY]\n", "GO\n", INDEX + "\n", "GO\n", property_call() + "\n", "GO\n", INSERT + "\n", "GO\n"))

            def readline(self, size):
                if not 0 < size <= MAX_LINE_CHARS + 1:
                    raise AssertionError("Leitura sem limite.")
                return next(self.lines, "")

        count = 0
        for command in SQLCommandReader(Stream(), target_database=DESTINATION):
            self.assertEqual(command.text, INSERT)
            self.assertEqual(command.kind, "INSERT")
            count += 1
        self.assertEqual(count, 1000)


if __name__ == "__main__":
    unittest.main()
