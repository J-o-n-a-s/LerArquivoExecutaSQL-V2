"""R3: contrato restrito de SET, com dados e recursos exclusivamente sintéticos."""

import codecs
import io
import unittest
from unittest.mock import patch

from sql_classifier import MAX_BATCH_CHARS, MAX_LINE_CHARS, MAX_STATEMENT_CHARS, SQLClassificationError, SQLCommand, SQLCommandReader
from tests._application_harness import CloseError, ExecuteError, PRIVATE, RollbackError, Scenario


DESTINATION = "DestinoSessaoSintetico"
EXTERNAL = "ExternoSessaoSintetico"
OPTIONS = ("ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_PADDING")
INSERT = f"INSERT INTO [dbo].[SessaoSintetica] VALUES (N'{PRIVATE}')"


def setting(option, value="ON"):
    return f"SET {option} {value}"


class SessionSettingsTests(unittest.TestCase):
    def closed(self, scenario):
        self.assertEqual((scenario.cursor.close_attempts, scenario.connection.close_attempts, scenario.file.close_attempts), (1, 1, 1))

    def private(self, result):
        for secret in (PRIVATE, DESTINATION, EXTERNAL, "SessaoSintetica"):
            self.assertNotIn(secret, result.output)

    def run_sql(self, text, expected=(), *, refused=False, payload=None):
        scenario = Scenario(text=text, payload=payload, target_database=DESTINATION)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1 if refused else 0, result.output)
        self.assertEqual(scenario.cursor.statements, list(expected))
        self.assertEqual(scenario.confirmed, list(expected))
        self.assertEqual(scenario.connection.commit_attempts, len(expected))
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertEqual(scenario.connect.call_count, 1)
        self.assertIn(f"Database={DESTINATION};", scenario.connect.call_args.args[0])
        self.assertEqual(result.namespace["database"], DESTINATION)
        self.closed(scenario)
        self.private(result)
        if refused:
            self.assertIn("SQLClassificationError", result.output)
            self.assertNotIn("Processo finalizado", result.output)
        return scenario, result

    def reader(self, text, *, target=DESTINATION):
        return SQLCommandReader(io.StringIO(text), target_database=target)

    def failure(self, **options):
        text = setting("QUOTED_IDENTIFIER")
        scenario = Scenario(text=text + "\nGO\n" + INSERT, target_database=DESTINATION, **options)
        # Modelo explícito de um SET já aplicado antes de uma falha do driver.
        # Rollback transacional não modifica esse efeito de sessão simulado.
        effects = {}
        original = scenario.cursor.execute

        def execute(statement):
            if statement == text:
                effects["QUOTED_IDENTIFIER"] = "ON"
            return original(statement)

        scenario.cursor.execute = execute
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.closed(scenario)
        self.private(result)
        return scenario, result, effects

    def test_ansi_nulls_on_is_executable(self):
        self.run_sql(setting("ANSI_NULLS"), [setting("ANSI_NULLS")])

    def test_ansi_nulls_off_is_refused(self):
        self.run_sql(setting("ANSI_NULLS", "OFF"), refused=True)

    def test_quoted_identifier_on_is_executable(self):
        self.run_sql(setting("QUOTED_IDENTIFIER"), [setting("QUOTED_IDENTIFIER")])

    def test_quoted_identifier_off_is_refused(self):
        self.run_sql(setting("QUOTED_IDENTIFIER", "OFF"), refused=True)

    def test_ansi_padding_on_is_executable(self):
        self.run_sql(setting("ANSI_PADDING"), [setting("ANSI_PADDING")])

    def test_ansi_padding_off_is_refused(self):
        self.run_sql(setting("ANSI_PADDING", "OFF"), refused=True)

    def test_keyword_and_option_case_are_preserved(self):
        for option in OPTIONS:
            for text in (setting(option).lower(), f"sEt {option.title()} oN"):
                with self.subTest(text=text):
                    self.run_sql(text, [text])

    def test_optional_semicolon(self):
        for option in OPTIONS:
            with self.subTest(option=option):
                text = setting(option) + ";"
                self.run_sql(text, [text])

    def test_comments_between_tokens_are_preserved(self):
        for option in OPTIONS:
            with self.subTest(option=option):
                text = f"SET /* uma */ {option} /* duas */ ON /* fim */;"
                self.run_sql(text, [text])

    def test_multiline_setting_and_line_comments(self):
        text = "SET -- opção\nQUOTED_IDENTIFIER\n/* valor */ ON;"
        self.run_sql(text, [text])

    def test_isolated_settings_with_go(self):
        commands = [setting(option) for option in OPTIONS]
        self.run_sql("\nGO\n".join(commands) + "\nGO\n" + INSERT, commands + [INSERT])

    def test_setting_before_insert_in_same_batch(self):
        text = setting("ANSI_NULLS")
        self.run_sql(text + "\n" + INSERT, [text, INSERT])

    def test_setting_after_insert_in_same_batch_keeps_order(self):
        text = setting("ANSI_PADDING")
        self.run_sql(INSERT + ";\n" + text, [INSERT + ";", text])

    def test_setting_then_ddl_refuses_entire_batch(self):
        self.run_sql(setting("ANSI_NULLS") + ";\nCREATE TABLE [t] ([v] INT)", refused=True)

    def test_ddl_then_setting_refuses_entire_batch(self):
        for ddl in ("CREATE TABLE [t] ([v] INT)", "ALTER TABLE [t] ADD [v] INT"):
            with self.subTest(ddl=ddl):
                self.run_sql(ddl + "\n" + setting("ANSI_NULLS"), refused=True)

    def test_setting_and_ddl_in_separate_batches(self):
        text = setting("ANSI_NULLS")
        self.run_sql(text + "\nGO\nCREATE TABLE [t] ([v] INT)\nGO\n" + INSERT, [text, INSERT])

    def test_setting_in_comments_never_changes_requests(self):
        text = "-- SET QUOTED_IDENTIFIER OFF\n/* SET ANSI_NULLS ON */\n" + INSERT
        scenario, result = self.run_sql(text, [INSERT])
        self.assertEqual(result.namespace["leitor_sql"].session_requests, dict.fromkeys(OPTIONS))

    def test_setting_in_literal_is_not_a_command(self):
        text = "INSERT INTO [dbo].[SessaoSintetica] VALUES (N'SET QUOTED_IDENTIFIER OFF; GO')"
        _, result = self.run_sql(text, [text])
        self.assertEqual(result.namespace["leitor_sql"].session_requests, dict.fromkeys(OPTIONS))

    def test_setting_in_procedure_body_is_opaque(self):
        text = "CREATE PROC [dbo].[p] AS SET QUOTED_IDENTIFIER OFF;\n" + INSERT + "\nGO\n" + INSERT
        _, result = self.run_sql(text, [INSERT])
        self.assertEqual(result.namespace["leitor_sql"].session_requests, dict.fromkeys(OPTIONS))

    def test_setting_in_trigger_body_is_opaque(self):
        text = "CREATE TRIGGER [dbo].[tr] ON [dbo].[t] AFTER INSERT AS\nSET ANSI_PADDING OFF\n" + INSERT + "\nGO\n" + INSERT
        _, result = self.run_sql(text, [INSERT])
        self.assertEqual(result.namespace["leitor_sql"].session_requests, dict.fromkeys(OPTIONS))

    def test_setting_in_routine_header_is_refused(self):
        self.run_sql("CREATE PROC [dbo].[p]\nSET ANSI_NULLS ON\nAS SELECT 1\nGO\n" + INSERT, refused=True)

    def test_initial_requests_are_unknown_not_driver_defaults(self):
        reader = self.reader("")
        self.assertEqual(reader.session_requests, dict.fromkeys(OPTIONS))
        self.assertEqual(list(reader), [])
        self.assertEqual(reader.session_requests, dict.fromkeys(OPTIONS))

    def test_requests_advance_only_as_commands_are_emitted(self):
        reader = self.reader("\n".join(setting(option) for option in OPTIONS))
        commands = iter(reader)
        self.assertEqual(reader.session_requests, dict.fromkeys(OPTIONS))
        for index, option in enumerate(OPTIONS):
            self.assertEqual(next(commands).text, setting(option))
            self.assertEqual(reader.session_requests, {name: "ON" if i <= index else None for i, name in enumerate(OPTIONS)})
        self.assertEqual(list(commands), [])

    def test_invalid_batch_does_not_publish_session_requests(self):
        reader = self.reader(setting("ANSI_NULLS") + "\nEXEC [p]")
        with self.assertRaises(SQLClassificationError):
            next(iter(reader))
        self.assertEqual(reader.session_requests, dict.fromkeys(OPTIONS))
        self.assertEqual(reader.context_state, "invalid")

    def test_requests_persist_between_go_batches(self):
        reader = self.reader(setting("QUOTED_IDENTIFIER") + "\nGO\n" + INSERT + "\nGO\n")
        self.assertEqual([c.text for c in reader], [setting("QUOTED_IDENTIFIER"), INSERT])
        self.assertEqual(reader.session_requests["QUOTED_IDENTIFIER"], "ON")

    def test_setting_in_matching_use_context(self):
        text = setting("ANSI_NULLS")
        self.run_sql(f"USE [{DESTINATION}]\nGO\n{text}\nGO\n{INSERT}", [text, INSERT])

    def test_on_settings_in_external_context_are_refused(self):
        for option in OPTIONS:
            with self.subTest(option=option):
                self.run_sql(f"USE [{EXTERNAL}]\nGO\n{setting(option)}", refused=True)

    def test_off_settings_in_external_context_are_refused(self):
        for option in OPTIONS:
            with self.subTest(option=option):
                self.run_sql(f"USE [{EXTERNAL}]\nGO\n{setting(option, 'OFF')}", refused=True)

    def test_external_to_target_before_settings_is_allowed(self):
        text = setting("ANSI_PADDING")
        self.run_sql(f"USE [{EXTERNAL}]\nGO\nUSE [{DESTINATION}]\nGO\n{text}\nGO\n{INSERT}", [text, INSERT])

    def test_use_does_not_reset_or_replay_requests(self):
        text = setting("QUOTED_IDENTIFIER")
        _, result = self.run_sql(f"{text}\nGO\nUSE [{EXTERNAL}]\nGO\nUSE [{DESTINATION}]\nGO\n{INSERT}", [text, INSERT])
        self.assertEqual(result.namespace["leitor_sql"].session_requests["QUOTED_IDENTIFIER"], "ON")

    def test_missing_destination_refuses_session_before_read(self):
        reader = self.reader(setting("ANSI_NULLS"), target=None)
        with self.assertRaises(SQLClassificationError):
            list(reader)
        self.assertEqual(reader.line_number, 0)

    def test_unknown_options_remain_forbidden(self):
        for option in ("NOCOUNT", "ANSI_WARNINGS", "ANSI_NULLS_EXTRA", "QUOTED_IDENTIFIER_EXTRA", "ANSI_PADDING_EXTRA", "DATEFORMAT", "LANGUAGE", "XACT_ABORT", "ARITHABORT"):
            with self.subTest(option=option):
                self.run_sql(setting(option), refused=True)

    def test_values_other_than_on_off_are_refused(self):
        for option in OPTIONS:
            for value in ("", "1", "TRUE", "DEFAULT", "NULL", "ONWARD", "OFFLINE", "'ON'", "[ON]", "\"ON\"", "(ON)", "@valor"):
                with self.subTest(option=option, value=value):
                    self.run_sql(setting(option, value), refused=True)

    def test_delimited_or_qualified_options_are_refused(self):
        for option in ("[ANSI_NULLS]", '"QUOTED_IDENTIFIER"', "dbo.ANSI_PADDING"):
            with self.subTest(option=option):
                self.run_sql(setting(option), refused=True)

    def test_multiple_option_syntax_is_not_generic_set(self):
        for text in ("SET ANSI_NULLS, ANSI_PADDING ON", "SET ANSI_NULLS ON, ANSI_PADDING ON", "SET ANSI_NULLS = ON"):
            with self.subTest(text=text):
                self.run_sql(text, refused=True)

    def test_repeated_on_is_not_deduplicated(self):
        for option in OPTIONS:
            with self.subTest(option=option):
                text = setting(option)
                self.run_sql(text + "\nGO\n" + text, [text, text])

    def test_on_then_off_refuses_whole_batch(self):
        for option in OPTIONS:
            with self.subTest(option=option):
                self.run_sql(setting(option) + "\n" + setting(option, "OFF") + "\n" + INSERT, refused=True)

    def test_off_then_on_refuses_whole_batch(self):
        self.run_sql(setting("QUOTED_IDENTIFIER", "OFF") + "\n" + setting("QUOTED_IDENTIFIER"), refused=True)

    def test_prior_on_remains_confirmed_after_off_in_later_batch(self):
        text = setting("ANSI_NULLS")
        _, result = self.run_sql(text + "\nGO\n" + setting("ANSI_NULLS", "OFF"), [text], refused=True)
        self.assertIn("Importação parcialmente concluída", result.output)
        self.assertIn("Comandos confirmados: 1", result.output)

    def test_setting_order_is_preserved_without_reordering(self):
        commands = [setting("ANSI_PADDING"), setting("QUOTED_IDENTIFIER"), setting("ANSI_NULLS"), INSERT]
        self.run_sql(";\n".join(commands), [text + ";" for text in commands[:-1]] + commands[-1:])

    def test_quoted_identifier_on_precedes_double_quoted_target(self):
        text = 'INSERT INTO "dbo"."SessaoSintetica" ("v""x") VALUES (1)'
        self.run_sql(setting("QUOTED_IDENTIFIER") + "\n" + text, [setting("QUOTED_IDENTIFIER"), text])

    def test_quoted_identifier_off_never_reinterprets_double_quotes(self):
        text = 'INSERT INTO "dbo"."SessaoSintetica" VALUES (1)'
        self.run_sql(setting("QUOTED_IDENTIFIER", "OFF") + "\n" + text, refused=True)

    def test_double_quoted_strings_do_not_become_scalars(self):
        self.run_sql(setting("QUOTED_IDENTIFIER") + '\nINSERT INTO [t] VALUES ("texto")', refused=True)

    def test_unknown_initial_quote_state_preserves_explicit_legacy_precondition(self):
        text = 'INSERT INTO "dbo"."SessaoSintetica" VALUES (1)'
        _, result = self.run_sql(text, [text])
        self.assertIsNone(result.namespace["leitor_sql"].session_requests["QUOTED_IDENTIFIER"])

    def test_unicode_literals_and_escapes_are_preserved(self):
        text = "INSERT INTO [t] VALUES (N'ÿþ 漢字 ''SET ANSI_PADDING OFF''; GO')"
        self.run_sql(setting("ANSI_PADDING") + "\n" + text, [setting("ANSI_PADDING"), text])

    def test_settings_and_identity_keep_execute_commit_order(self):
        commands = [setting("ANSI_NULLS"), "SET IDENTITY_INSERT [dbo].[SessaoSintetica] ON", INSERT, "SET IDENTITY_INSERT [dbo].[SessaoSintetica] OFF"]
        scenario, _ = self.run_sql("\n".join(commands), commands)
        self.assertEqual(scenario.events, ["open", "connect", "cursor"] + [event for _ in commands for event in ("execute", "commit")] + ["cursor.close", "connection.close", "file.close"])

    def test_unknown_command_after_set_prevents_every_execute(self):
        self.run_sql(setting("ANSI_NULLS") + "\n" + INSERT + "\nEXEC [p]", refused=True)

    def test_foreign_target_after_set_refuses_whole_batch(self):
        self.run_sql(setting("QUOTED_IDENTIFIER") + f"\nINSERT INTO [{EXTERNAL}].[dbo].[t] VALUES (1)", refused=True)

    def test_foreign_identity_after_set_refuses_whole_batch(self):
        self.run_sql(setting("ANSI_NULLS") + f"\nSET IDENTITY_INSERT [{EXTERNAL}].[dbo].[t] ON", refused=True)

    def test_use_after_set_in_same_batch_remains_forbidden(self):
        self.run_sql(setting("ANSI_NULLS") + f"\nUSE [{DESTINATION}]", refused=True)

    def test_use_before_set_in_same_batch_remains_forbidden(self):
        self.run_sql(f"USE [{DESTINATION}]\n" + setting("ANSI_NULLS"), refused=True)

    def test_cast_temporal_remains_verbatim_after_settings(self):
        text = "INSERT INTO [t] VALUES (CAST(N'2001-02-03' AS DATE), CAST('04:05:06' AS TIME(7)), CAST('2001-02-03T04:05:06' AS DATETIME2(0)))"
        self.run_sql(setting("ANSI_NULLS") + "\n" + text, [setting("ANSI_NULLS"), text])

    def test_invalid_cast_after_set_refuses_entire_batch(self):
        self.run_sql(setting("ANSI_NULLS") + "\nINSERT INTO [t] VALUES (CAST(GETDATE() AS DATE))", refused=True)

    def test_utf16_bom_endianness(self):
        commands = [setting(option) for option in OPTIONS] + [INSERT]
        text = "\r\nGO\r\n".join(commands)
        for bom, encoding in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(encoding=encoding):
                self.run_sql("", commands, payload=bom + text.encode(encoding))

    def test_utf16_without_bom_is_refused(self):
        scenario = Scenario(payload=setting("ANSI_NULLS").encode("utf-16-le"), target_database=DESTINATION)
        result = scenario.run()
        self.assertEqual(result.exit_code, 1)
        self.assertIsNone(result.uncaught)
        self.assertEqual(scenario.cursor.statements, [])
        self.closed(scenario)
        self.private(result)

    def test_setting_line_limit_is_preserved(self):
        self.run_sql("SET " + " " * MAX_LINE_CHARS + "ANSI_NULLS ON", refused=True)

    def test_setting_statement_limit_is_preserved(self):
        self.run_sql("SET\n" + ("/*" + "x" * 8192 + "*/\n") * (MAX_STATEMENT_CHARS // 8192 + 1) + "ANSI_NULLS ON", refused=True)

    def test_setting_batch_limit_is_preserved(self):
        text = setting("ANSI_NULLS") + "\n"
        self.run_sql(text * (MAX_BATCH_CHARS // len(text) + 1), refused=True)

    def test_setting_comment_depth_limit_is_preserved(self):
        self.run_sql("SET " + "/*" * 33 + "*/" * 33 + "ANSI_NULLS ON", refused=True)

    def test_unterminated_comment_never_releases_set(self):
        self.run_sql(setting("ANSI_NULLS") + " /* " + PRIVATE, refused=True)

    def test_incremental_setting_stream_does_not_read_next_batch(self):
        class Stream:
            def __init__(self):
                self.lines = iter((setting("ANSI_NULLS") + "\n", "GO\n", INSERT + "\n", "GO\n"))
                self.calls = 0
                self.allowed = 2

            def readline(self, size):
                if not 0 < size <= MAX_LINE_CHARS + 1:
                    raise AssertionError("Leitura deve ser limitada.")
                self.calls += 1
                if self.calls > self.allowed:
                    raise AssertionError("Leitura antecipada de lote futuro.")
                return next(self.lines, "")

        stream = Stream()
        reader = SQLCommandReader(stream, target_database=DESTINATION)
        commands = iter(reader)
        self.assertEqual(next(commands).text, setting("ANSI_NULLS"))
        self.assertEqual(stream.calls, 2)
        stream.allowed = 5
        self.assertEqual(next(commands).text, INSERT)
        self.assertEqual(list(commands), [])

    def test_large_stream_retains_only_three_requests(self):
        class Stream:
            def __init__(self):
                self.lines = (line for _ in range(3000) for line in (setting("ANSI_NULLS") + "\n", "GO\n"))

            def readline(self, size):
                if not 0 < size <= MAX_LINE_CHARS + 1:
                    raise AssertionError("Leitura sem limite.")
                return next(self.lines, "")

        reader = SQLCommandReader(Stream(), target_database=DESTINATION)
        count = 0
        for command in reader:
            self.assertEqual(command.kind, "SESSION_SET")
            count += 1
        self.assertEqual(count, 3000)
        self.assertEqual(reader.session_requests, {"ANSI_NULLS": "ON", "QUOTED_IDENTIFIER": None, "ANSI_PADDING": None})

    def test_session_kind_and_line_numbers_differ_from_identity(self):
        reader = self.reader("-- início\nSET ANSI_NULLS ON;\nSET IDENTITY_INSERT [t] ON")
        self.assertEqual([(c.kind, c.line) for c in reader], [("SESSION_SET", 2), ("IDENTITY_INSERT", 3)])

    def test_requests_property_returns_independent_snapshot(self):
        reader = self.reader(setting("ANSI_NULLS"))
        state = reader.session_requests
        state["QUOTED_IDENTIFIER"] = "ON"
        self.assertIsNone(reader.session_requests["QUOTED_IDENTIFIER"])
        list(reader)
        self.assertIsNone(state["ANSI_NULLS"])

    def test_session_execute_failure_is_unknown_even_after_rollback(self):
        scenario, result, effects = self.failure(execute_error_at=1)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.cursor.statements, [setting("QUOTED_IDENTIFIER")])
        self.assertEqual(scenario.connection.commit_attempts, 0)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertEqual(effects, {"QUOTED_IDENTIFIER": "ON"})
        self.assertIn("Estado da configuração de sessão desconhecido", result.output)
        self.assertNotIn("revertido por rollback", result.output)
        self.assertIn("ExecuteError", result.output)
        self.assertIn("Não repetir automaticamente", result.output)

    def test_executor_does_not_claim_rollback_undid_session_set(self):
        # Isolar o contrato do executor para demonstrar o diagnóstico incorreto
        # antes de a gramática nova permitir chegar até execute de SESSION_SET.
        class ClassifiedSession:
            ignored_lines = 0

            def __iter__(self):
                yield SQLCommand(setting("QUOTED_IDENTIFIER"), 1, "SESSION_SET")

        scenario = Scenario(execute_error_at=1, target_database=DESTINATION)
        with patch("sql_classifier.SQLCommandReader", return_value=ClassifiedSession()):
            result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.cursor.statements, [setting("QUOTED_IDENTIFIER")])
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertNotIn("revertido por rollback", result.output)
        self.assertIn("Estado da configuração de sessão desconhecido", result.output)
        self.closed(scenario)
        self.private(result)

    def test_session_commit_failure_preserves_unknown_confirmation(self):
        scenario, result, effects = self.failure(commit_error_at=1)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.connection.commit_attempts, 1)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertEqual(len(scenario.cursor.statements), 1)
        self.assertEqual(effects, {"QUOTED_IDENTIFIER": "ON"})
        self.assertIn("CommitError", result.output)
        self.assertIn("Resultado de confirmação desconhecido", result.output)
        self.assertIn("Estado da configuração de sessão desconhecido", result.output)

    def test_session_commit_applied_before_error_is_not_repeated(self):
        scenario, result, _ = self.failure(commit_error_at=1, commit_applied_before_error=True)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.confirmed, [setting("QUOTED_IDENTIFIER")])
        self.assertEqual(scenario.cursor.statements, [setting("QUOTED_IDENTIFIER")])
        self.assertIn("Comandos confirmados: 0", result.output)
        self.assertIn("Não repetir automaticamente", result.output)

    def test_session_rollback_failure_preserves_original_error(self):
        scenario, result, _ = self.failure(execute_error_at=1, rollback_error=RollbackError(PRIVATE))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("Estado da configuração de sessão desconhecido", result.output)
        self.assertLess(result.output.index("ExecuteError"), result.output.index("RollbackError"))

    def test_session_keyboard_interrupt_during_execute(self):
        scenario, result, _ = self.failure(execute_error_at=1, execute_error=KeyboardInterrupt(PRIVATE))
        self.assertEqual(result.exit_code, 130)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("Estado da configuração de sessão desconhecido", result.output)
        self.assertNotIn("revertido por rollback", result.output)

    def test_session_keyboard_interrupt_during_commit(self):
        scenario, result, _ = self.failure(commit_error_at=1, commit_error=KeyboardInterrupt(PRIVATE))
        self.assertEqual(result.exit_code, 130)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("Resultado de confirmação desconhecido", result.output)
        self.assertIn("Estado da configuração de sessão desconhecido", result.output)

    def test_data_failure_after_session_set_does_not_restore_session(self):
        scenario, result, effects = self.failure(execute_error_at=2)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(effects, {"QUOTED_IDENTIFIER": "ON"})
        self.assertEqual(scenario.confirmed, [setting("QUOTED_IDENTIFIER")])
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("Comando da linha 3 revertido por rollback", result.output)
        self.assertIn("Importação parcialmente concluída", result.output)

    def test_secondary_cleanup_errors_do_not_mask_session_failure(self):
        scenario, result, _ = self.failure(execute_error_at=1, rollback_error=RollbackError(PRIVATE), cursor_close_error=CloseError(PRIVATE), connection_close_error=CloseError(PRIVATE), file_close_error=CloseError(PRIVATE))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.output.count("CloseError"), 3)
        self.assertLess(result.output.index("ExecuteError"), result.output.index("RollbackError"))
        self.assertIn("Estado da configuração de sessão desconhecido", result.output)

    def test_read_failure_after_confirmed_setting_has_partial_diagnostic(self):
        scenario = Scenario(text=setting("ANSI_NULLS") + "\nGO\n", target_database=DESTINATION, read_error_after=2)
        result = scenario.run()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.confirmed, [setting("ANSI_NULLS")])
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertIn("Importação parcialmente concluída", result.output)
        self.closed(scenario)
        self.private(result)

    def test_session_error_with_sql_and_credentials_is_redacted(self):
        _, result, _ = self.failure(execute_error_at=1, execute_error=ExecuteError(f"PWD={PRIVATE}; {INSERT}; {EXTERNAL}"))
        self.assertEqual(result.exit_code, 1)
        self.assertIn("ExecuteError", result.output)


if __name__ == "__main__":
    unittest.main()
