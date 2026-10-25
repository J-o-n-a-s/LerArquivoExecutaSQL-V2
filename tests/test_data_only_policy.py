"""R5B-3B-2B: política DATA-ONLY sintética, pura e sem integração."""

import importlib
import io
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from preflight import AuthorizationStatus, validate_decision_type
from sql_classifier import SQLClassificationError, SQLCommandReader
from tests._application_harness import PRIVATE


NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
VIEWS = tuple(f"view-opaque-{number:02d}" for number in range(32))
RECOGNIZED = "Definição de view reconhecida, mas exige equivalência externa comprovada."


class ExplodingTimezone(tzinfo):
    def utcoffset(self, value):
        raise ValueError("synthetic invalid offset")

    def dst(self, value):
        return None

    def tzname(self, value):
        return "synthetic-invalid"


def policy():
    return importlib.import_module("data_only_policy")


def target(module, suffix="a"):
    return module.SyntheticTargetIdentity(
        f"server-{suffix}", f"database-{suffix}", 7, f"connection-{suffix}")


def request(module, *, mode=None, execution="execution-a", destination=None,
            views=VIEWS, scope=None):
    return module.SyntheticDataOnlyRequest(
        mode if mode is not None else module.SyntheticImportMode.DATA_ONLY,
        scope if scope is not None else module.SyntheticDataOnlyScope.VIEW_DEFINITIONS,
        execution,
        destination if destination is not None else target(module),
        views,
        NOW,
    )


def checks(module, *, missing=None, unresolved=None, failed=None):
    items = []
    for category in module.SyntheticWritePathCategory:
        if category is missing:
            continue
        status = module.SyntheticCheckStatus.VERIFIED
        if category is unresolved:
            status = module.SyntheticCheckStatus.UNRESOLVED
        elif category is failed:
            status = module.SyntheticCheckStatus.FAILED
        items.append(module.SyntheticWritePathCheck(
            category, status, f"evidence-{category.value.lower()}"))
    return tuple(items)


def evidence(module, *, execution="execution-a", destination=None, views=VIEWS,
             proof_checks=None, captured=NOW - timedelta(minutes=1),
             expires=NOW + timedelta(minutes=5)):
    return module.SyntheticWritePathEvidence(
        execution,
        destination if destination is not None else target(module),
        views,
        "synthetic-provider-v1",
        "write-path-contract-v1",
        captured,
        expires,
        proof_checks if proof_checks is not None else checks(module),
    )


def acceptance(module, *, execution="execution-a", destination=None, views=VIEWS,
               scope=None, statement=None, issued=NOW - timedelta(seconds=30),
               expires=NOW + timedelta(minutes=2), approver="approver-opaque"):
    return module.SyntheticHumanAcceptance(
        execution,
        destination if destination is not None else target(module),
        views,
        scope if scope is not None else module.SyntheticDataOnlyScope.VIEW_DEFINITIONS,
        statement if statement is not None else
        module.SyntheticAcceptanceStatement.VIEWS_NOT_RECONSTRUCTED_OR_EQUIVALENT,
        approver,
        issued,
        expires,
    )


