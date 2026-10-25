"""R5B-2D-2B: probe por adaptador sintético, sem SQL, driver ou rede."""

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

import preflight


NOW = datetime(2026, 10, 24, 17, 0, tzinfo=timezone.utc)
TARGET = "DestinoProbeSintetico"
EXTERNAL = "ExternoProbeSintetico"
PRIVATE = "SEGREDO_PROBE_NAO_EXIBIR"


class FakeAdapter:
    def __init__(self, responses, *, errors=None, close_error=None):
        self.responses = responses
        self.errors = errors or {}
        self.close_error = close_error
        self.reads = []
        self.close_attempts = 0

    def read(self, section):
        if not isinstance(section, preflight.ProbeSection):
            raise AssertionError("Probe enviou consulta textual ou operação desconhecida.")
        self.reads.append(section)
        if section in self.errors:
            raise self.errors[section]
        return self.responses.get(section, ())

    def close(self):
        self.close_attempts += 1
        if self.close_error is not None:
            raise self.close_error


class PreflightProbeTests(unittest.TestCase):
    def api(self):
        for name in ("ProbeSection", "ProbeRequest", "ProbeStatus", "PreflightProbe"):
            self.assertTrue(hasattr(preflight, name), f"{name} ainda não implementado: RED esperado.")

    def request(self, *, target=TARGET, stable_keys=None, session_keys=()):
        self.api()
        if stable_keys is None:
            stable_keys = ("DATABASE_STATE", "UPDATEABILITY", "USER_ACCESS", "EVIDENCE_PERMISSIONS")
        return preflight.ProbeRequest(
            target, PRIVATE + "-execution", NOW - timedelta(minutes=1),
            NOW + timedelta(minutes=5), tuple(stable_keys), tuple(session_keys),
        )

    def responses(self):
        self.api()
        return {
            preflight.ProbeSection.IDENTITY: ({
                "server_instance": PRIVATE,
                "database_name": TARGET,
                "database_id": 91,
                "connection_fingerprint": PRIVATE + "-connection",
                "sql_major_version": 16,
                "version_supported": True,
            },),
            preflight.ProbeSection.CONFIGURATION: ({
                "stable_facts": (
                    ("DATABASE_STATE", "ONLINE"),
                    ("UPDATEABILITY", "READ_WRITE"),
                    ("USER_ACCESS", "MULTI_USER"),
                    ("EVIDENCE_PERMISSIONS", "COMPLETE"),
                ),
            },),
            preflight.ProbeSection.SESSION: ({"session_facts": ()},),
            preflight.ProbeSection.OPERATIONAL: ({
                "measured_at": NOW,
                "data_free_bytes": 10_000,
                "log_free_bytes": 5_000,
                "log_used_percent": 12.5,
            },),
        }

    def collect(self, adapter, request=None):
        self.api()
        return preflight.PreflightProbe().collect(adapter, request or self.request())

    def test_success_collects_typed_complete_snapshot_and_identity(self):
        adapter = FakeAdapter(self.responses())
        result = self.collect(adapter)
        self.assertEqual(result.status, preflight.ProbeStatus.SUCCESS)
        self.assertIsInstance(result.snapshot, preflight.VerifiedSnapshot)
        self.assertIs(result.snapshot.complete, True)
        self.assertEqual(result.snapshot.identity.database_name, TARGET)
        self.assertEqual(result.snapshot.identity.database_id, 91)
        self.assertEqual(adapter.reads, list(preflight.ProbeSection))
        self.assertEqual(adapter.close_attempts, 1)

    def test_current_database_divergence_is_fail_closed(self):
        responses = self.responses()
        row = dict(responses[preflight.ProbeSection.IDENTITY][0])
        row["database_name"] = EXTERNAL
        responses[preflight.ProbeSection.IDENTITY] = (row,)
        result = self.collect(FakeAdapter(responses))
        self.assertEqual(result.status, preflight.ProbeStatus.TARGET_MISMATCH)
        self.assertIsNone(result.snapshot)

    def test_missing_required_configuration_returns_incomplete_snapshot(self):
        responses = self.responses()
        row = dict(responses[preflight.ProbeSection.CONFIGURATION][0])
        row["stable_facts"] = tuple(
            pair for pair in row["stable_facts"] if pair[0] != "UPDATEABILITY")
        responses[preflight.ProbeSection.CONFIGURATION] = (row,)
        result = self.collect(FakeAdapter(responses))
        self.assertEqual(result.status, preflight.ProbeStatus.INCOMPLETE_RESPONSE)
        self.assertIsNotNone(result.snapshot)
        self.assertIs(result.snapshot.complete, False)

    def test_missing_required_session_value_is_incomplete(self):
        result = self.collect(FakeAdapter(self.responses()),
                              self.request(session_keys=("ANSI_NULLS",)))
        self.assertEqual(result.status, preflight.ProbeStatus.INCOMPLETE_RESPONSE)
        self.assertIs(result.snapshot.complete, False)

    def test_incomplete_identity_row_has_no_snapshot(self):
        responses = self.responses()
        row = dict(responses[preflight.ProbeSection.IDENTITY][0])
        del row["database_id"]
        responses[preflight.ProbeSection.IDENTITY] = (row,)
        result = self.collect(FakeAdapter(responses))
        self.assertEqual(result.status, preflight.ProbeStatus.INCOMPLETE_RESPONSE)
        self.assertIsNone(result.snapshot)

    def test_unexpected_response_types_are_rejected(self):
        for section, response in (
                (preflight.ProbeSection.IDENTITY, {"database_name": TARGET}),
                (preflight.ProbeSection.CONFIGURATION, ("não é linha",)),
                (preflight.ProbeSection.SESSION, ({"session_facts": []},))):
            with self.subTest(section=section.value):
                responses = self.responses()
                responses[section] = response
                result = self.collect(FakeAdapter(responses))
                self.assertEqual(result.status, preflight.ProbeStatus.INVALID_RESPONSE)

    def test_duplicate_single_row_sections_are_rejected(self):
        for section in (preflight.ProbeSection.IDENTITY,
                        preflight.ProbeSection.CONFIGURATION,
                        preflight.ProbeSection.SESSION):
            with self.subTest(section=section.value):
                responses = self.responses()
                responses[section] = responses[section] * 2
                result = self.collect(FakeAdapter(responses))
                self.assertEqual(result.status, preflight.ProbeStatus.DUPLICATE_RESULT)

    def test_unsupported_version_is_rejected(self):
        responses = self.responses()
        row = dict(responses[preflight.ProbeSection.IDENTITY][0])
        row.update(sql_major_version=99, version_supported=True)
        responses[preflight.ProbeSection.IDENTITY] = (row,)
        result = self.collect(FakeAdapter(responses))
        self.assertEqual(result.status, preflight.ProbeStatus.UNSUPPORTED_VERSION)
        self.assertIsNone(result.snapshot)

    def test_simulated_connection_timeout_and_permission_errors(self):
        cases = (
            (preflight.ProbeConnectionError(PRIVATE), preflight.ProbeStatus.CONNECTION_ERROR),
            (preflight.ProbeTimeoutError(PRIVATE), preflight.ProbeStatus.TIMEOUT),
            (preflight.ProbePermissionError(PRIVATE), preflight.ProbeStatus.PERMISSION_DENIED),
        )
        for error, status in cases:
            with self.subTest(status=status.value):
                adapter = FakeAdapter(self.responses(), errors={preflight.ProbeSection.IDENTITY: error})
                result = self.collect(adapter)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.snapshot)
                self.assertEqual(adapter.close_attempts, 1)

    def test_intermediate_read_error_discards_partial_result(self):
        adapter = FakeAdapter(
            self.responses(),
            errors={preflight.ProbeSection.CONFIGURATION: preflight.ProbeReadError(PRIVATE)},
        )
        result = self.collect(adapter)
        self.assertEqual(result.status, preflight.ProbeStatus.READ_ERROR)
        self.assertIsNone(result.snapshot)
        self.assertEqual(adapter.reads, [preflight.ProbeSection.IDENTITY,
                                         preflight.ProbeSection.CONFIGURATION])
        self.assertEqual(adapter.close_attempts, 1)

    def test_keyboard_interrupt_propagates_after_close(self):
        adapter = FakeAdapter(
            self.responses(), errors={preflight.ProbeSection.SESSION: KeyboardInterrupt(PRIVATE)})
        with self.assertRaises(KeyboardInterrupt):
            self.collect(adapter)
        self.assertEqual(adapter.close_attempts, 1)

    def test_close_failure_on_success_discards_snapshot(self):
        adapter = FakeAdapter(self.responses(), close_error=OSError(PRIVATE))
        result = self.collect(adapter)
        self.assertEqual(result.status, preflight.ProbeStatus.CLOSE_ERROR)
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.close_failed)
        self.assertEqual(adapter.close_attempts, 1)

    def test_close_failure_does_not_replace_primary_failure(self):
        adapter = FakeAdapter(
            self.responses(),
            errors={preflight.ProbeSection.IDENTITY: preflight.ProbeTimeoutError(PRIVATE)},
            close_error=OSError(PRIVATE),
        )
        result = self.collect(adapter)
        self.assertEqual(result.status, preflight.ProbeStatus.TIMEOUT)
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.close_failed)

    def test_optional_operational_absence_does_not_enter_stable_digest(self):
        responses = self.responses()
        responses[preflight.ProbeSection.OPERATIONAL] = ()
        without = self.collect(FakeAdapter(responses))
        with_values = self.collect(FakeAdapter(self.responses()))
        self.assertEqual(without.status, preflight.ProbeStatus.SUCCESS)
        self.assertIsNone(without.snapshot.operational)
        self.assertEqual(without.snapshot.stable_digest(), with_values.snapshot.stable_digest())

    def test_diagnostics_and_representations_never_expose_private_values(self):
        adapter = FakeAdapter(
            self.responses(), errors={preflight.ProbeSection.IDENTITY: preflight.ProbeConnectionError(PRIVATE)})
        result = self.collect(adapter)
        for rendered in (repr(result), result.reason):
            self.assertNotIn(PRIVATE, rendered)
            self.assertNotIn(TARGET, rendered)

    def test_probe_uses_only_fixed_semantic_read_sections(self):
        adapter = FakeAdapter(self.responses())
        result = self.collect(adapter)
        self.assertEqual(result.status, preflight.ProbeStatus.SUCCESS)
        self.assertEqual(adapter.reads, [
            preflight.ProbeSection.IDENTITY,
            preflight.ProbeSection.CONFIGURATION,
            preflight.ProbeSection.SESSION,
            preflight.ProbeSection.OPERATIONAL,
        ])
        self.assertTrue(all(isinstance(item, preflight.ProbeSection) for item in adapter.reads))

    def test_probe_result_has_no_grant_or_operational_authorization(self):
        result = self.collect(FakeAdapter(self.responses()))
        self.assertFalse(hasattr(result, "grant"))
        self.assertFalse(hasattr(preflight.PreflightProbe, "authorize"))
        self.assertFalse(hasattr(preflight.PreflightProbe, "execute"))


if __name__ == "__main__":
    unittest.main()
