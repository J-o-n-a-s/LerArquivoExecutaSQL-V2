"""R5B-2D-2A: RED/GREEN das validações temporais, booleanas e de identidade."""

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone, tzinfo

import preflight


NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
TARGET = "DestinoHardeningSintetico"
PRIVATE = "SEGREDO_HARDENING_NAO_EXIBIR"


class NullOffsetTimezone(tzinfo):
    """Tem tzinfo, mas continua sendo naive segundo o contrato de datetime."""

    def utcoffset(self, moment):
        return None


class NoReadAdapter:
    def __init__(self):
        self.reads = 0
        self.close_attempts = 0

    def read(self, section):
        self.reads += 1
        raise AssertionError("request inválido não pode iniciar coleta")

    def close(self):
        self.close_attempts += 1


class PreflightHardeningTests(unittest.TestCase):
    def bundle(self, signature_id="db.set.read_write"):
        definition = preflight.signature_by_id(signature_id)
        profile = None
        if definition.family is preflight.DatabaseInstructionFamily.CREATE:
            profile = preflight.CreateDatabaseProfile(
                "NONE",
                preflight.DatabaseFileProfile("PRIMARY", "UNICODE", "UNICODE", 8192, "KB", None, "UNLIMITED", 65536, "KB"),
                preflight.DatabaseFileProfile("LOG", "UNICODE", "UNICODE", 8192, "KB", 2048, "GB", 65536, "KB"),
                "DATABASE_DEFAULT",
            )
        candidate = preflight.DatabaseInstructionFacts(
            definition.signature_id, definition.family, TARGET, 3,
            definition.option, definition.requested_value, profile,
        )
        identity = preflight.TargetIdentity(PRIVATE, TARGET, 41, PRIVATE + "-connection")
        facts = {
            "DATABASE_STATE": "ONLINE",
            "UPDATEABILITY": "READ_WRITE",
            "USER_ACCESS": "MULTI_USER",
            "EVIDENCE_PERMISSIONS": "COMPLETE",
        }
        if definition.family is preflight.DatabaseInstructionFamily.CREATE:
            facts.update({"DATABASE_EXISTS": "YES", "CONTAINMENT": "NONE",
                          "CATALOG_COLLATION": "DATABASE_DEFAULT",
                          "STORAGE_READY": "YES", "SCHEMA_READY": "YES"})
        else:
            facts[definition.evidence_key] = definition.evidence_value
        session = ()
        if definition.requires_session_match:
            session = ((definition.option, definition.requested_value),)
        snapshot = preflight.VerifiedSnapshot(
            identity, PRIVATE + "-execution", NOW - timedelta(minutes=1),
            NOW + timedelta(minutes=5), 16, True, True,
            tuple(sorted(facts.items())), session,
        )
        grant = preflight.SignatureGrant(
            definition.signature_id, snapshot.execution_id,
            snapshot.identity.fingerprint(), snapshot.stable_digest(),
            definition.requested_value, NOW + timedelta(minutes=4),
            profile.digest() if profile else None,
            definition.family is preflight.DatabaseInstructionFamily.CREATE,
        )
        return definition, candidate, snapshot, grant

    def evaluate(self, candidate, snapshot, grant, *, now=NOW):
        return preflight.evaluate_database_authorization(candidate, snapshot, grant, now=now)

    def test_future_captured_at_is_incomplete_evidence(self):
        _, candidate, snapshot, _ = self.bundle()
        snapshot = dataclasses.replace(snapshot, captured_at=NOW + timedelta(seconds=1))
        grant = dataclasses.replace(self.bundle()[3], evidence_digest=snapshot.stable_digest())
        self.assertEqual(self.evaluate(candidate, snapshot, grant).status,
                         preflight.AuthorizationStatus.INCOMPLETE_EVIDENCE)

    def test_snapshot_expires_at_exact_now_is_expired(self):
        _, candidate, snapshot, _ = self.bundle()
        snapshot = dataclasses.replace(snapshot, expires_at=NOW)
        grant = dataclasses.replace(self.bundle()[3], evidence_digest=snapshot.stable_digest())
        self.assertEqual(self.evaluate(candidate, snapshot, grant).status,
                         preflight.AuthorizationStatus.EVIDENCE_EXPIRED)

    def test_grant_expires_at_exact_now_is_expired(self):
        _, candidate, snapshot, grant = self.bundle()
        grant = dataclasses.replace(grant, expires_at=NOW)
        self.assertEqual(self.evaluate(candidate, snapshot, grant).status,
                         preflight.AuthorizationStatus.GRANT_EXPIRED)

    def test_create_approval_requires_real_true_boolean(self):
        _, candidate, snapshot, grant = self.bundle("db.create.observed_v1")
        for value in ("yes", 1, object(), [True]):
            with self.subTest(value=type(value).__name__):
                changed = dataclasses.replace(grant, create_approved=value)
                self.assertEqual(self.evaluate(candidate, snapshot, changed).status,
                                 preflight.AuthorizationStatus.APPROVAL_REQUIRED)
        self.assertEqual(self.evaluate(candidate, snapshot, grant).status,
                         preflight.AuthorizationStatus.AUTHORIZED_SYNTHETIC)

    def test_snapshot_complete_requires_real_true_boolean(self):
        _, candidate, snapshot, grant = self.bundle()
        for value in ("yes", 1, object(), [True]):
            with self.subTest(value=type(value).__name__):
                changed = dataclasses.replace(snapshot, complete=value)
                self.assertEqual(self.evaluate(candidate, changed, grant).status,
                                 preflight.AuthorizationStatus.INCOMPLETE_EVIDENCE)

    def test_server_instance_must_be_valid_text(self):
        _, candidate, snapshot, grant = self.bundle()
        for value in (None, "", "   ", "servidor\x00oculto", 7):
            with self.subTest(value=repr(value)):
                identity = dataclasses.replace(snapshot.identity, server_instance=value)
                changed = dataclasses.replace(snapshot, identity=identity)
                changed_grant = dataclasses.replace(
                    grant, target_fingerprint=identity.fingerprint(),
                    evidence_digest=changed.stable_digest())
                self.assertEqual(self.evaluate(candidate, changed, changed_grant).status,
                                 preflight.AuthorizationStatus.IDENTITY_MISMATCH)

    def test_database_id_must_be_positive_exact_integer(self):
        _, candidate, snapshot, grant = self.bundle()
        for value in (0, -1, True, "41", 41.0):
            with self.subTest(value=repr(value)):
                identity = dataclasses.replace(snapshot.identity, database_id=value)
                changed = dataclasses.replace(snapshot, identity=identity)
                changed_grant = dataclasses.replace(
                    grant, target_fingerprint=identity.fingerprint(),
                    evidence_digest=changed.stable_digest())
                self.assertEqual(self.evaluate(candidate, changed, changed_grant).status,
                                 preflight.AuthorizationStatus.IDENTITY_MISMATCH)

    def test_connection_fingerprint_must_be_valid_text(self):
        _, candidate, snapshot, grant = self.bundle()
        for value in (None, "", "  ", "fp\nquebrado", 99):
            with self.subTest(value=repr(value)):
                identity = dataclasses.replace(snapshot.identity, connection_fingerprint=value)
                changed = dataclasses.replace(snapshot, identity=identity)
                changed_grant = dataclasses.replace(
                    grant, target_fingerprint=identity.fingerprint(),
                    evidence_digest=changed.stable_digest())
                self.assertEqual(self.evaluate(candidate, changed, changed_grant).status,
                                 preflight.AuthorizationStatus.IDENTITY_MISMATCH)

    def test_inconsistent_capture_and_expiration_are_incomplete(self):
        _, candidate, snapshot, grant = self.bundle()
        changed = dataclasses.replace(
            snapshot, captured_at=NOW - timedelta(minutes=2),
            expires_at=NOW - timedelta(minutes=3))
        changed_grant = dataclasses.replace(grant, evidence_digest=changed.stable_digest())
        self.assertEqual(self.evaluate(candidate, changed, changed_grant,
                                       now=NOW - timedelta(minutes=4)).status,
                         preflight.AuthorizationStatus.INCOMPLETE_EVIDENCE)

    def test_naive_or_unexpected_datetimes_are_controlled_refusals(self):
        _, candidate, snapshot, grant = self.bundle()
        naive = NOW.replace(tzinfo=None)
        cases = (
            (dataclasses.replace(snapshot, captured_at=naive), grant, NOW),
            (dataclasses.replace(snapshot, expires_at=naive), grant, NOW),
            (snapshot, dataclasses.replace(grant, expires_at=naive), NOW),
            (snapshot, grant, "agora"),
        )
        for changed, changed_grant, now in cases:
            with self.subTest(now_type=type(now).__name__):
                if changed is not snapshot:
                    changed_grant = dataclasses.replace(
                        changed_grant, evidence_digest=changed.stable_digest())
                decision = self.evaluate(candidate, changed, changed_grant, now=now)
                self.assertIn(decision.status, {
                    preflight.AuthorizationStatus.INCOMPLETE_EVIDENCE,
                    preflight.AuthorizationStatus.GRANT_EXPIRED,
                })

    def test_mandatory_identity_and_fact_shapes_are_not_truthy_shortcuts(self):
        _, candidate, snapshot, grant = self.bundle()
        blank_identity = dataclasses.replace(snapshot.identity, database_name="   ")
        changed = dataclasses.replace(snapshot, identity=blank_identity)
        changed_grant = dataclasses.replace(
            grant, target_fingerprint=blank_identity.fingerprint(),
            evidence_digest=changed.stable_digest())
        self.assertEqual(self.evaluate(candidate, changed, changed_grant).status,
                         preflight.AuthorizationStatus.IDENTITY_MISMATCH)

        truthy_fact = dataclasses.replace(snapshot, stable_facts=tuple(
            (key, True if key == "DATABASE_STATE" else value)
            for key, value in snapshot.stable_facts))
        truthy_grant = dataclasses.replace(grant, evidence_digest=truthy_fact.stable_digest())
        self.assertEqual(self.evaluate(candidate, truthy_fact, truthy_grant).status,
                         preflight.AuthorizationStatus.INCOMPLETE_EVIDENCE)

        list_facts = dataclasses.replace(snapshot, stable_facts=list(snapshot.stable_facts))
        list_grant = dataclasses.replace(grant, evidence_digest=list_facts.stable_digest())
        self.assertEqual(self.evaluate(candidate, list_facts, list_grant).status,
                         preflight.AuthorizationStatus.INCOMPLETE_EVIDENCE)

    def test_valid_snapshot_and_grant_remain_synthetic_only(self):
        _, candidate, snapshot, grant = self.bundle()
        decision = self.evaluate(candidate, snapshot, grant)
        self.assertEqual(decision.status, preflight.AuthorizationStatus.AUTHORIZED_SYNTHETIC)
        self.assertNotIn(PRIVATE, repr(decision))
        self.assertNotIn(PRIVATE, decision.reason)

    def test_tzinfo_with_null_offset_is_controlled_refusal(self):
        _, candidate, snapshot, grant = self.bundle()
        invalid = datetime(2026, 10, 24, 15, 0, tzinfo=NullOffsetTimezone())
        cases = (
            (dataclasses.replace(snapshot, captured_at=invalid), grant, NOW),
            (dataclasses.replace(snapshot, expires_at=invalid), grant, NOW),
            (snapshot, dataclasses.replace(grant, expires_at=invalid), NOW),
            (snapshot, grant, invalid),
        )
        for changed_snapshot, changed_grant, now in cases:
            with self.subTest(field=(changed_snapshot.captured_at,
                                     changed_snapshot.expires_at,
                                     changed_grant.expires_at, now)):
                decision = self.evaluate(candidate, changed_snapshot,
                                         changed_grant, now=now)
                self.assertIsInstance(decision.status,
                                      preflight.AuthorizationStatus)
                self.assertNotEqual(
                    decision.status,
                    preflight.AuthorizationStatus.AUTHORIZED_SYNTHETIC,
                )

    def test_decision_requires_real_authorization_status_enum(self):
        forged = preflight.AuthorizationDecision(
            "AUTHORIZED_SYNTHETIC", "db.set.read_write")
        validated = preflight.validate_decision_type(forged)
        self.assertEqual(validated.status,
                         preflight.AuthorizationStatus.INVALID_DECISION_TYPE)
        self.assertIsInstance(validated.status, preflight.AuthorizationStatus)

    def test_unhashable_signature_id_is_controlled_refusal(self):
        _, candidate, snapshot, grant = self.bundle()
        malformed = dataclasses.replace(candidate, signature_id=[])
        decision = self.evaluate(malformed, snapshot, grant)
        self.assertEqual(decision.status,
                         preflight.AuthorizationStatus.INVALID_DECISION_TYPE)

    def test_malformed_create_profile_is_rejected_before_digest(self):
        _, candidate, snapshot, grant = self.bundle("db.create.observed_v1")
        malformed_profiles = (
            object(),
            dataclasses.replace(candidate.create_profile, primary=object()),
            dataclasses.replace(
                candidate.create_profile,
                primary=dataclasses.replace(candidate.create_profile.primary,
                                            size=True),
            ),
        )
        for profile in malformed_profiles:
            with self.subTest(profile_type=type(profile).__name__):
                decision = self.evaluate(
                    dataclasses.replace(candidate, create_profile=profile),
                    snapshot,
                    grant,
                )
                self.assertEqual(
                    decision.status,
                    preflight.AuthorizationStatus.INVALID_DECISION_TYPE,
                )

    def test_probe_request_unhashable_key_is_invalid_without_read(self):
        adapter = NoReadAdapter()
        request = preflight.ProbeRequest(
            TARGET,
            PRIVATE + "-execution",
            NOW - timedelta(minutes=1),
            NOW + timedelta(minutes=1),
            ([],),
            (),
        )
        result = preflight.PreflightProbe().collect(adapter, request)
        self.assertEqual(result.status, preflight.ProbeStatus.INVALID_RESPONSE)
        self.assertEqual(adapter.reads, 0)
        self.assertEqual(adapter.close_attempts, 1)


if __name__ == "__main__":
    unittest.main()
