"""Contratos da Fase 2C-1 para estatísticas observáveis do importador."""

import unittest

from tests._application_harness import (
    PRIVATE,
    STATEMENT,
    CloseError,
    CommitError,
    ExecuteError,
    RollbackError,
    Scenario,
    SYNTHETIC_DATABASE,
)


IDENTITY_ON = "SET IDENTITY_INSERT [dbo].[Sintetico] ON"
IDENTITY_OFF = "SET IDENTITY_INSERT [dbo].[Sintetico] OFF"
SESSION_SET = "SET ANSI_NULLS ON"

EXPECTED_KEYS = {
    "linhas_fisicas_lidas",
    "linhas_ignoradas",
    "comandos_emitidos",
    "execucoes_tentadas",
    "execucoes_retornadas",
    "commits_tentados",
    "commits_retornados",
    "comandos_confirmados",
    "inserts_confirmados",
    "identity_insert_confirmados",
    "session_set_confirmados",
    "falhas_execucao",
    "confirmacoes_desconhecidas",
}


class ProcessingStatisticsTests(unittest.TestCase):
    def run_scenario(self, text="", **options):
        scenario = Scenario(text=text, **options)
        result = scenario.run()
        self.assertIsNone(result.uncaught, type(result.uncaught).__name__)
        statistics = result.namespace["estatisticas"]
        self.assertEqual(set(statistics), EXPECTED_KEYS)
        self.assertEqual(
            statistics["comandos_confirmados"],
            statistics["inserts_confirmados"]
            + statistics["identity_insert_confirmados"]
            + statistics["session_set_confirmados"],
        )
        return scenario, result, statistics

    def assert_stats(self, statistics, **expected):
        baseline = dict.fromkeys(EXPECTED_KEYS, 0)
        baseline.update(expected)
        self.assertEqual(statistics, baseline)

    def test_empty_file_has_only_zero_statistics(self):
        _, result, statistics = self.run_scenario("")
        self.assertEqual(result.exit_code, 0)
        self.assert_stats(statistics)
        self.assertNotIn("linhas inseridas", result.output.lower())

    def test_one_insert_counts_each_observable_transition_once(self):
        _, result, statistics = self.run_scenario(STATEMENT + "\n")
        self.assertEqual(result.exit_code, 0)
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=1,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            execucoes_retornadas=1,
            commits_tentados=1,
            commits_retornados=1,
            comandos_confirmados=1,
            inserts_confirmados=1,
        )

    def test_multiline_insert_is_one_command_but_two_physical_lines(self):
        text = "INSERT INTO [dbo].[Sintetico] ([nome])\nVALUES (N'x')\n"
        _, _, statistics = self.run_scenario(text)
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=2,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            execucoes_retornadas=1,
            commits_tentados=1,
            commits_retornados=1,
            comandos_confirmados=1,
            inserts_confirmados=1,
        )

    def test_two_inserts_on_one_line_are_two_commands_not_two_lines(self):
        text = STATEMENT + "; " + STATEMENT + "\n"
        scenario, _, statistics = self.run_scenario(text)
        self.assertEqual(scenario.cursor.statements, [STATEMENT + ";", STATEMENT])
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=1,
            comandos_emitidos=2,
            execucoes_tentadas=2,
            execucoes_retornadas=2,
            commits_tentados=2,
            commits_retornados=2,
            comandos_confirmados=2,
            inserts_confirmados=2,
        )

    def test_multirow_values_is_one_confirmed_insert_not_a_row_estimate(self):
        text = "INSERT INTO [dbo].[Sintetico] ([nome]) VALUES (N'a'), (N'b'), (N'c')\n"
        _, result, statistics = self.run_scenario(text)
        self.assertEqual(statistics["inserts_confirmados"], 1)
        self.assertEqual(statistics["comandos_confirmados"], 1)
        self.assertNotRegex(result.output.lower(), r"registros? inseridos?")

    def test_identity_insert_on_and_off_have_their_own_confirmed_counter(self):
        text = IDENTITY_ON + "\nGO\n" + IDENTITY_OFF + "\n"
        _, _, statistics = self.run_scenario(text)
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=3,
            linhas_ignoradas=1,
            comandos_emitidos=2,
            execucoes_tentadas=2,
            execucoes_retornadas=2,
            commits_tentados=2,
            commits_retornados=2,
            comandos_confirmados=2,
            identity_insert_confirmados=2,
        )

    def test_session_set_has_its_own_confirmed_counter(self):
        _, _, statistics = self.run_scenario(SESSION_SET + "\n")
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=1,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            execucoes_retornadas=1,
            commits_tentados=1,
            commits_retornados=1,
            comandos_confirmados=1,
            session_set_confirmados=1,
        )

    def test_mixed_valid_input_separates_metadata_ignored_lines_and_types(self):
        text = (
            "-- comentário\n\n"
            f"USE [{SYNTHETIC_DATABASE}]\nGO\n"
            "CREATE TABLE [dbo].[Preparacao] ([v] INT)\nGO\n"
            + SESSION_SET + "\nGO\n"
            + IDENTITY_ON + "\nGO\n"
            + STATEMENT + "\nGO\n"
            + IDENTITY_OFF + "\n"
        )
        scenario, _, statistics = self.run_scenario(text)
        self.assertEqual(scenario.cursor.statements, [SESSION_SET, IDENTITY_ON, STATEMENT, IDENTITY_OFF])
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=13,
            linhas_ignoradas=9,
            comandos_emitidos=4,
            execucoes_tentadas=4,
            execucoes_retornadas=4,
            commits_tentados=4,
            commits_retornados=4,
            comandos_confirmados=4,
            inserts_confirmados=1,
            identity_insert_confirmados=2,
            session_set_confirmados=1,
        )

    def test_recognized_ddl_is_ignored_without_becoming_a_command(self):
        _, _, statistics = self.run_scenario("CREATE TABLE [dbo].[Preparacao] ([v] INT)\n")
        self.assert_stats(statistics, linhas_fisicas_lidas=1, linhas_ignoradas=1)

    def test_refused_view_is_never_counted_as_emitted_or_attempted(self):
        _, result, statistics = self.run_scenario("CREATE VIEW [dbo].[v] AS SELECT 1 AS [v]\n")
        self.assertEqual(result.exit_code, 1)
        self.assert_stats(statistics, linhas_fisicas_lidas=1)

    def test_refused_administration_is_never_counted_as_emitted_or_attempted(self):
        _, result, statistics = self.run_scenario(
            f"CREATE DATABASE [{SYNTHETIC_DATABASE}]\n"
        )
        self.assertEqual(result.exit_code, 1)
        self.assert_stats(statistics, linhas_fisicas_lidas=1)

    def test_unsupported_command_is_never_counted_as_emitted_or_attempted(self):
        _, result, statistics = self.run_scenario("DELETE FROM [dbo].[Sintetico]\n")
        self.assertEqual(result.exit_code, 1)
        self.assert_stats(statistics, linhas_fisicas_lidas=1)

    def test_first_execute_failure_is_counted_without_confirmation(self):
        _, result, statistics = self.run_scenario(
            STATEMENT + "\n", execute_error_at=1, execute_error=ExecuteError(PRIVATE)
        )
        self.assertEqual(result.exit_code, 1)
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=1,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            falhas_execucao=1,
        )

    def test_later_execute_failure_preserves_previous_confirmed_command(self):
        _, result, statistics = self.run_scenario(
            (STATEMENT + "\n") * 3, execute_error_at=2
        )
        self.assertEqual(result.exit_code, 1)
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=3,
            comandos_emitidos=2,
            execucoes_tentadas=2,
            execucoes_retornadas=1,
            commits_tentados=1,
            commits_retornados=1,
            comandos_confirmados=1,
            inserts_confirmados=1,
            falhas_execucao=1,
        )

    def test_commit_failure_is_one_unknown_confirmation_and_zero_confirmed(self):
        _, result, statistics = self.run_scenario(
            STATEMENT + "\n", commit_error_at=1, commit_error=CommitError(PRIVATE)
        )
        self.assertEqual(result.exit_code, 1)
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=1,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            execucoes_retornadas=1,
            commits_tentados=1,
            confirmacoes_desconhecidas=1,
        )

    def test_rollback_failure_after_execute_failure_makes_confirmation_unknown(self):
        _, _, statistics = self.run_scenario(
            STATEMENT + "\n",
            execute_error_at=1,
            rollback_error=RollbackError(PRIVATE),
        )
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=1,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            falhas_execucao=1,
            confirmacoes_desconhecidas=1,
        )

    def test_keyboard_interrupt_during_execute_preserves_partial_statistics(self):
        _, result, statistics = self.run_scenario(
            STATEMENT + "\n", execute_error_at=1, execute_error=KeyboardInterrupt(PRIVATE)
        )
        self.assertEqual(result.exit_code, 130)
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=1,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            falhas_execucao=1,
        )

    def test_read_or_classification_failure_preserves_prior_batch_statistics(self):
        text = STATEMENT + "\nGO\nCREATE VIEW [dbo].[v] AS SELECT 1 AS [v]\n"
        scenario, result, statistics = self.run_scenario(text)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(scenario.cursor.statements, [STATEMENT])
        self.assert_stats(
            statistics,
            linhas_fisicas_lidas=3,
            linhas_ignoradas=1,
            comandos_emitidos=1,
            execucoes_tentadas=1,
            execucoes_retornadas=1,
            commits_tentados=1,
            commits_retornados=1,
            comandos_confirmados=1,
            inserts_confirmados=1,
        )

    def test_close_failure_does_not_change_completed_command_statistics(self):
        _, result, statistics = self.run_scenario(
            STATEMENT + "\n", cursor_close_error=CloseError(PRIVATE)
        )
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(statistics["inserts_confirmados"], 1)
        self.assertEqual(statistics["confirmacoes_desconhecidas"], 0)

    def test_two_runs_do_not_share_counters(self):
        _, _, first = self.run_scenario((STATEMENT + "\n") * 2)
        _, _, second = self.run_scenario(SESSION_SET + "\n")
        self.assertEqual(first["comandos_confirmados"], 2)
        self.assertEqual(second["comandos_confirmados"], 1)
        self.assertEqual(second["inserts_confirmados"], 0)
        self.assertEqual(second["session_set_confirmados"], 1)

    def test_output_and_statistics_do_not_expose_sql_or_private_values(self):
        scenario, result, statistics = self.run_scenario(
            STATEMENT + "\n", execute_error_at=1, execute_error=ExecuteError(PRIVATE)
        )
        self.assertEqual(scenario.cursor.statements, [STATEMENT])
        self.assertNotIn(PRIVATE, result.output)
        self.assertNotIn(STATEMENT, result.output)
        self.assertNotIn(PRIVATE, repr(statistics))
        self.assertNotIn(STATEMENT, repr(statistics))

    def test_commands_are_neither_duplicated_nor_lost(self):
        text = SESSION_SET + "; " + IDENTITY_ON + "; " + STATEMENT + "; " + IDENTITY_OFF + "\n"
        scenario, _, statistics = self.run_scenario(text)
        self.assertEqual(
            scenario.cursor.statements,
            [SESSION_SET + ";", IDENTITY_ON + ";", STATEMENT + ";", IDENTITY_OFF],
        )
        self.assertEqual(statistics["comandos_emitidos"], 4)
        self.assertEqual(statistics["comandos_confirmados"], 4)


if __name__ == "__main__":
    unittest.main()
