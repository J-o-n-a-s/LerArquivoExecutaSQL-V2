"""R5B-2D-1: núcleo sintético de decisão, sem integração operacional."""

import dataclasses
import io
import unittest
from datetime import datetime, timedelta, timezone

try:
    import preflight
except ModuleNotFoundError:
    preflight = None

from sql_classifier import SQLClassificationError, SQLCommandReader, _commands
from tests.test_database_administration import (
    ALTER_CLAUSES,
    DESTINATION,
    EXTERNAL,
    RECOGNIZED,
    alter_database,
    create_database,
)


PRIVATE = "SEGREDO_AUTORIZACAO_NAO_EXIBIR"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

EXPECTED = (
    ("db.create.observed_v1", "CREATE", None, None),
    ("db.set.compatibility_level.150", "ALTER", "COMPATIBILITY_LEVEL", "150"),
    ("db.set.ansi_null_default.off", "ALTER", "ANSI_NULL_DEFAULT", "OFF"),
    ("db.set.ansi_nulls.off", "ALTER", "ANSI_NULLS", "OFF"),
    ("db.set.ansi_padding.off", "ALTER", "ANSI_PADDING", "OFF"),
    ("db.set.ansi_warnings.off", "ALTER", "ANSI_WARNINGS", "OFF"),
    ("db.set.arithabort.off", "ALTER", "ARITHABORT", "OFF"),
    ("db.set.concat_null_yields_null.off", "ALTER", "CONCAT_NULL_YIELDS_NULL", "OFF"),
    ("db.set.numeric_roundabort.off", "ALTER", "NUMERIC_ROUNDABORT", "OFF"),
    ("db.set.quoted_identifier.off", "ALTER", "QUOTED_IDENTIFIER", "OFF"),
    ("db.set.cursor_close_on_commit.off", "ALTER", "CURSOR_CLOSE_ON_COMMIT", "OFF"),
    ("db.set.cursor_default.global", "ALTER", "CURSOR_DEFAULT", "GLOBAL"),
    ("db.set.recursive_triggers.off", "ALTER", "RECURSIVE_TRIGGERS", "OFF"),
    ("db.set.auto_close.off", "ALTER", "AUTO_CLOSE", "OFF"),
    ("db.set.auto_shrink.off", "ALTER", "AUTO_SHRINK", "OFF"),
    ("db.set.auto_update_statistics.on", "ALTER", "AUTO_UPDATE_STATISTICS", "ON"),
    ("db.set.auto_update_statistics_async.off", "ALTER", "AUTO_UPDATE_STATISTICS_ASYNC", "OFF"),
    ("db.set.date_correlation_optimization.off", "ALTER", "DATE_CORRELATION_OPTIMIZATION", "OFF"),
    ("db.set.parameterization.simple", "ALTER", "PARAMETERIZATION", "SIMPLE"),
    ("db.set.query_store.off", "ALTER", "QUERY_STORE", "OFF"),
    ("db.set.allow_snapshot_isolation.off", "ALTER", "ALLOW_SNAPSHOT_ISOLATION", "OFF"),
    ("db.set.read_committed_snapshot.off", "ALTER", "READ_COMMITTED_SNAPSHOT", "OFF"),
    ("db.set.disable_broker", "ALTER", "DISABLE_BROKER", None),
    ("db.set.honor_broker_priority.off", "ALTER", "HONOR_BROKER_PRIORITY", "OFF"),
    ("db.set.trustworthy.off", "ALTER", "TRUSTWORTHY", "OFF"),
    ("db.set.db_chaining.off", "ALTER", "DB_CHAINING", "OFF"),
    ("db.set.recovery.full", "ALTER", "RECOVERY", "FULL"),
    ("db.set.page_verify.checksum", "ALTER", "PAGE_VERIFY", "CHECKSUM"),
    ("db.set.target_recovery_time.60_seconds", "ALTER", "TARGET_RECOVERY_TIME", "60 SECONDS"),
    ("db.set.delayed_durability.disabled", "ALTER", "DELAYED_DURABILITY", "DISABLED"),
    ("db.set.accelerated_database_recovery.off", "ALTER", "ACCELERATED_DATABASE_RECOVERY", "OFF"),
    ("db.set.multi_user", "ALTER", "MULTI_USER", None),
    ("db.set.filestream_non_transacted_access.off", "ALTER", "FILESTREAM_NON_TRANSACTED_ACCESS", "OFF"),
    ("db.set.read_write", "ALTER", "READ_WRITE", None),
)

