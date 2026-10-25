"""Política sintética e offline para o escopo restrito de definições de views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import NoReturn


class SyntheticImportMode(Enum):
    STRICT = "STRICT"
    DATA_ONLY = "DATA_ONLY"


class SyntheticDataOnlyScope(Enum):
    VIEW_DEFINITIONS = "VIEW_DEFINITIONS"


class SyntheticWritePathCategory(Enum):
    TARGET_TABLES = "TARGET_TABLES"
    COLUMNS_AND_TYPES = "COLUMNS_AND_TYPES"
    IDENTITY_AND_IDENTITY_INSERT = "IDENTITY_AND_IDENTITY_INSERT"
    CONSTRAINTS_AND_UNIQUENESS = "CONSTRAINTS_AND_UNIQUENESS"
    DEFAULTS_AND_COMPUTED_COLUMNS = "DEFAULTS_AND_COMPUTED_COLUMNS"
    TRIGGERS_AND_DEPENDENCIES = "TRIGGERS_AND_DEPENDENCIES"
    SESSION_OPTIONS = "SESSION_OPTIONS"
    DATABASE_IDENTITY_AND_CONTEXT = "DATABASE_IDENTITY_AND_CONTEXT"
    VIEW_WRITE_PATH_DEPENDENCIES = "VIEW_WRITE_PATH_DEPENDENCIES"


class SyntheticCheckStatus(Enum):
    VERIFIED = "VERIFIED"
    UNRESOLVED = "UNRESOLVED"
    FAILED = "FAILED"


class SyntheticAcceptanceStatement(Enum):
    VIEWS_NOT_RECONSTRUCTED_OR_EQUIVALENT = "VIEWS_NOT_RECONSTRUCTED_OR_EQUIVALENT"


class SyntheticPolicyStatus(Enum):
    STRICT_MODE_BLOCKED = "STRICT_MODE_BLOCKED"
    MISSING_HUMAN_ACCEPTANCE = "MISSING_HUMAN_ACCEPTANCE"
    MISSING_WRITE_PATH_EVIDENCE = "MISSING_WRITE_PATH_EVIDENCE"
    INCOMPLETE_WRITE_PATH_EVIDENCE = "INCOMPLETE_WRITE_PATH_EVIDENCE"
    WRITE_PATH_INCOMPATIBLE = "WRITE_PATH_INCOMPATIBLE"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    EXECUTION_MISMATCH = "EXECUTION_MISMATCH"
    INVALID_VIEW_SET = "INVALID_VIEW_SET"
    VIEW_SET_MISMATCH = "VIEW_SET_MISMATCH"
    UNRESOLVED_DEPENDENCY = "UNRESOLVED_DEPENDENCY"
    EVIDENCE_EXPIRED = "EVIDENCE_EXPIRED"
    INVALID_WRITE_PATH_EVIDENCE = "INVALID_WRITE_PATH_EVIDENCE"
    ACCEPTANCE_EXPIRED = "ACCEPTANCE_EXPIRED"
    INVALID_HUMAN_ACCEPTANCE = "INVALID_HUMAN_ACCEPTANCE"
    SCOPE_NOT_SUPPORTED = "SCOPE_NOT_SUPPORTED"
    SYNTHETIC_CONDITIONS_SATISFIED = "SYNTHETIC_CONDITIONS_SATISFIED"
    INVALID_REQUEST = "INVALID_REQUEST"


_REASONS = {
    SyntheticPolicyStatus.STRICT_MODE_BLOCKED:
        "O modo estrito permanece bloqueado para definições de views.",
    SyntheticPolicyStatus.MISSING_HUMAN_ACCEPTANCE:
        "A aceitação humana vinculada à execução está ausente.",
    SyntheticPolicyStatus.MISSING_WRITE_PATH_EVIDENCE:
        "A evidência sintética do caminho de escrita está ausente.",
    SyntheticPolicyStatus.INCOMPLETE_WRITE_PATH_EVIDENCE:
        "A evidência sintética não cobre todas as categorias exigidas.",
    SyntheticPolicyStatus.WRITE_PATH_INCOMPATIBLE:
        "Ao menos uma categoria do caminho de escrita falhou.",
    SyntheticPolicyStatus.TARGET_MISMATCH:
        "O alvo da evidência ou aceitação não corresponde à solicitação.",
    SyntheticPolicyStatus.EXECUTION_MISMATCH:
        "A execução da evidência ou aceitação não corresponde à solicitação.",
    SyntheticPolicyStatus.INVALID_VIEW_SET:
        "O conjunto solicitado deve conter exatamente 32 itens distintos e vinculados.",
    SyntheticPolicyStatus.VIEW_SET_MISMATCH:
        "O conjunto de views não corresponde exatamente ao da solicitação.",
    SyntheticPolicyStatus.UNRESOLVED_DEPENDENCY:
        "Ao menos uma dependência relevante permanece sem resolução.",
    SyntheticPolicyStatus.EVIDENCE_EXPIRED:
        "A evidência sintética expirou.",
    SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE:
        "A estrutura da evidência sintética é inválida.",
    SyntheticPolicyStatus.ACCEPTANCE_EXPIRED:
        "A aceitação humana expirou.",
    SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE:
        "A estrutura da aceitação humana é inválida.",
    SyntheticPolicyStatus.SCOPE_NOT_SUPPORTED:
        "O escopo solicitado não é suportado pela política restrita.",
    SyntheticPolicyStatus.SYNTHETIC_CONDITIONS_SATISFIED:
        "As condições sintéticas foram satisfeitas; nenhuma operação foi liberada.",
    SyntheticPolicyStatus.INVALID_REQUEST:
        "A estrutura da solicitação sintética é inválida.",
}


@dataclass(frozen=True, repr=False)
class SyntheticTargetIdentity:
    server_instance: str
    database_name: str
    database_id: int
    connection_fingerprint: str

    def __repr__(self) -> str:
        return "SyntheticTargetIdentity(<redacted>)"


@dataclass(frozen=True, repr=False)
class SyntheticDataOnlyRequest:
    mode: SyntheticImportMode
    scope: SyntheticDataOnlyScope
    execution_id: str
    target: SyntheticTargetIdentity
    view_keys: tuple[str, ...]
    evaluated_at: datetime

    def __repr__(self) -> str:
        return "SyntheticDataOnlyRequest(<redacted>)"


@dataclass(frozen=True, repr=False)
class SyntheticWritePathCheck:
    category: SyntheticWritePathCategory
    status: SyntheticCheckStatus
    evidence_id: str

    def __repr__(self) -> str:
        category = getattr(self.category, "value", "invalid")
        status = getattr(self.status, "value", "invalid")
        return f"SyntheticWritePathCheck(category={category!r}, status={status!r})"


@dataclass(frozen=True, repr=False)
class SyntheticWritePathEvidence:
    execution_id: str
    target: SyntheticTargetIdentity
    view_keys: tuple[str, ...]
    origin_id: str
    contract_version: str
    captured_at: datetime
    expires_at: datetime
    checks: tuple[SyntheticWritePathCheck, ...]

    def __repr__(self) -> str:
        return f"SyntheticWritePathEvidence(check_count={_safe_length(self.checks)})"


@dataclass(frozen=True, repr=False)
class SyntheticHumanAcceptance:
    execution_id: str
    target: SyntheticTargetIdentity
    view_keys: tuple[str, ...]
    scope: SyntheticDataOnlyScope
    statement: SyntheticAcceptanceStatement
    approver_id: str
    issued_at: datetime
    expires_at: datetime

    def __repr__(self) -> str:
        return "SyntheticHumanAcceptance(<redacted>)"


@dataclass(frozen=True, repr=False)
class SyntheticDataOnlyDecision:
    status: SyntheticPolicyStatus

    @property
    def reason(self) -> str:
        return _REASONS[self.status]

    def __bool__(self) -> NoReturn:
        raise TypeError("SyntheticDataOnlyDecision não possui semântica booleana")

    def __repr__(self) -> str:
        status = getattr(self.status, "value", "invalid")
        return f"SyntheticDataOnlyDecision(status={status!r})"


def _decision(status: SyntheticPolicyStatus) -> SyntheticDataOnlyDecision:
    return SyntheticDataOnlyDecision(status)


def _safe_length(value: object) -> int | str:
    try:
        return len(value)  # type: ignore[arg-type]
    except (TypeError, AttributeError):
        return "invalid"


def _is_bound_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and "*" not in value
        and "?" not in value
        and not any(ord(character) < 32 or 127 <= ord(character) <= 159
                    for character in value)
    )


def _is_aware(value: object) -> bool:
    if type(value) is not datetime or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except Exception:
        return False


def _is_valid_target(value: object) -> bool:
    return (
        type(value) is SyntheticTargetIdentity
        and _is_bound_text(value.server_instance)
        and _is_bound_text(value.database_name)
        and type(value.database_id) is int
        and value.database_id > 0
        and _is_bound_text(value.connection_fingerprint)
    )


def _is_valid_view_set(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 32
        and all(_is_bound_text(item) for item in value)
        and len(set(value)) == 32
    )


def _is_valid_request(value: SyntheticDataOnlyRequest) -> bool:
    return (
        _is_bound_text(value.execution_id)
        and _is_valid_target(value.target)
        and _is_aware(value.evaluated_at)
    )


def _check_structure(
        value: object,
) -> tuple[bool, set[SyntheticWritePathCategory], set[SyntheticCheckStatus]]:
    if type(value) is not tuple:
        return False, set(), set()
    categories: set[SyntheticWritePathCategory] = set()
    statuses: set[SyntheticCheckStatus] = set()
    for check in value:
        if (
            type(check) is not SyntheticWritePathCheck
            or type(check.category) is not SyntheticWritePathCategory
            or type(check.status) is not SyntheticCheckStatus
            or not _is_bound_text(check.evidence_id)
            or check.category in categories
        ):
            return False, set(), set()
        categories.add(check.category)
        statuses.add(check.status)
    return True, categories, statuses


def evaluate_data_only_view_conditions(
    request: object,
    evidence: object,
    acceptance: object,
) -> SyntheticDataOnlyDecision:
    """Avalia somente artefatos imutáveis; não realiza efeitos externos."""
    if type(request) is not SyntheticDataOnlyRequest:
        return _decision(SyntheticPolicyStatus.INVALID_REQUEST)
    if request.mode is SyntheticImportMode.STRICT:
        return _decision(SyntheticPolicyStatus.STRICT_MODE_BLOCKED)
    if request.mode is not SyntheticImportMode.DATA_ONLY:
        return _decision(SyntheticPolicyStatus.INVALID_REQUEST)
    if request.scope is not SyntheticDataOnlyScope.VIEW_DEFINITIONS:
        return _decision(SyntheticPolicyStatus.SCOPE_NOT_SUPPORTED)
    if not _is_valid_request(request):
        return _decision(SyntheticPolicyStatus.INVALID_REQUEST)
    if not _is_valid_view_set(request.view_keys):
        return _decision(SyntheticPolicyStatus.INVALID_VIEW_SET)

    if evidence is None:
        return _decision(SyntheticPolicyStatus.MISSING_WRITE_PATH_EVIDENCE)
    if type(evidence) is not SyntheticWritePathEvidence:
        return _decision(SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE)
    if (
        not _is_bound_text(evidence.execution_id)
        or not _is_valid_target(evidence.target)
        or not _is_valid_view_set(evidence.view_keys)
        or not _is_bound_text(evidence.origin_id)
        or not _is_bound_text(evidence.contract_version)
        or not _is_aware(evidence.captured_at)
        or not _is_aware(evidence.expires_at)
        or evidence.captured_at >= evidence.expires_at
    ):
        return _decision(SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE)
    if evidence.execution_id != request.execution_id:
        return _decision(SyntheticPolicyStatus.EXECUTION_MISMATCH)
    if evidence.target != request.target:
        return _decision(SyntheticPolicyStatus.TARGET_MISMATCH)
    if evidence.view_keys != request.view_keys:
        return _decision(SyntheticPolicyStatus.VIEW_SET_MISMATCH)
    if evidence.captured_at > request.evaluated_at:
        return _decision(SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE)
    if evidence.expires_at <= request.evaluated_at:
        return _decision(SyntheticPolicyStatus.EVIDENCE_EXPIRED)
    valid_checks, categories, statuses = _check_structure(evidence.checks)
    if not valid_checks:
        return _decision(SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE)
    if categories != set(SyntheticWritePathCategory):
        return _decision(SyntheticPolicyStatus.INCOMPLETE_WRITE_PATH_EVIDENCE)
    if SyntheticCheckStatus.UNRESOLVED in statuses:
        return _decision(SyntheticPolicyStatus.UNRESOLVED_DEPENDENCY)
    if SyntheticCheckStatus.FAILED in statuses:
        return _decision(SyntheticPolicyStatus.WRITE_PATH_INCOMPATIBLE)
    if statuses != {SyntheticCheckStatus.VERIFIED}:
        return _decision(SyntheticPolicyStatus.INVALID_WRITE_PATH_EVIDENCE)

    if acceptance is None:
        return _decision(SyntheticPolicyStatus.MISSING_HUMAN_ACCEPTANCE)
    if type(acceptance) is not SyntheticHumanAcceptance:
        return _decision(SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE)
    if (
        not _is_bound_text(acceptance.execution_id)
        or not _is_valid_target(acceptance.target)
        or not _is_valid_view_set(acceptance.view_keys)
        or acceptance.scope is not SyntheticDataOnlyScope.VIEW_DEFINITIONS
        or acceptance.statement
        is not SyntheticAcceptanceStatement.VIEWS_NOT_RECONSTRUCTED_OR_EQUIVALENT
        or not _is_bound_text(acceptance.approver_id)
        or not _is_aware(acceptance.issued_at)
        or not _is_aware(acceptance.expires_at)
        or acceptance.issued_at >= acceptance.expires_at
    ):
        return _decision(SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE)
    if acceptance.execution_id != request.execution_id:
        return _decision(SyntheticPolicyStatus.EXECUTION_MISMATCH)
    if acceptance.target != request.target:
        return _decision(SyntheticPolicyStatus.TARGET_MISMATCH)
    if acceptance.view_keys != request.view_keys:
        return _decision(SyntheticPolicyStatus.VIEW_SET_MISMATCH)
    if acceptance.issued_at > request.evaluated_at:
        return _decision(SyntheticPolicyStatus.INVALID_HUMAN_ACCEPTANCE)
    if acceptance.expires_at <= request.evaluated_at:
        return _decision(SyntheticPolicyStatus.ACCEPTANCE_EXPIRED)

    return _decision(SyntheticPolicyStatus.SYNTHETIC_CONDITIONS_SATISFIED)


__all__ = [
    "SyntheticAcceptanceStatement",
    "SyntheticCheckStatus",
    "SyntheticDataOnlyDecision",
    "SyntheticDataOnlyRequest",
    "SyntheticDataOnlyScope",
    "SyntheticHumanAcceptance",
    "SyntheticImportMode",
    "SyntheticPolicyStatus",
    "SyntheticTargetIdentity",
    "SyntheticWritePathCategory",
    "SyntheticWritePathCheck",
    "SyntheticWritePathEvidence",
    "evaluate_data_only_view_conditions",
]