class DataOnlyPolicyTests(unittest.TestCase):
    def evaluate(self, module, req=None, proof=None, approval=None):
        return module.evaluate_data_only_view_conditions(
            req if req is not None else request(module),
            proof,
            approval,
        )

    def assert_status(self, decision, status):
        self.assertEqual(decision.status, status)
        self.assertIsInstance(decision.reason, str)
        self.assertNotEqual(decision.reason, "")

    def test_strict_mode_is_fail_closed(self):
        module = policy()
        decision = self.evaluate(
            module,
            request(module, mode=module.SyntheticImportMode.STRICT),
            evidence(module),
            acceptance(module),
        )
        self.assert_status(decision, module.SyntheticPolicyStatus.STRICT_MODE_BLOCKED)

    def test_data_only_without_human_acceptance_is_refused(self):
        module = policy()
        decision = self.evaluate(module, proof=evidence(module))
        self.assert_status(decision, module.SyntheticPolicyStatus.MISSING_HUMAN_ACCEPTANCE)

    def test_missing_write_path_evidence_is_refused(self):
        module = policy()
        decision = self.evaluate(module, approval=acceptance(module))
        self.assert_status(decision, module.SyntheticPolicyStatus.MISSING_WRITE_PATH_EVIDENCE)

    def test_partial_write_path_evidence_is_refused(self):
        module = policy()
        partial = evidence(
            module,
            proof_checks=checks(
                module, missing=module.SyntheticWritePathCategory.TRIGGERS_AND_DEPENDENCIES),
        )
        decision = self.evaluate(module, proof=partial, approval=acceptance(module))
        self.assert_status(decision, module.SyntheticPolicyStatus.INCOMPLETE_WRITE_PATH_EVIDENCE)

    def test_incompatible_write_path_is_refused(self):
        module = policy()
        incompatible = evidence(
            module,
            proof_checks=checks(
                module, failed=module.SyntheticWritePathCategory.COLUMNS_AND_TYPES),
        )
        decision = self.evaluate(module, proof=incompatible, approval=acceptance(module))
        self.assert_status(decision, module.SyntheticPolicyStatus.WRITE_PATH_INCOMPATIBLE)

    def test_evidence_from_another_target_is_refused(self):
        module = policy()
        decision = self.evaluate(
            module,
            proof=evidence(module, destination=target(module, "other")),
            approval=acceptance(module),
        )
        self.assert_status(decision, module.SyntheticPolicyStatus.TARGET_MISMATCH)

    def test_evidence_from_another_execution_is_refused(self):
        module = policy()
        decision = self.evaluate(
            module,
            proof=evidence(module, execution="execution-other"),
            approval=acceptance(module),
        )
        self.assert_status(decision, module.SyntheticPolicyStatus.EXECUTION_MISMATCH)

    def test_view_set_must_have_exactly_32_distinct_bound_items(self):
        module = policy()
        for invalid in (VIEWS[:-1], VIEWS[:-1] + (VIEWS[0],), VIEWS[:-1] + ("*",)):
            with self.subTest(size=len(invalid), last=invalid[-1]):
                decision = self.evaluate(
                    module,
                    request(module, views=invalid),
                    evidence(module, views=invalid),
                    acceptance(module, views=invalid),
                )
                self.assert_status(decision, module.SyntheticPolicyStatus.INVALID_VIEW_SET)

    def test_view_set_mismatch_across_artifacts_is_refused(self):
        module = policy()
        changed = VIEWS[:-1] + ("view-opaque-replacement",)
        decision = self.evaluate(
            module,
            proof=evidence(module, views=changed),
            approval=acceptance(module),
        )
        self.assert_status(decision, module.SyntheticPolicyStatus.VIEW_SET_MISMATCH)

    def test_unresolved_dependency_is_refused(self):
        module = policy()
        unresolved = evidence(
            module,
            proof_checks=checks(
                module, unresolved=module.SyntheticWritePathCategory.VIEW_WRITE_PATH_DEPENDENCIES),
        )
        decision = self.evaluate(module, proof=unresolved, approval=acceptance(module))
        self.assert_status(decision, module.SyntheticPolicyStatus.UNRESOLVED_DEPENDENCY)

    def test_expired_or_future_write_path_evidence_is_refused(self):
        module = policy()
        for captured, expires, expected in (
            (NOW - timedelta(minutes=2), NOW, module.SyntheticPolicyStatus.EVIDENCE_EXPIRED),
            (NOW + timedelta(seconds=1), NOW + timedelta(minutes=2), module.SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE),
        ):
            with self.subTest(expected=expected):
                decision = self.evaluate(
                    module,
                    proof=evidence(module, captured=captured, expires=expires),
                    approval=acceptance(module),
                )
                self.assert_status(decision, expected)

    def test_expired_or_future_human_acceptance_is_refused(self):
        module = policy()
        for issued, expires, expected in (
            (NOW - timedelta(minutes=2), NOW, module.SyntheticPolicyStatus.ACCEPTANCE_EXPIRED),
            (NOW + timedelta(seconds=1), NOW + timedelta(minutes=2), module.SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE),
        ):
            with self.subTest(expected=expected):
                decision = self.evaluate(
                    module,
                    proof=evidence(module),
                    approval=acceptance(module, issued=issued, expires=expires),
                )
                self.assert_status(decision, expected)

    def test_truthy_or_wrong_approval_types_are_refused(self):
        module = policy()
        for invalid in (True, 1, "yes", {"approved": True}, object()):
            with self.subTest(kind=type(invalid).__name__):
                decision = self.evaluate(module, proof=evidence(module), approval=invalid)
                self.assert_status(decision, module.SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE)

    def test_acceptance_cannot_be_reused_for_execution_or_target(self):
        module = policy()
        cases = (
            (acceptance(module, execution="execution-other"), module.SyntheticPolicyStatus.EXECUTION_MISMATCH),
            (acceptance(module, destination=target(module, "other")), module.SyntheticPolicyStatus.TARGET_MISMATCH),
        )
        for approval, expected in cases:
            with self.subTest(expected=expected):
                decision = self.evaluate(module, proof=evidence(module), approval=approval)
                self.assert_status(decision, expected)

    def test_generic_or_non_view_scope_is_never_accepted(self):
        module = policy()
        for invalid_scope in ("*", "ALL", "DATABASE_ADMINISTRATION", "SECURITY"):
            with self.subTest(scope=invalid_scope):
                decision = self.evaluate(
                    module,
                    request(module, scope=invalid_scope),
                    evidence(module),
                    acceptance(module),
                )
                self.assert_status(decision, module.SyntheticPolicyStatus.SCOPE_NOT_SUPPORTED)

    def test_invalid_acceptance_statement_is_refused(self):
        module = policy()
        invalid = acceptance(module, statement="MATCHED")
        decision = self.evaluate(module, proof=evidence(module), approval=invalid)
        self.assert_status(decision, module.SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE)

    def test_complete_synthetic_fixture_has_only_synthetic_positive_status(self):
        module = policy()
        decision = self.evaluate(
            module, proof=evidence(module), approval=acceptance(module))
        self.assert_status(
            decision,
            module.SyntheticPolicyStatus.SYNTHETIC_CONDITIONS_SATISFIED,
        )
        self.assertNotIn("AUTHORIZED", decision.status.value)
        self.assertNotIn("MATCHED", decision.status.value)

    def test_decision_cannot_be_used_as_boolean_or_administrative_grant(self):
        module = policy()
        decision = self.evaluate(
            module, proof=evidence(module), approval=acceptance(module))
        with self.assertRaises(TypeError):
            bool(decision)
        administrative = validate_decision_type(decision)
        self.assertEqual(administrative.status, AuthorizationStatus.INVALID_DECISION_TYPE)

    def test_structures_are_immutable_and_repr_is_redacted(self):
        module = policy()
        secret = PRIVATE + "_POLICY"
        private_target = module.SyntheticTargetIdentity(secret, secret, 7, secret)
        req = request(module, execution=secret, destination=private_target,
                      views=tuple(secret + str(i) for i in range(32)))
        proof = evidence(module, execution=secret, destination=private_target,
                         views=req.view_keys)
        approval = acceptance(module, execution=secret, destination=private_target,
                              views=req.view_keys, approver=secret)
        decision = self.evaluate(module, req, proof, approval)
        for value in (private_target, req, proof, approval, decision, *proof.checks):
            self.assertNotIn(secret, repr(value))
        with self.assertRaises(FrozenInstanceError):
            req.execution_id = "changed"

    def test_invalid_request_and_evidence_structures_fail_closed(self):
        module = policy()
        bad_request = replace(request(module), execution_id="*")
        self.assert_status(
            self.evaluate(module, bad_request, evidence(module), acceptance(module)),
            module.SyntheticPolicyStatus.INVALID_REQUEST,
        )
        bad_evidence = replace(evidence(module), origin_id="*")
        self.assert_status(
            self.evaluate(module, proof=bad_evidence, approval=acceptance(module)),
            module.SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE,
        )

    def test_datetime_with_failing_utcoffset_is_refused_without_exception(self):
        module = policy()
        invalid_time = datetime(2026, 10, 24, 12, 0, tzinfo=ExplodingTimezone())
        req = request(module)
        proof = evidence(module)
        approval = acceptance(module)
        cases = (
            (
                "request",
                replace(req, evaluated_at=invalid_time),
                proof,
                approval,
                module.SyntheticPolicyStatus.INVALID_REQUEST,
            ),
            (
                "evidence-captured",
                req,
                replace(proof, captured_at=invalid_time),
                approval,
                module.SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE,
            ),
            (
                "evidence-expiry",
                req,
                replace(proof, expires_at=invalid_time),
                approval,
                module.SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE,
            ),
            (
                "acceptance-issued",
                req,
                proof,
                replace(approval, issued_at=invalid_time),
                module.SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE,
            ),
            (
                "acceptance-expiry",
                req,
                proof,
                replace(approval, expires_at=invalid_time),
                module.SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE,
            ),
        )
        for label, candidate_request, candidate_proof, candidate_approval, expected in cases:
            with self.subTest(label=label):
                decision = self.evaluate(
                    module, candidate_request, candidate_proof, candidate_approval)
                self.assert_status(decision, expected)

    def test_control_characters_in_identity_or_approval_are_refused(self):
        module = policy()
        for control in ("\0", "\n", "\t", "\x7f", "\x85"):
            with self.subTest(location="target", codepoint=ord(control)):
                invalid_target = module.SyntheticTargetIdentity(
                    f"server{control}internal", "database-a", 7, "connection-a")
                req = request(module, destination=invalid_target)
                decision = self.evaluate(
                    module,
                    req,
                    evidence(module, destination=invalid_target),
                    acceptance(module, destination=invalid_target),
                )
                self.assert_status(decision, module.SyntheticPolicyStatus.INVALID_REQUEST)

            with self.subTest(location="approval", codepoint=ord(control)):
                decision = self.evaluate(
                    module,
                    proof=evidence(module),
                    approval=acceptance(
                        module, approver=f"approver{control}internal"),
                )
                self.assert_status(
                    decision,
                    module.SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE,
                )

    def test_module_has_no_operational_integration_or_sql(self):
        module = policy()
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "pyodbc", "SQLCommandReader", "cursor.", ".execute(", ".commit(",
            ".rollback(", "CREATE VIEW", "SELECT ", "allow_all",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_sql_command_reader_remains_fail_closed_for_views(self):
        text = "CREATE VIEW [dbo].[synthetic] AS SELECT 1 AS [value]"
        reader = SQLCommandReader(io.StringIO(text), target_database="destination")
        with self.assertRaises(SQLClassificationError) as caught:
            list(reader)
        self.assertEqual(caught.exception.reason, RECOGNIZED)
        self.assertEqual(reader.ignored_lines, 0)


if __name__ == "__main__":
    unittest.main()
