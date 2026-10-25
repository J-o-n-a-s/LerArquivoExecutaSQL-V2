"""R5C-1A: ponte SQLSTATE sanitizada, sem dependência de pyodbc."""

import unittest

import preflight

try:
    import sqlserver_driver_errors as driver_errors
except ModuleNotFoundError:
    driver_errors = None


PRIVATE = "server=privado;user=privado;password=privado;SELECT segredo"


class AttributeDriverError(Exception):
    def __init__(self, sqlstate, *args):
        super().__init__(*args)
        self.sqlstate = sqlstate


class SQLServerDriverErrorTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(driver_errors, "ponte SQLSTATE ainda não implementada")
        self.assertTrue(hasattr(driver_errors, "extract_sqlstate"))
        self.assertTrue(hasattr(driver_errors, "translate_driver_error"))

    def translated(self, error):
        self.api()
        return driver_errors.translate_driver_error(error)

    def test_known_connection_states_are_classified(self):
        for state in ("08001", "08002", "08003", "08004", "08007", "08S01"):
            with self.subTest(state=state):
                translated = self.translated(Exception(state, PRIVATE))
                self.assertIs(type(translated), preflight.ProbeConnectionError)
                self.assertEqual(translated.sqlstate, state)

    def test_known_timeout_states_are_classified(self):
        for state in ("HYT00", "HYT01"):
            with self.subTest(state=state):
                translated = self.translated(Exception(state, PRIVATE))
                self.assertIs(type(translated), preflight.ProbeTimeoutError)
                self.assertEqual(translated.sqlstate, state)

    def test_invalid_authorization_is_the_only_permission_state(self):
        translated = self.translated(Exception("28000", PRIVATE))
        self.assertIs(type(translated), preflight.ProbePermissionError)
        self.assertEqual(translated.sqlstate, "28000")

    def test_unknown_and_ambiguous_states_are_read_errors(self):
        for state in ("42000", "42S02", "HY000", "42501"):
            with self.subTest(state=state):
                translated = self.translated(Exception(state, PRIVATE))
                self.assertIs(type(translated), preflight.ProbeReadError)
                self.assertEqual(translated.sqlstate, state)

    def test_missing_malformed_or_unexpected_args_are_read_errors(self):
        cases = (
            Exception(),
            Exception(PRIVATE),
            Exception(PRIVATE, "08001"),
            Exception(b"08001", PRIVATE),
            Exception("0800", PRIVATE),
            Exception("080001", PRIVATE),
            Exception("08s01", PRIVATE),
            Exception("08Á01", PRIVATE),
            Exception(object(), PRIVATE),
        )
        for error in cases:
            with self.subTest(args=tuple(type(value).__name__ for value in error.args)):
                translated = self.translated(error)
                self.assertIs(type(translated), preflight.ProbeReadError)
                self.assertIsNone(translated.sqlstate)

    def test_explicit_attribute_is_supported_but_conflict_fails_closed(self):
        valid = AttributeDriverError("HYT00", PRIVATE)
        self.assertEqual(driver_errors.extract_sqlstate(valid), "HYT00")
        self.assertIs(type(self.translated(valid)), preflight.ProbeTimeoutError)

        for error in (
            AttributeDriverError("HYT00", "08001", PRIVATE),
            AttributeDriverError("bad", "08001", PRIVATE),
        ):
            with self.subTest(sqlstate=error.sqlstate):
                translated = self.translated(error)
                self.assertIs(type(translated), preflight.ProbeReadError)
                self.assertIsNone(translated.sqlstate)

    def test_public_exception_is_fixed_and_contains_no_private_arguments(self):
        for state in ("08001", "HYT00", "28000", "42000", None):
            error = Exception(state, PRIVATE) if state is not None else OSError(PRIVATE)
            with self.subTest(state=state):
                translated = self.translated(error)
                self.assertNotIn(PRIVATE, str(translated))
                self.assertNotIn(PRIVATE, repr(translated))
                self.assertEqual(len(translated.args), 1)
                self.assertIs(type(translated.args[0]), str)


if __name__ == "__main__":
    unittest.main()
