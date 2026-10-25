"""Contratos da Fase 2A: erros, transações pendentes e liberação de recursos."""

import contextlib
import io
import unittest

from tests._application_harness import (
    PRIVATE,
    STATEMENT,
    SYNTHETIC_PATH,
    CloseError,
    CommitError,
    DatabaseError,
    ExecuteError,
    RollbackError,
    Scenario,
)


class FailureHandlingTests(unittest.TestCase):
    def assert_handled(self, result, code=1):
        self.assertIsNone(result.uncaught, type(result.uncaught).__name__)
        self.assertEqual(result.exit_code, code)
        self.assertNotIn(PRIVATE, result.output)
        self.assertNotIn(STATEMENT, result.output)
        if code:
            self.assertNotIn("Processo finalizado", result.output)

    def assert_closed(self, scenario, *, cursor=1, connection=1, file=1):
        self.assertEqual(scenario.cursor.close_attempts, cursor)
        self.assertEqual(scenario.connection.close_attempts, connection)
        self.assertEqual(scenario.file.close_attempts, file)

    def assert_connect_error(self, error):
        scenario = Scenario(connect_error=error)
        module = scenario.run(as_main=False)
        self.assert_handled(module, code=0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            connection, failed = module.namespace["_conectar_sql"](
                f"Driver=SINTETICO;UID=usuario;PWD={PRIVATE}"
            )
        self.assertIsNone(connection)
        self.assertTrue(failed)
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertIn("DatabaseError", output.getvalue())

    def test_connection_error_with_empty_args(self):
        self.assert_connect_error(DatabaseError())

    def test_connection_error_with_one_arg(self):
        self.assert_connect_error(DatabaseError(PRIVATE))

    def test_connection_error_with_driver_details_is_redacted(self):
        self.assert_connect_error(DatabaseError("08001", f"PWD={PRIVATE}; SQL={STATEMENT}"))

    def test_connection_failure_closes_only_open_file(self):
        scenario = Scenario(connect_error=DatabaseError("08001", PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario, cursor=0, connection=0)
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertIn("conexão", result.output)

    def test_file_not_found_prevents_connection(self):
        scenario = Scenario(open_error=FileNotFoundError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario, cursor=0, connection=0, file=0)
        scenario.connect.assert_not_called()
        self.assertIn("abertura do arquivo", result.output)

    def test_permission_error_prevents_connection(self):
        scenario = Scenario(open_error=PermissionError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        scenario.connect.assert_not_called()
        self.assertIn("PermissionError", result.output)

    def test_other_open_error_is_handled(self):
        scenario = Scenario(open_error=OSError(PRIVATE))
        self.assert_handled(scenario.run())
        scenario.connect.assert_not_called()

    def test_cursor_creation_failure_closes_acquired_resources(self):
        scenario = Scenario(cursor_error=DatabaseError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario, cursor=0)
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertIn("criação do cursor", result.output)

    def test_read_failure_before_commands_closes_every_resource(self):
        scenario = Scenario(read_error_after=0)
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertEqual(scenario.cursor.statements, [])
        self.assertIn("leitura do arquivo", result.output)

    def test_read_failure_after_commit_reports_partial_result(self):
        # O lote deve estar validado antes do commit; falhar na leitura do próximo.
        scenario = Scenario(text=STATEMENT + "\nGO\n", read_error_after=2)
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertEqual(scenario.confirmed, [STATEMENT])
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertIn("Importação parcialmente concluída", result.output)
        self.assertIn("Comandos confirmados: 1", result.output)

    def test_utf16_without_bom_is_handled(self):
        scenario = Scenario(payload=STATEMENT.encode("utf-16-le"))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        # UnicodeDecodeError (3.13) especializa UnicodeError (3.12).
        self.assertRegex(
            result.output,
            r"(?m)^Falha em leitura do arquivo: Unicode(?:Decode)?Error\.$",
        )
        self.assertIn("leitura do arquivo", result.output)
        self.assertEqual(scenario.cursor.statements, [])
        self.assertEqual(scenario.connection.commit_attempts, 0)

    def test_truncated_utf16_is_handled(self):
        scenario = Scenario(payload=b"\xff\xfe\x41")
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("UnicodeDecodeError", result.output)

    def test_execute_failure_rolls_back_before_closing(self):
        scenario = Scenario(execute_error_at=1)
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertEqual(scenario.connection.commit_attempts, 0)
        self.assertEqual(scenario.pending, [])
        self.assertLess(scenario.events.index("rollback"), scenario.events.index("cursor.close"))
        self.assertIn("revertido por rollback", result.output)
        self.assertIn("execução do comando", result.output)
        self.assertIn("linha 1", result.output)

    def test_prior_commits_survive_later_execute_failure(self):
        scenario = Scenario(text=(STATEMENT + "\n") * 3, execute_error_at=2)
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertEqual(scenario.confirmed, [STATEMENT])
        self.assertEqual(len(scenario.cursor.statements), 2)
        self.assertEqual(scenario.connection.commit_attempts, 1)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("Importação parcialmente concluída", result.output)
        self.assertIn("Comandos confirmados: 1", result.output)
        self.assertIn("linha 2", result.output)

    def test_commit_failure_is_unknown_even_after_successful_rollback(self):
        scenario = Scenario(commit_error_at=1)
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("Resultado de confirmação desconhecido", result.output)
        self.assertNotIn("revertido por rollback", result.output)
        self.assertIn("Comandos confirmados: 0", result.output)

    def test_commit_applied_before_error_is_never_retried(self):
        scenario = Scenario(text=(STATEMENT + "\n") * 2, commit_error_at=1, commit_applied_before_error=True)
        result = scenario.run()
        self.assert_handled(result)
        self.assertEqual(scenario.confirmed, [STATEMENT])
        self.assertEqual(len(scenario.cursor.statements), 1)
        self.assertEqual(scenario.connection.commit_attempts, 1)
        self.assertIn("Resultado de confirmação desconhecido", result.output)
        self.assertIn("Não repetir automaticamente", result.output)
        self.assertIn("Comandos confirmados: 0", result.output)

    def test_later_commit_failure_preserves_known_partial_result(self):
        scenario = Scenario(text=(STATEMENT + "\n") * 3, commit_error_at=2)
        result = scenario.run()
        self.assert_handled(result)
        self.assertEqual(scenario.confirmed, [STATEMENT])
        self.assertEqual(len(scenario.cursor.statements), 2)
        self.assertIn("Comandos confirmados: 1", result.output)
        self.assertIn("Importação parcialmente concluída", result.output)
        self.assertIn("Resultado de confirmação desconhecido", result.output)

    def test_rollback_failure_is_secondary_to_execute_failure(self):
        scenario = Scenario(execute_error_at=1, rollback_error=RollbackError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("execução do comando: ExecuteError", result.output)
        self.assertIn("rollback: RollbackError", result.output)
        self.assertLess(result.output.index("ExecuteError"), result.output.index("RollbackError"))
        self.assertIn("desconhecido", result.output)

    def test_rollback_failure_does_not_mask_commit_failure(self):
        scenario = Scenario(commit_error_at=1, rollback_error=RollbackError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("confirmação do comando: CommitError", result.output)
        self.assertIn("rollback: RollbackError", result.output)
        self.assertIn("Resultado de confirmação desconhecido", result.output)

    def test_cursor_close_failure_still_closes_connection_and_file(self):
        scenario = Scenario(cursor_close_error=CloseError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("encerramento do cursor", result.output)
        self.assertEqual(scenario.confirmed, [STATEMENT])
        self.assertEqual(scenario.connection.rollback_attempts, 0)

    def test_connection_close_failure_still_closes_file(self):
        scenario = Scenario(connection_close_error=CloseError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("encerramento da conexão", result.output)

    def test_file_close_failure_reports_nonzero_exit(self):
        scenario = Scenario(file_close_error=CloseError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("encerramento do arquivo", result.output)

    def test_all_close_failures_are_reported_without_skipping_resources(self):
        scenario = Scenario(
            cursor_close_error=CloseError(PRIVATE),
            connection_close_error=CloseError(PRIVATE),
            file_close_error=CloseError(PRIVATE),
        )
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertEqual(result.output.count("CloseError"), 3)

    def test_file_close_failure_does_not_replace_read_failure(self):
        scenario = Scenario(read_error_after=0, file_close_error=CloseError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("leitura do arquivo: OSError", result.output)
        self.assertIn("encerramento do arquivo: CloseError", result.output)
        self.assertLess(result.output.index("OSError"), result.output.index("CloseError"))

    def test_execute_error_survives_rollback_and_all_close_failures(self):
        scenario = Scenario(
            execute_error_at=1,
            rollback_error=RollbackError(PRIVATE),
            cursor_close_error=CloseError(PRIVATE),
            connection_close_error=CloseError(PRIVATE),
            file_close_error=CloseError(PRIVATE),
        )
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("execução do comando: ExecuteError", result.output)
        self.assertIn("rollback: RollbackError", result.output)
        self.assertEqual(result.output.count("CloseError"), 3)
        self.assertLess(result.output.index("ExecuteError"), result.output.index("RollbackError"))

    def test_keyboard_interrupt_during_read_returns_130(self):
        scenario = Scenario(read_error_after=0, read_error=KeyboardInterrupt(PRIVATE))
        result = scenario.run()
        self.assert_handled(result, code=130)
        self.assert_closed(scenario)
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertIn("interrompida", result.output)

    def test_keyboard_interrupt_during_execute_rolls_back(self):
        scenario = Scenario(execute_error_at=1, execute_error=KeyboardInterrupt(PRIVATE))
        result = scenario.run()
        self.assert_handled(result, code=130)
        self.assert_closed(scenario)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("revertido por rollback", result.output)

    def test_keyboard_interrupt_during_commit_is_unknown(self):
        scenario = Scenario(commit_error_at=1, commit_error=KeyboardInterrupt(PRIVATE))
        result = scenario.run()
        self.assert_handled(result, code=130)
        self.assert_closed(scenario)
        self.assertEqual(scenario.connection.rollback_attempts, 1)
        self.assertIn("Resultado de confirmação desconhecido", result.output)

    def test_keyboard_interrupt_during_connection_closes_file(self):
        scenario = Scenario(connect_error=KeyboardInterrupt(PRIVATE))
        self.assert_handled(scenario.run(), code=130)
        self.assert_closed(scenario, cursor=0, connection=0)

    def test_interrupt_after_connection_acquisition_closes_connection(self):
        scenario = Scenario()

        def interrupt_success_message(message):
            if "Banco de dados conectado com sucesso" in message:
                raise KeyboardInterrupt(PRIVATE)
            print(message)

        scenario.print = interrupt_success_message
        result = scenario.run()
        self.assert_handled(result, code=130)
        self.assert_closed(scenario, cursor=0)
        self.assertEqual(scenario.connection.rollback_attempts, 0)

    def test_keyboard_interrupt_during_input_is_handled(self):
        scenario = Scenario()
        scenario.input.side_effect = KeyboardInterrupt(PRIVATE)
        result = scenario.run()
        self.assert_handled(result, code=130)
        self.assert_closed(scenario, cursor=0, connection=0, file=0)
        scenario.connect.assert_not_called()

    def test_keyboard_interrupt_during_close_still_closes_other_resources(self):
        scenario = Scenario(cursor_close_error=KeyboardInterrupt(PRIVATE))
        self.assert_handled(scenario.run(), code=130)
        self.assert_closed(scenario)

    def test_keyboard_interrupt_during_rollback_preserves_primary_failure(self):
        scenario = Scenario(execute_error_at=1, rollback_error=KeyboardInterrupt(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assert_closed(scenario)
        self.assertIn("ExecuteError", result.output)
        self.assertIn("rollback: KeyboardInterrupt", result.output)

    def test_interrupt_is_not_replaced_by_cleanup_failure_exit_code(self):
        scenario = Scenario(
            execute_error_at=1,
            execute_error=KeyboardInterrupt(PRIVATE),
            cursor_close_error=CloseError(PRIVATE),
        )
        self.assert_handled(scenario.run(), code=130)
        self.assert_closed(scenario)

    def test_success_closes_every_resource_exactly_once(self):
        scenario = Scenario()
        result = scenario.run()
        self.assert_handled(result, code=0)
        self.assert_closed(scenario)
        self.assertEqual(scenario.events, ["open", "connect", "cursor", "execute", "commit", "cursor.close", "connection.close", "file.close"])
        self.assertEqual(scenario.connection.rollback_attempts, 0)
        self.assertIn("Processo finalizado", result.output)

    def test_success_keeps_one_connection_and_commit_per_accepted_line(self):
        scenario = Scenario(text=STATEMENT + "\nGO\n" + STATEMENT + "\n")
        result = scenario.run()
        self.assert_handled(result, code=0)
        self.assert_closed(scenario)
        self.assertEqual(scenario.connect.call_count, 1)
        self.assertEqual(scenario.connection.commit_attempts, 2)
        self.assertEqual(scenario.cursor.statements, [STATEMENT, STATEMENT])
        self.assertEqual(result.namespace["num_linhas"], 3)
        self.assertEqual(result.namespace["linhas_inseridas"], 2)
        self.assertEqual(result.namespace["linhas_em_branco"], 1)

    def test_valid_utf16_bom_succeeds_incrementally(self):
        scenario = Scenario(payload=(STATEMENT + "\r\nGO\r\n").encode("utf-16"))
        result = scenario.run()
        self.assert_handled(result, code=0)
        self.assert_closed(scenario)
        scenario.open.assert_called_once_with(SYNTHETIC_PATH, "r", encoding="utf-16")
        self.assertEqual(scenario.cursor.statements, [STATEMENT])

    def test_cancel_without_import_is_success_without_resources(self):
        scenario = Scenario()
        scenario.input.side_effect = [SYNTHETIC_PATH, "n", "2"]
        result = scenario.run()
        self.assert_handled(result, code=0)
        self.assert_closed(scenario, cursor=0, connection=0, file=0)
        scenario.connect.assert_not_called()
        scenario.open.assert_not_called()

    def test_eof_during_input_returns_failure(self):
        scenario = Scenario()
        scenario.input.side_effect = EOFError(PRIVATE)
        self.assert_handled(scenario.run())
        self.assert_closed(scenario, cursor=0, connection=0, file=0)

    def test_cleanup_failure_after_full_processing_is_not_called_partial(self):
        scenario = Scenario(cursor_close_error=CloseError(PRIVATE))
        result = scenario.run()
        self.assert_handled(result)
        self.assertNotIn("Importação parcialmente concluída", result.output)
        self.assertIn("Processamento concluído; falha no encerramento", result.output)
        self.assertIn("Comandos confirmados: 1", result.output)

    def test_error_messages_never_echo_driver_sql_or_credentials(self):
        scenario = Scenario(execute_error_at=1, execute_error=ExecuteError(f"PWD={PRIVATE}; {STATEMENT}"))
        scenario.input.side_effect = [SYNTHETIC_PATH, "s", "1"]
        result = scenario.run()
        self.assert_handled(result)
        self.assertIn("ExecuteError", result.output)
        self.assertIn("Executando a linha -> 1", result.output)


if __name__ == "__main__":
    unittest.main()
