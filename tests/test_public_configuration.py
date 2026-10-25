"""Fase 2D-1: configuração explícita e privacidade do importador."""

import re
import unittest
import warnings

from tests._application_harness import (
    PRIVATE,
    STATEMENT,
    SYNTHETIC_DATABASE,
    SYNTHETIC_DRIVER,
    SYNTHETIC_PATH,
    SYNTHETIC_SERVER,
    DatabaseError,
    Scenario,
)


VALID_ENVIRONMENT = {
    "LER_SQL_SERVER": SYNTHETIC_SERVER,
    "LER_SQL_DATABASE": SYNTHETIC_DATABASE,
    "LER_SQL_DRIVER": SYNTHETIC_DRIVER,
}


class PublicConfigurationTests(unittest.TestCase):
    def assert_invalid(self, environment, *, path=SYNTHETIC_PATH):
        scenario = Scenario(environment=environment)
        scenario.input.side_effect = [path, "n", "1"]
        result = scenario.run()
        self.assertIsNone(result.uncaught, type(result.uncaught).__name__)
        self.assertEqual(result.exit_code, 1, result.output)
        scenario.open.assert_not_called()
        scenario.connect.assert_not_called()
        for value in environment.values():
            if isinstance(value, str) and value:
                self.assertNotIn(value, result.output)
        self.assertNotIn(PRIVATE, result.output)
        if path:
            self.assertNotIn(path, result.output)
        return scenario, result

    def test_missing_configuration_never_opens_or_connects(self):
        self.assert_invalid({})

    def test_partial_configuration_never_opens_or_connects(self):
        for missing in VALID_ENVIRONMENT:
            with self.subTest(missing=missing):
                environment = VALID_ENVIRONMENT.copy()
                del environment[missing]
                self.assert_invalid(environment)

    def test_empty_server_database_or_driver_is_rejected(self):
        for name in VALID_ENVIRONMENT:
            with self.subTest(name=name):
                environment = VALID_ENVIRONMENT.copy()
                environment[name] = ""
                self.assert_invalid(environment)

    def test_empty_file_path_has_no_private_fallback(self):
        self.assert_invalid(VALID_ENVIRONMENT, path="")

    def test_control_characters_are_rejected_in_every_field(self):
        for name in VALID_ENVIRONMENT:
            with self.subTest(name=name):
                environment = VALID_ENVIRONMENT.copy()
                environment[name] = environment[name] + "\n" + PRIVATE
                self.assert_invalid(environment)
        self.assert_invalid(VALID_ENVIRONMENT, path=SYNTHETIC_PATH + "\x00" + PRIVATE)

    def test_del_and_c1_controls_are_rejected_in_connection_fields(self):
        for control in ("\x7f", "\x85", "\x80", "\x9f"):
            for name in VALID_ENVIRONMENT:
                with self.subTest(codepoint=ord(control), name=name):
                    environment = VALID_ENVIRONMENT.copy()
                    environment[name] += control + PRIVATE
                    self.assert_invalid(environment)

    def test_del_and_c1_controls_are_rejected_in_file_path(self):
        for control in ("\x7f", "\x85", "\x80", "\x9f"):
            with self.subTest(codepoint=ord(control)):
                self.assert_invalid(
                    VALID_ENVIRONMENT,
                    path=SYNTHETIC_PATH + control + PRIVATE,
                )

    def test_legitimate_unicode_configuration_and_path_are_accepted(self):
        environment = {
            "LER_SQL_SERVER": "ServidorÁrvore\\Instância",
            "LER_SQL_DATABASE": "Banco_日本",
            "LER_SQL_DRIVER": "Driver ODBC São",
        }
        path = "synthetic://arquivo-ação-日本.sql"
        scenario = Scenario(environment=environment, expected_path=path)
        scenario.input.side_effect = [path, "n", "1"]
        result = scenario.run()
        self.assertEqual(result.exit_code, 0, result.output)
        scenario.open.assert_called_once_with(path, "r", encoding="utf-16")
        scenario.connect.assert_called_once()

    def test_odbc_parameter_injection_is_rejected(self):
        attacks = (
            "host;Trusted_Connection=no",
            "database;AttachDbFilename=private",
            "{driver}",
            "driver}",
        )
        for name in VALID_ENVIRONMENT:
            for attack in attacks:
                with self.subTest(name=name, delimiter=attack[0]):
                    environment = VALID_ENVIRONMENT.copy()
                    environment[name] = attack
                    self.assert_invalid(environment)

    def test_leading_or_trailing_whitespace_is_rejected(self):
        for name in VALID_ENVIRONMENT:
            with self.subTest(name=name):
                environment = VALID_ENVIRONMENT.copy()
                environment[name] = " " + environment[name]
                self.assert_invalid(environment)

    def test_valid_configuration_is_delivered_to_fake_driver(self):
        scenario = Scenario(environment=VALID_ENVIRONMENT)
        result = scenario.run()
        self.assertIsNone(result.uncaught)
        self.assertEqual(result.exit_code, 0, result.output)
        scenario.connect.assert_called_once_with(
            "Driver={" + SYNTHETIC_DRIVER + "};"
            "Server=" + SYNTHETIC_SERVER + ";"
            "Database=" + SYNTHETIC_DATABASE + ";"
            "Trusted_Connection=yes;"
        )

    def test_configured_database_is_the_reader_target(self):
        text = f"USE [{SYNTHETIC_DATABASE}]\nGO\n{STATEMENT}\n"
        scenario = Scenario(text=text, environment=VALID_ENVIRONMENT)
        result = scenario.run()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(scenario.cursor.statements, [STATEMENT])

    def test_user_must_still_confirm_before_any_side_effect(self):
        scenario = Scenario(environment=VALID_ENVIRONMENT)
        scenario.input.side_effect = [SYNTHETIC_PATH, "n", "2"]
        result = scenario.run()
        self.assertEqual(result.exit_code, 0, result.output)
        scenario.open.assert_not_called()
        scenario.connect.assert_not_called()

    def test_valid_path_is_not_printed(self):
        scenario = Scenario(environment=VALID_ENVIRONMENT)
        result = scenario.run()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn(SYNTHETIC_PATH, result.output)

    def test_connection_failure_does_not_expose_configuration(self):
        scenario = Scenario(
            environment=VALID_ENVIRONMENT,
            connect_error=DatabaseError(PRIVATE),
        )
        result = scenario.run()
        self.assertEqual(result.exit_code, 1, result.output)
        for private in (*VALID_ENVIRONMENT.values(), PRIVATE):
            self.assertNotIn(private, result.output)

    def test_file_failure_does_not_expose_path(self):
        scenario = Scenario(environment=VALID_ENVIRONMENT, open_error=OSError(PRIVATE))
        result = scenario.run()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertNotIn(SYNTHETIC_PATH, result.output)
        self.assertNotIn(PRIVATE, result.output)

    def test_keyboard_interrupt_keeps_code_130_without_configuration_leak(self):
        scenario = Scenario(environment=VALID_ENVIRONMENT)
        scenario.input.side_effect = KeyboardInterrupt(PRIVATE)
        result = scenario.run()
        self.assertEqual(result.exit_code, 130, result.output)
        for private in (*VALID_ENVIRONMENT.values(), PRIVATE):
            self.assertNotIn(private, result.output)

    def test_statistics_remain_available_with_explicit_configuration(self):
        scenario = Scenario(text=STATEMENT + "\n", environment=VALID_ENVIRONMENT)
        result = scenario.run()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.namespace["estatisticas"]["comandos_confirmados"], 1)
        self.assertEqual(result.namespace["estatisticas"]["inserts_confirmados"], 1)

    def test_source_has_no_absolute_user_path_or_syntax_warning(self):
        from tests import _application_harness

        production = _application_harness.SOURCE_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[A-Za-z]:\\Users\\", production, re.IGNORECASE))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(production, str(_application_harness.SOURCE_PATH), "exec")
        self.assertFalse(any(item.category is SyntaxWarning for item in caught))


if __name__ == "__main__":
    unittest.main()