CLAUSE_IDS = dict(zip(ALTER_CLAUSES, (row[0] for row in EXPECTED[1:])))


class AuthorizationCoreTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(preflight, "preflight.py ainda não existe: RED esperado.")
        return preflight

    def profile(self, *, data_size=8192):
        api = self.api()
        return api.CreateDatabaseProfile(
            containment="NONE",
            primary=api.DatabaseFileProfile("PRIMARY", "UNICODE", "UNICODE", data_size, "KB", None, "UNLIMITED", 65536, "KB"),
            log=api.DatabaseFileProfile("LOG", "UNICODE", "UNICODE", 8192, "KB", 2048, "GB", 65536, "KB"),
            catalog_collation="DATABASE_DEFAULT",
        )

    def candidate(self, definition, *, target=DESTINATION, profile=None):
        api = self.api()
        if definition.family.value == "CREATE" and profile is None:
            profile = self.profile()
        return api.DatabaseInstructionFacts(
            definition.signature_id, definition.family, target, 7,
            definition.option, definition.requested_value, profile,
        )

    def snapshot(self, definition, *, target=DESTINATION, complete=True,
                 major=None, session_value=None, operational=None):
        api = self.api()
        if major is None:
            major = 13 if definition.forced_on_since_major else 16
        identity = api.TargetIdentity(PRIVATE, target, 73, PRIVATE + "-connection")
        facts = {
            "DATABASE_STATE": "ONLINE",
            "UPDATEABILITY": "READ_WRITE",
            "USER_ACCESS": "MULTI_USER",
            "EVIDENCE_PERMISSIONS": "COMPLETE",
        }
        if definition.family.value == "CREATE":
            facts.update({
                "DATABASE_EXISTS": "YES",
                "CONTAINMENT": "NONE",
                "CATALOG_COLLATION": "DATABASE_DEFAULT",
                "STORAGE_READY": "YES",
                "SCHEMA_READY": "YES",
            })
        else:
            facts[definition.evidence_key] = definition.evidence_value
        session = {}
        if definition.requires_session_match:
            session[definition.option] = definition.requested_value if session_value is None else session_value
        return api.VerifiedSnapshot(
            identity=identity,
            execution_id=PRIVATE + "-execution",
            captured_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
            sql_major_version=major,
            version_supported=True,
            complete=complete,
            stable_facts=tuple(sorted(facts.items())),
            session_facts=tuple(sorted(session.items())),
            operational=operational,
        )

    def grant(self, definition, candidate, snapshot, *, create_approved=None):
        api = self.api()
        if create_approved is None:
            create_approved = definition.family.value == "CREATE"
        return api.SignatureGrant(
            signature_id=definition.signature_id,
            execution_id=snapshot.execution_id,
            target_fingerprint=snapshot.identity.fingerprint(),
            evidence_digest=snapshot.stable_digest(),
            requested_value=definition.requested_value,
            expires_at=NOW + timedelta(minutes=3),
            create_profile_digest=candidate.create_profile.digest() if candidate.create_profile else None,
            create_approved=create_approved,
        )

    def decision(self, definition, *, candidate=None, snapshot=None, grant=True):
        api = self.api()
        candidate = candidate or self.candidate(definition)
        snapshot = snapshot or self.snapshot(definition)
        authorization = self.grant(definition, candidate, snapshot) if grant is True else grant
        return api.evaluate_database_authorization(candidate, snapshot, authorization, now=NOW)

    def capture(self, text):
        captured = []
        with self.assertRaises(SQLClassificationError) as caught:
            list(_commands(text, 1, validate_database=captured.append))
        self.assertEqual(caught.exception.reason, RECOGNIZED)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_exactly_34_stable_unique_definitions(self):
        api = self.api()
        actual = tuple((item.signature_id, item.family.value, item.option, item.requested_value)
                       for item in api.DATABASE_SIGNATURES)
        self.assertEqual(actual, EXPECTED)
        self.assertEqual(len({row[0] for row in actual}), 34)

    def test_parser_attaches_all_34_identifiers_without_changing_refusal(self):
        self.api()
        statements = (create_database(), *(alter_database(clause) for clause in ALTER_CLAUSES))
        expected_ids = (EXPECTED[0][0], *(CLAUSE_IDS[clause] for clause in ALTER_CLAUSES))
        for statement, expected_id in zip(statements, expected_ids):
            with self.subTest(signature_id=expected_id):
                instruction = self.capture(statement)
                self.assertEqual(instruction.signature_id, expected_id)
                self.assertEqual(instruction.target, DESTINATION)
                self.assertEqual(instruction.line, 1)

    def test_family_option_and_requested_value_are_preserved(self):
        self.api()
        create = self.capture(create_database())
        self.assertEqual((create.family.value, create.option, create.requested_value), ("CREATE", None, None))
        for clause, expected in zip(ALTER_CLAUSES, EXPECTED[1:]):
            with self.subTest(clause=clause):
                instruction = self.capture(alter_database(clause))
                self.assertEqual((instruction.family.value, instruction.option, instruction.requested_value), expected[1:])

    def test_create_profile_binds_numeric_structure_without_paths(self):
        first = self.capture(create_database())
        second = self.capture(create_database().replace("SIZE = 8192 KB", "SIZE = 16384 KB", 1))
        self.assertNotEqual(first.create_profile, second.create_profile)
        self.assertNotEqual(first.create_profile.digest(), second.create_profile.digest())
        for private in (DESTINATION, "NomeDadosPrivado", "NomeLogPrivado", "X:\\privado"):
            self.assertNotIn(private, repr(first))
            self.assertNotIn(private, repr(first.create_profile))

    def test_each_signature_can_be_authorized_only_with_complete_synthetic_proof(self):
        api = self.api()
        for definition in api.DATABASE_SIGNATURES:
            with self.subTest(signature_id=definition.signature_id):
                result = self.decision(definition)
                self.assertEqual(result.status, api.AuthorizationStatus.AUTHORIZED_SYNTHETIC)

    def test_default_application_path_still_refuses_all_34(self):
        statements = (create_database(), *(alter_database(clause) for clause in ALTER_CLAUSES))
        for statement in statements:
            with self.subTest(prefix=statement[:24]):
                reader = SQLCommandReader(io.StringIO(statement), target_database=DESTINATION)
                with self.assertRaises(SQLClassificationError) as caught:
                    list(reader)
                self.assertEqual(caught.exception.reason, RECOGNIZED)

    def test_target_and_identity_mismatch_are_distinct(self):
        api = self.api()
        definition = api.signature_by_id("db.set.read_write")
        candidate = self.candidate(definition, target=EXTERNAL)
        self.assertEqual(self.decision(definition, candidate=candidate).status, api.AuthorizationStatus.TARGET_MISMATCH)
        candidate = self.candidate(definition)
        snapshot = self.snapshot(definition)
        grant = dataclasses.replace(self.grant(definition, candidate, snapshot), target_fingerprint="wrong")
        self.assertEqual(self.decision(definition, candidate=candidate, snapshot=snapshot, grant=grant).status,
                         api.AuthorizationStatus.IDENTITY_MISMATCH)

    def test_option_and_requested_value_mismatch_are_denied(self):
        api = self.api()
        definition = api.signature_by_id("db.set.recovery.full")
        candidate = self.candidate(definition)
        for changed in (dataclasses.replace(candidate, option="PAGE_VERIFY"),
                        dataclasses.replace(candidate, requested_value="SIMPLE")):
            with self.subTest(changed=changed.signature_id):
                self.assertEqual(self.decision(definition, candidate=changed).status,
                                 api.AuthorizationStatus.VALUE_MISMATCH)

    def test_missing_and_incomplete_snapshot_are_denied(self):
        api = self.api()
        definition = api.signature_by_id("db.set.page_verify.checksum")
        candidate = self.candidate(definition)
        self.assertEqual(api.evaluate_database_authorization(candidate, None, None, now=NOW).status,
                         api.AuthorizationStatus.MISSING_EVIDENCE)
        snapshot = self.snapshot(definition, complete=False)
        self.assertEqual(self.decision(definition, candidate=candidate, snapshot=snapshot).status,
                         api.AuthorizationStatus.INCOMPLETE_EVIDENCE)

    def test_missing_required_fact_is_denied(self):
        api = self.api()
        definition = api.signature_by_id("db.set.query_store.off")
        snapshot = self.snapshot(definition)
        snapshot = dataclasses.replace(snapshot, stable_facts=tuple(
            pair for pair in snapshot.stable_facts if pair[0] != definition.evidence_key))
        self.assertEqual(self.decision(definition, snapshot=snapshot).status,
                         api.AuthorizationStatus.MISSING_EVIDENCE)

    def test_unknown_or_unsupported_version_is_denied(self):
        api = self.api()
        definition = api.signature_by_id("db.set.read_write")
        for major, supported in ((None, None), (16, None), (99, False), (99, True)):
            with self.subTest(major=major, supported=supported):
                snapshot = dataclasses.replace(self.snapshot(definition),
                                               sql_major_version=major,
                                               version_supported=supported)
                self.assertEqual(self.decision(definition, snapshot=snapshot).status,
                                 api.AuthorizationStatus.UNSUPPORTED_VERSION)

    def test_expired_snapshot_and_grant_are_distinguished(self):
        api = self.api()
        definition = api.signature_by_id("db.set.read_write")
        snapshot = dataclasses.replace(self.snapshot(definition), expires_at=NOW - timedelta(seconds=1))
        self.assertEqual(self.decision(definition, snapshot=snapshot).status,
                         api.AuthorizationStatus.EVIDENCE_EXPIRED)
        snapshot = self.snapshot(definition)
        candidate = self.candidate(definition)
        grant = dataclasses.replace(self.grant(definition, candidate, snapshot),
                                    expires_at=NOW - timedelta(seconds=1))
        self.assertEqual(self.decision(definition, candidate=candidate, snapshot=snapshot, grant=grant).status,
                         api.AuthorizationStatus.GRANT_EXPIRED)

    def test_create_requires_specific_approval_and_profile_match(self):
        api = self.api()
        definition = api.signature_by_id("db.create.observed_v1")
        candidate = self.candidate(definition)
        snapshot = self.snapshot(definition)
        self.assertEqual(self.decision(definition, candidate=candidate, snapshot=snapshot, grant=None).status,
                         api.AuthorizationStatus.APPROVAL_REQUIRED)
        unapproved = self.grant(definition, candidate, snapshot, create_approved=False)
        self.assertEqual(self.decision(definition, candidate=candidate, snapshot=snapshot, grant=unapproved).status,
                         api.AuthorizationStatus.APPROVAL_REQUIRED)
        other = dataclasses.replace(candidate, create_profile=self.profile(data_size=16384))
        grant = self.grant(definition, candidate, snapshot)
        self.assertEqual(self.decision(definition, candidate=other, snapshot=snapshot, grant=grant).status,
                         api.AuthorizationStatus.VALUE_MISMATCH)

    def test_partial_authorization_cannot_authorize_another_signature(self):
        api = self.api()
        allowed = api.signature_by_id("db.set.read_write")
        denied = api.signature_by_id("db.set.multi_user")
        allowed_candidate = self.candidate(allowed)
        allowed_snapshot = self.snapshot(allowed)
        grant = self.grant(allowed, allowed_candidate, allowed_snapshot)
        denied_candidate = self.candidate(denied)
        denied_snapshot = self.snapshot(denied)
        self.assertEqual(self.decision(denied, candidate=denied_candidate,
                                       snapshot=denied_snapshot, grant=grant).status,
                         api.AuthorizationStatus.SIGNATURE_NOT_AUTHORIZED)
        with self.assertRaises(ValueError):
            dataclasses.replace(grant, signature_id="*")

    def test_invalid_decision_type_is_never_interpreted_as_authorized(self):
        api = self.api()
        decision = api.validate_decision_type("AUTHORIZED")
        self.assertEqual(decision.status, api.AuthorizationStatus.INVALID_DECISION_TYPE)
        with self.assertRaises(TypeError):
            bool(decision)

    def test_version_dependent_ansi_behavior_never_equates_on_to_off(self):
        api = self.api()
        ids = ("db.set.ansi_nulls.off", "db.set.ansi_padding.off",
               "db.set.concat_null_yields_null.off")
        for signature_id in ids:
            definition = api.signature_by_id(signature_id)
            with self.subTest(signature_id=signature_id):
                modern = self.snapshot(definition, major=14, session_value="ON")
                self.assertEqual(self.decision(definition, snapshot=modern).status,
                                 api.AuthorizationStatus.VERSION_DEPENDENT_BEHAVIOR)
                legacy = self.snapshot(definition, major=13, session_value="OFF")
                self.assertEqual(self.decision(definition, snapshot=legacy).status,
                                 api.AuthorizationStatus.AUTHORIZED_SYNTHETIC)

    def test_persisted_and_effective_session_values_are_separate(self):
        api = self.api()
        definition = api.signature_by_id("db.set.ansi_warnings.off")
        snapshot = self.snapshot(definition, session_value="ON")
        self.assertEqual(dict(snapshot.stable_facts)[definition.evidence_key], "OFF")
        self.assertEqual(dict(snapshot.session_facts)[definition.option], "ON")
        self.assertEqual(self.decision(definition, snapshot=snapshot).status,
                         api.AuthorizationStatus.VALUE_MISMATCH)

    def test_mutable_operational_indicators_do_not_change_stable_digest(self):
        api = self.api()
        definition = api.signature_by_id("db.set.read_write")
        first = api.OperationalIndicators(NOW, 10_000, 4_000, 12.5)
        second = api.OperationalIndicators(NOW + timedelta(minutes=1), 9_000, 5_000, 45.0)
        one = self.snapshot(definition, operational=first)
        two = self.snapshot(definition, operational=second)
        self.assertNotEqual(one.operational, two.operational)
        self.assertEqual(one.stable_digest(), two.stable_digest())

    def test_structures_are_frozen_and_public_representations_are_redacted(self):
        api = self.api()
        definition = api.signature_by_id("db.set.read_write")
        candidate = self.candidate(definition, target=PRIVATE)
        snapshot = self.snapshot(definition, target=PRIVATE)
        decision = self.decision(definition, candidate=candidate, snapshot=snapshot)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            candidate.target = DESTINATION
        for rendered in (repr(candidate), repr(snapshot), repr(snapshot.identity), repr(decision), decision.reason):
            self.assertNotIn(PRIVATE, rendered)

    def test_grant_is_bound_to_execution_and_evidence(self):
        api = self.api()
        definition = api.signature_by_id("db.set.read_write")
        candidate = self.candidate(definition)
        snapshot = self.snapshot(definition)
        original = self.grant(definition, candidate, snapshot)
        for grant in (dataclasses.replace(original, execution_id="other"),
                      dataclasses.replace(original, evidence_digest="other")):
            with self.subTest(grant=repr(grant)):
                self.assertEqual(self.decision(definition, candidate=candidate,
                                               snapshot=snapshot, grant=grant).status,
                                 api.AuthorizationStatus.IDENTITY_MISMATCH)


if __name__ == "__main__":
    unittest.main()
