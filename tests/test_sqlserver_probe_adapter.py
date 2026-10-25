"""R5B-2D-3A: contrato offline do adaptador SQL Server read-only."""

import re
import unittest
from datetime import datetime, timedelta, timezone

import preflight

try:
    import sqlserver_probe_adapter as sql_adapter
except ModuleNotFoundError:
    sql_adapter = None


TARGET = "DestinoOffline"
FINGERPRINT = "sha256:conexao-sintetica"
PRIVATE = "SEGREDO_DRIVER_NAO_EXIBIR"
NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)

IDENTITY_COLUMNS = (
    "server_instance", "database_name", "database_id", "sql_major_version",
)
CONFIGURATION_COLUMNS = (
    "database_state", "is_read_only", "user_access", "compatibility_level",
)
SESSION_COLUMNS = (
    "ansi_nulls", "ansi_padding", "ansi_warnings", "arithabort",
    "concat_null_yields_null", "numeric_roundabort", "quoted_identifier",
)


class FakeCursor:
    def __init__(self, batches, *, execute_errors=None, fetch_errors=None,
                 close_error=None):
        self.batches = list(batches)
        self.execute_errors = execute_errors or {}
        self.fetch_errors = fetch_errors or {}
        self.close_error = close_error
        self.executions = []
        self.close_attempts = 0
        self._index = -1
        self.description = None

    def execute(self, statement, *parameters):
        self._index += 1
        self.executions.append((statement, parameters))
        error = self.execute_errors.get(self._index)
        if error is not None:
            raise error
        self.description = [(name,) for name in self.batches[self._index][0]]
        return self

    def fetchall(self):
        error = self.fetch_errors.get(self._index)
        if error is not None:
            raise error
        return self.batches[self._index][1]

    def close(self):
        self.close_attempts += 1
        if self.close_error is not None:
            raise self.close_error


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.cursor_calls = 0
        self.close_attempts = 0
        self.commit_attempts = 0
        self.rollback_attempts = 0

    def cursor(self):
        self.cursor_calls += 1
        return self.fake_cursor

    def close(self):
        self.close_attempts += 1
        raise AssertionError("adaptador não possui a conexão")

    def commit(self):
        self.commit_attempts += 1
        raise AssertionError("probe read-only não faz commit")

    def rollback(self):
        self.rollback_attempts += 1
        raise AssertionError("probe read-only não faz rollback")


class RowLike:
    """Modelo mínimo de pyodbc.Row: comprimento e índice inteiro."""

    def __init__(self, *values):
        self._values = values

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        if type(index) is not int:
            raise TypeError("índice deve ser inteiro")
        return self._values[index]


class LengthOnly:
    def __len__(self):
        return 4


class SyntheticDriverError(Exception):
    pass


class SQLServerProbeAdapterTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(sql_adapter, "adaptador ainda não implementado")
        for name in (
            "SQLServerReadOnlyProbeAdapter", "IDENTITY_QUERY",
            "CONFIGURATION_QUERY", "SESSION_QUERY",
        ):
            self.assertTrue(hasattr(sql_adapter, name), name)

    def batches(self, *, major="16"):
        return [
            (IDENTITY_COLUMNS, (("SERVIDOR\\INSTANCIA", TARGET, 91, major),)),
            (CONFIGURATION_COLUMNS, (("ONLINE", False, "MULTI_USER", 150),)),
            (SESSION_COLUMNS, ((1, 1, 1, 1, 1, 0, 1),)),
        ]

    def make(self, *, batches=None, execute_errors=None, fetch_errors=None,
             close_error=None, fingerprint=FINGERPRINT):
        self.api()
        cursor = FakeCursor(
            batches if batches is not None else self.batches(),
            execute_errors=execute_errors,
            fetch_errors=fetch_errors,
            close_error=close_error,
        )
        connection = FakeConnection(cursor)
        adapter = sql_adapter.SQLServerReadOnlyProbeAdapter(
            connection, fingerprint)
        return adapter, connection, cursor

    def request(self):
        return preflight.ProbeRequest(
            TARGET,
            "execucao-offline",
            NOW,
            NOW + timedelta(minutes=5),
            ("COMPATIBILITY_LEVEL", "DATABASE_STATE", "UPDATEABILITY",
             "USER_ACCESS"),
            ("ANSI_NULLS", "QUOTED_IDENTIFIER"),
        )

    def collect(self, **kwargs):
        adapter, connection, cursor = self.make(**kwargs)
        result = preflight.PreflightProbe().collect(adapter, self.request())
        return result, connection, cursor

    def test_queries_are_constant_single_read_only_selects(self):
        self.api()
        forbidden = re.compile(
            r"\b(?:INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|EXEC(?:UTE)?|"
            r"USE|SET|COMMIT|ROLLBACK)\b",
            re.IGNORECASE,
        )
        for query in (
            sql_adapter.IDENTITY_QUERY,
            sql_adapter.CONFIGURATION_QUERY,
            sql_adapter.SESSION_QUERY,
        ):
            with self.subTest(query=query[:30]):
                normalized = query.strip()
                self.assertRegex(normalized, r"(?is)^SELECT\b")
                self.assertEqual(len(re.findall(r"\bSELECT\b", normalized,
                                                re.IGNORECASE)), 1)
                self.assertNotRegex(normalized, forbidden)
                self.assertNotIn(";", normalized)
                self.assertNotIn("--", normalized)
                self.assertNotIn("/*", normalized)

    def test_session_query_converts_all_sql_variant_values_to_int(self):
        self.api()
        query = sql_adapter.SESSION_QUERY
        expected = (
            ("ANSI_NULLS", "ansi_nulls"),
            ("ANSI_PADDING", "ansi_padding"),
            ("ANSI_WARNINGS", "ansi_warnings"),
            ("ARITHABORT", "arithabort"),
            ("CONCAT_NULL_YIELDS_NULL", "concat_null_yields_null"),
            ("NUMERIC_ROUNDABORT", "numeric_roundabort"),
            ("QUOTED_IDENTIFIER", "quoted_identifier"),
        )
        self.assertEqual(query.upper().count("SESSIONPROPERTY("), 7)
        self.assertEqual(query.upper().count("CONVERT(INT, SESSIONPROPERTY("), 7)
        for option, alias in expected:
            with self.subTest(option=option):
                pattern = (
                    r"CONVERT\s*\(\s*int\s*,\s*SESSIONPROPERTY\s*\(\s*'"
                    + option
                    + r"'\s*\)\s*\)\s+AS\s+"
                    + alias
                    + r"\b"
                )
                self.assertRegex(query, re.compile(pattern, re.IGNORECASE))

    def test_pyodbc_row_like_tuple_and_list_are_accepted(self):
        valid_rows = (
            RowLike("SERVIDOR\\INSTANCIA", TARGET, 91, "16"),
            ("SERVIDOR\\INSTANCIA", TARGET, 91, "16"),
            ["SERVIDOR\\INSTANCIA", TARGET, 91, "16"],
        )
        for row in valid_rows:
            with self.subTest(row_type=type(row).__name__):
                batches = self.batches()
                batches[0] = (IDENTITY_COLUMNS, (row,))
                result, _, _ = self.collect(batches=batches)
                self.assertEqual(result.status, preflight.ProbeStatus.SUCCESS)

    def test_malformed_or_non_sequence_rows_remain_rejected(self):
        invalid_rows = (
            RowLike("SERVIDOR\\INSTANCIA", TARGET, 91),
            "SERVIDOR\\INSTANCIA",
            b"quatro",
            {0: "SERVIDOR\\INSTANCIA", 1: TARGET, 2: 91, 3: "16"},
            (value for value in ("SERVIDOR\\INSTANCIA", TARGET, 91, "16")),
            LengthOnly(),
            object(),
        )
        for row in invalid_rows:
            with self.subTest(row_type=type(row).__name__):
                batches = self.batches()
                batches[0] = (IDENTITY_COLUMNS, (row,))
                result, _, _ = self.collect(batches=batches)
                self.assertEqual(result.status, preflight.ProbeStatus.READ_ERROR)
                self.assertIsNone(result.snapshot)

    def test_reads_execute_constants_in_fixed_order_without_parameters(self):
        adapter, connection, cursor = self.make()
        for section in preflight.ProbeSection:
            adapter.read(section)
        adapter.close()
        self.assertEqual(
            cursor.executions,
            [(sql_adapter.IDENTITY_QUERY, ()),
             (sql_adapter.CONFIGURATION_QUERY, ()),
             (sql_adapter.SESSION_QUERY, ())],
        )
        self.assertEqual(connection.cursor_calls, 1)

    def test_queries_do_not_contain_target_fingerprint_or_dump_content(self):
        marker = PRIVATE + " dump INSERT CREATE"
        adapter, _, cursor = self.make(fingerprint=marker)
        adapter.read(preflight.ProbeSection.IDENTITY)
        adapter.close()
        rendered = "\n".join(statement for statement, _ in cursor.executions)
        self.assertNotIn(marker, rendered)
        self.assertNotIn(TARGET, rendered)
        self.assertTrue(all(not parameters for _, parameters in cursor.executions))

    def test_identity_uses_current_database_server_and_external_fingerprint(self):
        adapter, _, _ = self.make()
        rows = adapter.read(preflight.ProbeSection.IDENTITY)
        adapter.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {
            "server_instance": "SERVIDOR\\INSTANCIA",
            "database_name": TARGET,
            "database_id": 91,
            "connection_fingerprint": FINGERPRINT,
            "sql_major_version": 16,
            "version_supported": True,
        })

    def test_configuration_and_session_are_typed_without_favorable_defaults(self):
        adapter, _, _ = self.make()
        adapter.read(preflight.ProbeSection.IDENTITY)
        configuration = adapter.read(preflight.ProbeSection.CONFIGURATION)
        session = adapter.read(preflight.ProbeSection.SESSION)
        operational = adapter.read(preflight.ProbeSection.OPERATIONAL)
        adapter.close()
        self.assertEqual(dict(configuration[0]["stable_facts"]), {
            "COMPATIBILITY_LEVEL": "150",
            "DATABASE_STATE": "ONLINE",
            "UPDATEABILITY": "READ_WRITE",
            "USER_ACCESS": "MULTI_USER",
        })
        self.assertEqual(dict(session[0]["session_facts"])["NUMERIC_ROUNDABORT"],
                         "OFF")
        self.assertEqual(operational, ())
        for forbidden in ("EVIDENCE_PERMISSIONS", "STORAGE_READY", "SCHEMA_READY"):
            self.assertNotIn(forbidden, dict(configuration[0]["stable_facts"]))

    def test_adapter_and_probe_accept_documented_subset(self):
        result, _, _ = self.collect()
        self.assertEqual(result.status, preflight.ProbeStatus.SUCCESS)
        self.assertIs(result.snapshot.complete, True)
        self.assertIsNone(result.snapshot.operational)

    def test_uncollected_permission_evidence_keeps_snapshot_incomplete(self):
        adapter, _, _ = self.make()
        request = self.request()
        request = preflight.ProbeRequest(
            request.target_database,
            request.execution_id,
            request.captured_at,
            request.expires_at,
            request.required_stable_keys + ("EVIDENCE_PERMISSIONS",),
            request.required_session_keys,
        )
        result = preflight.PreflightProbe().collect(adapter, request)
        self.assertEqual(result.status, preflight.ProbeStatus.INCOMPLETE_RESPONSE)
        self.assertIsNotNone(result.snapshot)
        self.assertIs(result.snapshot.complete, False)

    def test_absent_and_duplicate_results_remain_visible_to_probe(self):
        for rows, expected in (
            ((), preflight.ProbeStatus.INCOMPLETE_RESPONSE),
            ((("S", TARGET, 91, "16"), ("S", TARGET, 91, "16")),
             preflight.ProbeStatus.DUPLICATE_RESULT),
        ):
            with self.subTest(expected=expected.value):
                batches = self.batches()
                batches[0] = (IDENTITY_COLUMNS, rows)
                result, _, _ = self.collect(batches=batches)
                self.assertEqual(result.status, expected)
                self.assertIsNone(result.snapshot)

    def test_missing_or_duplicate_column_is_sanitized_read_error(self):
        for columns in (
            IDENTITY_COLUMNS[:-1],
            ("server_instance", "database_name", "database_id", "database_id"),
        ):
            with self.subTest(columns=columns):
                batches = self.batches()
                batches[0] = (columns, (("S", TARGET, 91, "16"),))
                result, _, _ = self.collect(batches=batches)
                self.assertEqual(result.status, preflight.ProbeStatus.READ_ERROR)
                self.assertIsNone(result.snapshot)

    def test_null_and_incorrect_identity_types_are_sanitized(self):
        bad_rows = (
            (None, TARGET, 91, "16"),
            ("S", TARGET, "91", "16"),
            ("S", TARGET, 91, object()),
        )
        for row in bad_rows:
            with self.subTest(row_types=tuple(type(value).__name__ for value in row)):
                batches = self.batches()
                batches[0] = (IDENTITY_COLUMNS, (row,))
                result, _, _ = self.collect(batches=batches)
                self.assertEqual(result.status, preflight.ProbeStatus.READ_ERROR)

    def test_null_or_incorrect_configuration_and_session_types_are_sanitized(self):
        cases = (
            (1, CONFIGURATION_COLUMNS, (("ONLINE", None, "MULTI_USER", 150),)),
            (1, CONFIGURATION_COLUMNS, (("ONLINE", False, "MULTI_USER", "150"),)),
            (2, SESSION_COLUMNS, ((1, 1, None, 1, 1, 0, 1),)),
            (2, SESSION_COLUMNS, ((1, 1, True, 1, 1, 0, 1),)),
        )
        for index, columns, rows in cases:
            with self.subTest(section=index, row=rows[0]):
                batches = self.batches()
                batches[index] = (columns, rows)
                result, _, _ = self.collect(batches=batches)
                self.assertEqual(result.status, preflight.ProbeStatus.READ_ERROR)
                self.assertIsNone(result.snapshot)

    def test_sql_server_2025_major_17_is_not_supported(self):
        result, _, _ = self.collect(batches=self.batches(major="17"))
        self.assertEqual(result.status, preflight.ProbeStatus.UNSUPPORTED_VERSION)
        self.assertIsNone(result.snapshot)

    def test_timeout_permission_and_driver_failures_are_typed_and_sanitized(self):
        cases = (
            (preflight.ProbeTimeoutError(PRIVATE), preflight.ProbeStatus.TIMEOUT),
            (preflight.ProbePermissionError(PRIVATE),
             preflight.ProbeStatus.PERMISSION_DENIED),
            (OSError(PRIVATE), preflight.ProbeStatus.READ_ERROR),
        )
        for error, expected in cases:
            with self.subTest(expected=expected.value):
                result, _, cursor = self.collect(execute_errors={0: error})
                self.assertEqual(result.status, expected)
                self.assertNotIn(PRIVATE, repr(result))
                self.assertNotIn(PRIVATE, result.reason)
                self.assertEqual(cursor.close_attempts, 1)

    def test_sqlstate_driver_failures_are_translated_at_adapter_boundary(self):
        cases = (
            ("08S01", preflight.ProbeStatus.CONNECTION_ERROR),
            ("HYT00", preflight.ProbeStatus.TIMEOUT),
            ("28000", preflight.ProbeStatus.PERMISSION_DENIED),
            ("42000", preflight.ProbeStatus.READ_ERROR),
            ("ZZ999", preflight.ProbeStatus.READ_ERROR),
        )
        for state, expected in cases:
            with self.subTest(state=state):
                result, connection, cursor = self.collect(
                    execute_errors={0: SyntheticDriverError(state, PRIVATE)})
                self.assertEqual(result.status, expected)
                self.assertNotIn(PRIVATE, repr(result))
                self.assertNotIn(PRIVATE, result.reason)
                self.assertEqual(cursor.executions,
                                 [(sql_adapter.IDENTITY_QUERY, ())])
                self.assertEqual(cursor.close_attempts, 1)
                self.assertEqual(connection.close_attempts, 0)
                self.assertEqual(connection.commit_attempts, 0)
                self.assertEqual(connection.rollback_attempts, 0)

    def test_fetch_sqlstate_is_translated_without_additional_statement(self):
        result, connection, cursor = self.collect(
            fetch_errors={0: SyntheticDriverError("HYT01", PRIVATE)})
        self.assertEqual(result.status, preflight.ProbeStatus.TIMEOUT)
        self.assertEqual(cursor.executions, [(sql_adapter.IDENTITY_QUERY, ())])
        self.assertEqual(cursor.close_attempts, 1)
        self.assertEqual(connection.close_attempts, 0)
        self.assertEqual(connection.commit_attempts, 0)
        self.assertEqual(connection.rollback_attempts, 0)

    def test_primary_sqlstate_failure_survives_cursor_close_failure(self):
        result, connection, cursor = self.collect(
            execute_errors={0: SyntheticDriverError("08S01", PRIVATE)},
            close_error=OSError(PRIVATE),
        )
        self.assertEqual(result.status, preflight.ProbeStatus.CONNECTION_ERROR)
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.close_failed)
        self.assertEqual(cursor.close_attempts, 1)
        self.assertEqual(connection.close_attempts, 0)
        self.assertNotIn(PRIVATE, repr(result))
        self.assertNotIn(PRIVATE, result.reason)

    def test_keyboard_interrupt_and_system_exit_propagate_after_cursor_close(self):
        for error in (KeyboardInterrupt(PRIVATE), SystemExit(7)):
            with self.subTest(error=type(error).__name__):
                adapter, _, cursor = self.make(execute_errors={0: error})
                with self.assertRaises(type(error)):
                    preflight.PreflightProbe().collect(adapter, self.request())
                self.assertEqual(cursor.close_attempts, 1)

    def test_close_owns_cursor_but_never_connection_or_transaction(self):
        result, connection, cursor = self.collect()
        self.assertEqual(result.status, preflight.ProbeStatus.SUCCESS)
        self.assertEqual(cursor.close_attempts, 1)
        self.assertEqual(connection.close_attempts, 0)
        self.assertEqual(connection.commit_attempts, 0)
        self.assertEqual(connection.rollback_attempts, 0)

    def test_cursor_close_failure_invalidates_success_without_closing_connection(self):
        result, connection, cursor = self.collect(close_error=OSError(PRIVATE))
        self.assertEqual(result.status, preflight.ProbeStatus.CLOSE_ERROR)
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.close_failed)
        self.assertEqual(cursor.close_attempts, 1)
        self.assertEqual(connection.close_attempts, 0)

    def test_adapter_public_representation_redacts_connection_values(self):
        adapter, _, _ = self.make(fingerprint=PRIVATE)
        try:
            self.assertNotIn(PRIVATE, repr(adapter))
            self.assertNotIn(TARGET, repr(adapter))
        finally:
            adapter.close()

    def test_no_grant_authorization_or_execution_api_is_exposed(self):
        self.api()
        for name in ("grant", "authorize", "commit", "rollback"):
            self.assertFalse(hasattr(sql_adapter.SQLServerReadOnlyProbeAdapter,
                                     name))


if __name__ == "__main__":
    unittest.main()
