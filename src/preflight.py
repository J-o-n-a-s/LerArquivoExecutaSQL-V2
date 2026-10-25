"""Núcleo sintético fail-closed; não contém driver, SQL ou integração operacional."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DatabaseInstructionFamily(str, Enum):
    CREATE = "CREATE"
    ALTER = "ALTER"


@dataclass(frozen=True)
class DatabaseSignature:
    signature_id: str
    family: DatabaseInstructionFamily
    option: str | None
    requested_value: str | None
    evidence_key: str | None
    evidence_value: str | None
    requires_session_match: bool = False
    forced_on_since_major: int | None = None


def _alter(signature_id, option, value=None, *, evidence_key=None,
           evidence_value=None, session=False, forced=None):
    return DatabaseSignature(
        signature_id, DatabaseInstructionFamily.ALTER, option, value,
        evidence_key or option, value if evidence_value is None else evidence_value,
        session, forced,
    )


DATABASE_SIGNATURES = (
    DatabaseSignature("db.create.observed_v1", DatabaseInstructionFamily.CREATE,
                      None, None, None, None),
    _alter("db.set.compatibility_level.150", "COMPATIBILITY_LEVEL", "150"),
    _alter("db.set.ansi_null_default.off", "ANSI_NULL_DEFAULT", "OFF"),
    _alter("db.set.ansi_nulls.off", "ANSI_NULLS", "OFF", session=True, forced=14),
    _alter("db.set.ansi_padding.off", "ANSI_PADDING", "OFF", session=True, forced=14),
    _alter("db.set.ansi_warnings.off", "ANSI_WARNINGS", "OFF", session=True),
    _alter("db.set.arithabort.off", "ARITHABORT", "OFF", session=True),
    _alter("db.set.concat_null_yields_null.off", "CONCAT_NULL_YIELDS_NULL", "OFF",
           session=True, forced=14),
    _alter("db.set.numeric_roundabort.off", "NUMERIC_ROUNDABORT", "OFF", session=True),
    _alter("db.set.quoted_identifier.off", "QUOTED_IDENTIFIER", "OFF", session=True),
    _alter("db.set.cursor_close_on_commit.off", "CURSOR_CLOSE_ON_COMMIT", "OFF"),
    _alter("db.set.cursor_default.global", "CURSOR_DEFAULT", "GLOBAL"),
    _alter("db.set.recursive_triggers.off", "RECURSIVE_TRIGGERS", "OFF"),
    _alter("db.set.auto_close.off", "AUTO_CLOSE", "OFF"),
    _alter("db.set.auto_shrink.off", "AUTO_SHRINK", "OFF"),
    _alter("db.set.auto_update_statistics.on", "AUTO_UPDATE_STATISTICS", "ON"),
    _alter("db.set.auto_update_statistics_async.off", "AUTO_UPDATE_STATISTICS_ASYNC", "OFF"),
    _alter("db.set.date_correlation_optimization.off", "DATE_CORRELATION_OPTIMIZATION", "OFF"),
    _alter("db.set.parameterization.simple", "PARAMETERIZATION", "SIMPLE"),
    _alter("db.set.query_store.off", "QUERY_STORE", "OFF"),
    _alter("db.set.allow_snapshot_isolation.off", "ALLOW_SNAPSHOT_ISOLATION", "OFF"),
    _alter("db.set.read_committed_snapshot.off", "READ_COMMITTED_SNAPSHOT", "OFF"),
    _alter("db.set.disable_broker", "DISABLE_BROKER", None,
           evidence_key="SERVICE_BROKER", evidence_value="DISABLED"),
    _alter("db.set.honor_broker_priority.off", "HONOR_BROKER_PRIORITY", "OFF"),
    _alter("db.set.trustworthy.off", "TRUSTWORTHY", "OFF"),
    _alter("db.set.db_chaining.off", "DB_CHAINING", "OFF"),
    _alter("db.set.recovery.full", "RECOVERY", "FULL"),
    _alter("db.set.page_verify.checksum", "PAGE_VERIFY", "CHECKSUM"),
    _alter("db.set.target_recovery_time.60_seconds", "TARGET_RECOVERY_TIME", "60 SECONDS"),
    _alter("db.set.delayed_durability.disabled", "DELAYED_DURABILITY", "DISABLED"),
    _alter("db.set.accelerated_database_recovery.off", "ACCELERATED_DATABASE_RECOVERY", "OFF"),
    _alter("db.set.multi_user", "MULTI_USER", None,
           evidence_key="USER_ACCESS", evidence_value="MULTI_USER"),
    _alter("db.set.filestream_non_transacted_access.off",
           "FILESTREAM_NON_TRANSACTED_ACCESS", "OFF"),
    _alter("db.set.read_write", "READ_WRITE", None,
           evidence_key="UPDATEABILITY", evidence_value="READ_WRITE"),
)

# Faixa apenas do núcleo sintético. Versões fora desta lista precisam de uma
# revisão explícita do contrato antes de qualquer integração futura.
SUPPORTED_SYNTHETIC_MAJOR_VERSIONS = frozenset({13, 14, 15, 16})

_SIGNATURES_BY_ID = {item.signature_id: item for item in DATABASE_SIGNATURES}
_SIGNATURES_BY_OPTION = {item.option: item for item in DATABASE_SIGNATURES if item.option}
if len(DATABASE_SIGNATURES) != 34 or len(_SIGNATURES_BY_ID) != 34:
    raise RuntimeError("O contrato administrativo exige 34 identificadores únicos.")


def signature_by_id(signature_id):
    return _SIGNATURES_BY_ID[signature_id]


def signature_by_option(option):
    return _SIGNATURES_BY_OPTION[option]


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"),
                         sort_keys=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class DatabaseFileProfile:
    role: str
    name_literal_kind: str
    filename_literal_kind: str
    size: int
    size_unit: str
    max_size: int | None
    max_size_unit: str
    growth: int
    growth_unit: str

    def as_digest_value(self):
        return (self.role, self.name_literal_kind, self.filename_literal_kind,
                self.size, self.size_unit, self.max_size, self.max_size_unit,
                self.growth, self.growth_unit)


@dataclass(frozen=True, repr=False)
class CreateDatabaseProfile:
    containment: str
    primary: DatabaseFileProfile
    log: DatabaseFileProfile
    catalog_collation: str

    def digest(self):
        return _digest((self.containment, self.primary.as_digest_value(),
                        self.log.as_digest_value(), self.catalog_collation))

    def __repr__(self):
        return ("CreateDatabaseProfile(containment=%r, primary=%r, log=%r, "
                "catalog_collation=%r)" %
                (self.containment, self.primary, self.log, self.catalog_collation))


@dataclass(frozen=True, repr=False)
class DatabaseInstructionFacts:
    signature_id: str
    family: DatabaseInstructionFamily
    target: str
    line: int
    option: str | None
    requested_value: str | None
    create_profile: CreateDatabaseProfile | None = None

    def __repr__(self):
        return (f"DatabaseInstructionFacts(signature_id={self.signature_id!r}, "
                f"family={self.family.value!r}, line={self.line}, "
                f"option={self.option!r}, requested_value={self.requested_value!r}, "
                f"create_profile={self.create_profile!r}, target=<redacted>)")


@dataclass(frozen=True, repr=False)
class TargetIdentity:
    server_instance: str
    database_name: str
    database_id: int
    connection_fingerprint: str

    def fingerprint(self):
        return _digest((self.server_instance, self.database_name,
                        self.database_id, self.connection_fingerprint))

    def __repr__(self):
        return f"TargetIdentity(database_id={self.database_id}, values=<redacted>)"


@dataclass(frozen=True)
class OperationalIndicators:
    measured_at: datetime
    data_free_bytes: int
    log_free_bytes: int
    log_used_percent: float


@dataclass(frozen=True, repr=False)
class VerifiedSnapshot:
    identity: TargetIdentity
    execution_id: str
    captured_at: datetime
    expires_at: datetime
    sql_major_version: int | None
    version_supported: bool | None
    complete: bool
    stable_facts: tuple[tuple[str, str], ...]
    session_facts: tuple[tuple[str, str], ...]
    operational: OperationalIndicators | None = None

    def stable_digest(self):
        # Indicadores operacionais são deliberadamente excluídos: variam durante
        # milhões de INSERTs e devem ter limites/checkpoints próprios no futuro.
        return _digest((self.identity.fingerprint(), self.execution_id,
                        self.captured_at.isoformat(), self.expires_at.isoformat(),
                        self.sql_major_version, self.version_supported,
                        self.complete, self.stable_facts, self.session_facts))

    def __repr__(self):
        return (f"VerifiedSnapshot(database_id={self.identity.database_id}, "
                f"sql_major_version={self.sql_major_version!r}, "
                f"version_supported={self.version_supported!r}, "
                f"complete={self.complete!r}, values=<redacted>)")


@dataclass(frozen=True, repr=False)
class SignatureGrant:
    signature_id: str
    execution_id: str
    target_fingerprint: str
    evidence_digest: str
    requested_value: str | None
    expires_at: datetime
    create_profile_digest: str | None = None
    create_approved: bool = False

    def __post_init__(self):
        if self.signature_id not in _SIGNATURES_BY_ID:
            raise ValueError("Grant exige exatamente uma assinatura conhecida.")

    def __repr__(self):
        return (f"SignatureGrant(signature_id={self.signature_id!r}, "
                f"requested_value={self.requested_value!r}, "
                f"create_approved={self.create_approved!r}, bindings=<redacted>)")


class AuthorizationStatus(str, Enum):
    AUTHORIZED_SYNTHETIC = "AUTHORIZED_SYNTHETIC"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    EVIDENCE_EXPIRED = "EVIDENCE_EXPIRED"
    GRANT_EXPIRED = "GRANT_EXPIRED"
    SIGNATURE_NOT_AUTHORIZED = "SIGNATURE_NOT_AUTHORIZED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    VERSION_DEPENDENT_BEHAVIOR = "VERSION_DEPENDENT_BEHAVIOR"
    INVALID_DECISION_TYPE = "INVALID_DECISION_TYPE"


_REASONS = {
    AuthorizationStatus.AUTHORIZED_SYNTHETIC: "Autorização sintética vinculada à evidência.",
    AuthorizationStatus.MISSING_EVIDENCE: "Evidência obrigatória ausente.",
    AuthorizationStatus.INCOMPLETE_EVIDENCE: "Evidência incompleta ou desconhecida.",
    AuthorizationStatus.TARGET_MISMATCH: "Alvo diferente da evidência verificada.",
    AuthorizationStatus.IDENTITY_MISMATCH: "Identidade, execução ou evidência divergente.",
    AuthorizationStatus.VALUE_MISMATCH: "Opção, valor ou perfil divergente.",
    AuthorizationStatus.EVIDENCE_EXPIRED: "Evidência expirada.",
    AuthorizationStatus.GRANT_EXPIRED: "Grant sintético expirado.",
    AuthorizationStatus.SIGNATURE_NOT_AUTHORIZED: "Assinatura não autorizada.",
    AuthorizationStatus.APPROVAL_REQUIRED: "Aprovação específica obrigatória ausente.",
    AuthorizationStatus.UNSUPPORTED_VERSION: "Versão ausente, desconhecida ou não suportada.",
    AuthorizationStatus.VERSION_DEPENDENT_BEHAVIOR: "Comportamento efetivo dependente da versão não é equivalente.",
    AuthorizationStatus.INVALID_DECISION_TYPE: "Tipo de decisão inválido; autorização negada.",
}


@dataclass(frozen=True, repr=False)
class AuthorizationDecision:
    status: AuthorizationStatus
    signature_id: str | None

    @property
    def reason(self):
        return _REASONS[self.status]

    def __bool__(self):
        raise TypeError("Decisão de autorização não pode ser usada como booleano.")

    def __repr__(self):
        return (f"AuthorizationDecision(status={self.status.value!r}, "
                f"signature_id={self.signature_id!r})")


def _decision(status, signature_id=None):
    return AuthorizationDecision(status, signature_id)


def validate_decision_type(decision):
    if (not isinstance(decision, AuthorizationDecision)
            or not isinstance(decision.status, AuthorizationStatus)):
        return _decision(AuthorizationStatus.INVALID_DECISION_TYPE)
    return decision


class ProbeSection(str, Enum):
    IDENTITY = "IDENTITY"
    CONFIGURATION = "CONFIGURATION"
    SESSION = "SESSION"
    OPERATIONAL = "OPERATIONAL"


class ProbeStatus(str, Enum):
    SUCCESS = "SUCCESS"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    INCOMPLETE_RESPONSE = "INCOMPLETE_RESPONSE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    DUPLICATE_RESULT = "DUPLICATE_RESULT"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    READ_ERROR = "READ_ERROR"
    CLOSE_ERROR = "CLOSE_ERROR"


class ProbeConnectionError(Exception):
    """Falha de conexão classificada, com SQLSTATE opcional sanitizado."""

    sqlstate: str | None


class ProbeTimeoutError(Exception):
    """Timeout classificado, com SQLSTATE opcional sanitizado."""

    sqlstate: str | None


class ProbePermissionError(Exception):
    """Falha de autenticação ou permissão com SQLSTATE opcional sanitizado."""

    sqlstate: str | None


class ProbeReadError(Exception):
    """Falha de leitura conservadora, com SQLSTATE opcional sanitizado."""

    sqlstate: str | None


@dataclass(frozen=True, repr=False)
class ProbeRequest:
    target_database: str
    execution_id: str
    captured_at: datetime
    expires_at: datetime
    required_stable_keys: tuple[str, ...]
    required_session_keys: tuple[str, ...]

    def __repr__(self):
        return (f"ProbeRequest(stable_keys={len(self.required_stable_keys)}, "
                f"session_keys={len(self.required_session_keys)}, values=<redacted>)")


_PROBE_REASONS = {
    ProbeStatus.SUCCESS: "Coleta sintética concluída.",
    ProbeStatus.TARGET_MISMATCH: "Banco atual diferente do destino solicitado.",
    ProbeStatus.INCOMPLETE_RESPONSE: "Resposta sintética incompleta.",
    ProbeStatus.INVALID_RESPONSE: "Resposta sintética com tipo ou estrutura inválida.",
    ProbeStatus.DUPLICATE_RESULT: "Resposta duplicada onde uma linha era obrigatória.",
    ProbeStatus.UNSUPPORTED_VERSION: "Versão sintética ausente ou não suportada.",
    ProbeStatus.CONNECTION_ERROR: "Falha sintética de conexão.",
    ProbeStatus.TIMEOUT: "Timeout sintético durante a coleta.",
    ProbeStatus.PERMISSION_DENIED: "Permissão sintética insuficiente.",
    ProbeStatus.READ_ERROR: "Falha sintética durante a leitura.",
    ProbeStatus.CLOSE_ERROR: "Falha sintética no fechamento do adaptador.",
}


@dataclass(frozen=True, repr=False)
class ProbeResult:
    status: ProbeStatus
    snapshot: VerifiedSnapshot | None
    close_failed: bool = False

    @property
    def reason(self):
        return _PROBE_REASONS[self.status]

    def __repr__(self):
        return (f"ProbeResult(status={self.status.value!r}, "
                f"has_snapshot={self.snapshot is not None}, "
                f"close_failed={self.close_failed})")


_MISSING = object()


class PreflightProbe:
    """Coleta somente respostas semânticas de um adaptador injetado."""

    @staticmethod
    def _request_valid(request):
        if (not isinstance(request, ProbeRequest)
                or not _valid_text(request.target_database)
                or not _valid_text(request.execution_id)
                or not _aware(request.captured_at)
                or not _aware(request.expires_at)
                or request.captured_at >= request.expires_at):
            return False
        for keys in (request.required_stable_keys, request.required_session_keys):
            if (type(keys) is not tuple
                    or any(not _valid_text(key) for key in keys)
                    or len(set(keys)) != len(keys)):
                return False
        return True

    @staticmethod
    def _one(rows, *, optional=False):
        if type(rows) is not tuple:
            return _MISSING, ProbeStatus.INVALID_RESPONSE
        if len(rows) > 1:
            return _MISSING, ProbeStatus.DUPLICATE_RESULT
        if not rows:
            return (None, None) if optional else (_MISSING, ProbeStatus.INCOMPLETE_RESPONSE)
        if type(rows[0]) is not dict:
            return _MISSING, ProbeStatus.INVALID_RESPONSE
        return rows[0], None

    @staticmethod
    def _identity(row):
        required = {"server_instance", "database_name", "database_id",
                    "connection_fingerprint", "sql_major_version",
                    "version_supported"}
        if not required <= set(row):
            return _MISSING, ProbeStatus.INCOMPLETE_RESPONSE
        identity = TargetIdentity(row["server_instance"], row["database_name"],
                                  row["database_id"], row["connection_fingerprint"])
        if not _valid_identity(identity):
            return _MISSING, ProbeStatus.INVALID_RESPONSE
        major = row["sql_major_version"]
        supported = row["version_supported"]
        if (type(major) is not int or type(supported) is not bool
                or not supported or major not in SUPPORTED_SYNTHETIC_MAJOR_VERSIONS):
            return _MISSING, ProbeStatus.UNSUPPORTED_VERSION
        return (identity, major, supported), None

    @staticmethod
    def _operational(row):
        if row is None:
            return None, None
        required = {"measured_at", "data_free_bytes", "log_free_bytes",
                    "log_used_percent"}
        if not required <= set(row):
            return _MISSING, ProbeStatus.INCOMPLETE_RESPONSE
        indicators = OperationalIndicators(
            row["measured_at"], row["data_free_bytes"], row["log_free_bytes"],
            row["log_used_percent"])
        if not _valid_operational(indicators):
            return _MISSING, ProbeStatus.INVALID_RESPONSE
        return indicators, None

    def _collect(self, adapter, request):
        if not self._request_valid(request):
            return ProbeResult(ProbeStatus.INVALID_RESPONSE, None)

        identity_row, error = self._one(adapter.read(ProbeSection.IDENTITY))
        if error:
            return ProbeResult(error, None)
        identity_values, error = self._identity(identity_row)
        if error:
            return ProbeResult(error, None)
        identity, major, supported = identity_values
        if identity.database_name != request.target_database:
            return ProbeResult(ProbeStatus.TARGET_MISMATCH, None)

        configuration, error = self._one(adapter.read(ProbeSection.CONFIGURATION))
        if error:
            return ProbeResult(error, None)
        if "stable_facts" not in configuration:
            return ProbeResult(ProbeStatus.INCOMPLETE_RESPONSE, None)
        stable = _fact_map(configuration["stable_facts"])
        if stable is None:
            return ProbeResult(ProbeStatus.INVALID_RESPONSE, None)

        session_row, error = self._one(adapter.read(ProbeSection.SESSION))
        if error:
            return ProbeResult(error, None)
        if "session_facts" not in session_row:
            return ProbeResult(ProbeStatus.INCOMPLETE_RESPONSE, None)
        session = _fact_map(session_row["session_facts"])
        if session is None:
            return ProbeResult(ProbeStatus.INVALID_RESPONSE, None)

        operational_row, error = self._one(
            adapter.read(ProbeSection.OPERATIONAL), optional=True)
        if error:
            return ProbeResult(error, None)
        operational, error = self._operational(operational_row)
        if error:
            return ProbeResult(error, None)

        complete = (set(request.required_stable_keys) <= set(stable)
                    and set(request.required_session_keys) <= set(session))
        snapshot = VerifiedSnapshot(
            identity, request.execution_id, request.captured_at,
            request.expires_at, major, supported, complete,
            tuple(sorted(stable.items())), tuple(sorted(session.items())),
            operational,
        )
        return ProbeResult(ProbeStatus.SUCCESS if complete else
                           ProbeStatus.INCOMPLETE_RESPONSE, snapshot)

    def collect(self, adapter, request):
        """Coleta um snapshot e sempre tenta fechar o adaptador.

        Erros esperados são convertidos em ``ProbeResult`` sanitizado. Uma
        interrupção é propagada somente após a tentativa independente de
        fechamento; falha de fechamento nunca oculta um resultado anterior.
        """
        result = None
        interrupted = None
        try:
            result = self._collect(adapter, request)
        except (KeyboardInterrupt, SystemExit) as error:
            interrupted = error
        except ProbeConnectionError:
            result = ProbeResult(ProbeStatus.CONNECTION_ERROR, None)
        except ProbeTimeoutError:
            result = ProbeResult(ProbeStatus.TIMEOUT, None)
        except ProbePermissionError:
            result = ProbeResult(ProbeStatus.PERMISSION_DENIED, None)
        except (ProbeReadError, Exception):
            result = ProbeResult(ProbeStatus.READ_ERROR, None)

        close_failed = False
        try:
            adapter.close()
        except (KeyboardInterrupt, SystemExit) as error:
            if interrupted is None:
                interrupted = error
        except Exception:
            close_failed = True

        if interrupted is not None:
            raise interrupted
        if close_failed:
            if result is None or result.status is ProbeStatus.SUCCESS:
                return ProbeResult(ProbeStatus.CLOSE_ERROR, None, True)
            return ProbeResult(result.status, result.snapshot, True)
        return result


_COMMON_FACTS = {
    "DATABASE_STATE": "ONLINE",
    "UPDATEABILITY": "READ_WRITE",
    "USER_ACCESS": "MULTI_USER",
    "EVIDENCE_PERMISSIONS": "COMPLETE",
}
_CREATE_FACTS = {
    "DATABASE_EXISTS": "YES",
    "CONTAINMENT": "NONE",
    "CATALOG_COLLATION": "DATABASE_DEFAULT",
    "STORAGE_READY": "YES",
    "SCHEMA_READY": "YES",
}


def _aware(moment):
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        return False
    try:
        return moment.utcoffset() is not None
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_text(value):
    return (type(value) is str and bool(value.strip()) and value == value.strip()
            and not any(ord(char) < 32 for char in value))


def _valid_identity(identity):
    return (isinstance(identity, TargetIdentity)
            and _valid_text(identity.server_instance)
            and _valid_text(identity.database_name)
            and type(identity.database_id) is int and identity.database_id > 0
            and _valid_text(identity.connection_fingerprint))


def _valid_file_profile(profile, role):
    return (type(profile) is DatabaseFileProfile
            and profile.role == role
            and _valid_text(profile.name_literal_kind)
            and _valid_text(profile.filename_literal_kind)
            and type(profile.size) is int and profile.size > 0
            and _valid_text(profile.size_unit)
            and (profile.max_size is None
                 or (type(profile.max_size) is int and profile.max_size > 0))
            and _valid_text(profile.max_size_unit)
            and type(profile.growth) is int and profile.growth > 0
            and _valid_text(profile.growth_unit))


def _valid_create_profile(profile):
    return (type(profile) is CreateDatabaseProfile
            and _valid_text(profile.containment)
            and _valid_file_profile(profile.primary, "PRIMARY")
            and _valid_file_profile(profile.log, "LOG")
            and _valid_text(profile.catalog_collation))


def _fact_map(pairs):
    if type(pairs) is not tuple:
        return None
    result = {}
    for pair in pairs:
        if (type(pair) is not tuple or len(pair) != 2
                or not _valid_text(pair[0]) or not _valid_text(pair[1])
                or pair[0] in result):
            return None
        result[pair[0]] = pair[1]
    return result


def _valid_operational(indicators):
    if indicators is None:
        return True
    return (isinstance(indicators, OperationalIndicators)
            and _aware(indicators.measured_at)
            and type(indicators.data_free_bytes) is int
            and indicators.data_free_bytes >= 0
            and type(indicators.log_free_bytes) is int
            and indicators.log_free_bytes >= 0
            and type(indicators.log_used_percent) in (int, float)
            and 0 <= indicators.log_used_percent <= 100)


def evaluate_database_authorization(candidate, snapshot, grant, *, now):
    """Decide somente sobre fatos sintéticos; nunca consulta ou libera a aplicação."""

    if not isinstance(candidate, DatabaseInstructionFacts):
        return _decision(AuthorizationStatus.INVALID_DECISION_TYPE)
    if (not _valid_text(candidate.target) or type(candidate.line) is not int
            or candidate.line <= 0):
        return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE,
                         candidate.signature_id)
    if not _valid_text(candidate.signature_id):
        return _decision(AuthorizationStatus.INVALID_DECISION_TYPE)
    definition = _SIGNATURES_BY_ID.get(candidate.signature_id)
    if definition is None:
        return _decision(AuthorizationStatus.SIGNATURE_NOT_AUTHORIZED,
                         candidate.signature_id)
    if (candidate.family != definition.family or candidate.option != definition.option
            or candidate.requested_value != definition.requested_value
            or (definition.family is DatabaseInstructionFamily.CREATE) !=
               (candidate.create_profile is not None)):
        return _decision(AuthorizationStatus.VALUE_MISMATCH, candidate.signature_id)
    if (definition.family is DatabaseInstructionFamily.CREATE
            and not _valid_create_profile(candidate.create_profile)):
        return _decision(AuthorizationStatus.INVALID_DECISION_TYPE,
                         candidate.signature_id)
    if not isinstance(snapshot, VerifiedSnapshot):
        return _decision(AuthorizationStatus.MISSING_EVIDENCE, candidate.signature_id)
    if snapshot.complete is not True:
        return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE, candidate.signature_id)
    if (not _valid_identity(snapshot.identity)
            or not _valid_text(snapshot.execution_id)):
        return _decision(AuthorizationStatus.IDENTITY_MISMATCH,
                         candidate.signature_id)
    if (not _aware(now) or not _aware(snapshot.captured_at)
            or not _aware(snapshot.expires_at)):
        return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE, candidate.signature_id)
    if snapshot.captured_at >= snapshot.expires_at or snapshot.captured_at > now:
        return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE,
                         candidate.signature_id)
    if now >= snapshot.expires_at:
        return _decision(AuthorizationStatus.EVIDENCE_EXPIRED, candidate.signature_id)
    if (snapshot.sql_major_version is None or snapshot.version_supported is not True
            or not isinstance(snapshot.sql_major_version, int)
            or snapshot.sql_major_version not in SUPPORTED_SYNTHETIC_MAJOR_VERSIONS):
        return _decision(AuthorizationStatus.UNSUPPORTED_VERSION, candidate.signature_id)
    if candidate.target != snapshot.identity.database_name:
        return _decision(AuthorizationStatus.TARGET_MISMATCH, candidate.signature_id)

    facts = _fact_map(snapshot.stable_facts)
    session = _fact_map(snapshot.session_facts)
    if facts is None or session is None or not _valid_operational(snapshot.operational):
        return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE, candidate.signature_id)
    for key, expected in _COMMON_FACTS.items():
        if key not in facts:
            return _decision(AuthorizationStatus.MISSING_EVIDENCE, candidate.signature_id)
        if facts[key] != expected:
            return _decision(AuthorizationStatus.VALUE_MISMATCH, candidate.signature_id)

    if not isinstance(grant, SignatureGrant):
        status = (AuthorizationStatus.APPROVAL_REQUIRED
                  if definition.family is DatabaseInstructionFamily.CREATE
                  else AuthorizationStatus.SIGNATURE_NOT_AUTHORIZED)
        return _decision(status, candidate.signature_id)
    if grant.signature_id != candidate.signature_id:
        return _decision(AuthorizationStatus.SIGNATURE_NOT_AUTHORIZED,
                         candidate.signature_id)
    if (not _aware(grant.expires_at)
            or not _valid_text(grant.execution_id)
            or not _valid_text(grant.target_fingerprint)
            or not _valid_text(grant.evidence_digest)):
        return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE, candidate.signature_id)
    if grant.expires_at <= snapshot.captured_at:
        return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE,
                         candidate.signature_id)
    if now >= grant.expires_at:
        return _decision(AuthorizationStatus.GRANT_EXPIRED, candidate.signature_id)
    if (grant.execution_id != snapshot.execution_id
            or grant.target_fingerprint != snapshot.identity.fingerprint()
            or grant.evidence_digest != snapshot.stable_digest()):
        return _decision(AuthorizationStatus.IDENTITY_MISMATCH, candidate.signature_id)
    if grant.requested_value != candidate.requested_value:
        return _decision(AuthorizationStatus.VALUE_MISMATCH, candidate.signature_id)

    if definition.family is DatabaseInstructionFamily.CREATE:
        for key, expected in _CREATE_FACTS.items():
            if key not in facts:
                return _decision(AuthorizationStatus.MISSING_EVIDENCE,
                                 candidate.signature_id)
            if facts[key] != expected:
                return _decision(AuthorizationStatus.VALUE_MISMATCH,
                                 candidate.signature_id)
        if grant.create_approved is not True:
            return _decision(AuthorizationStatus.APPROVAL_REQUIRED,
                             candidate.signature_id)
        if grant.create_profile_digest != candidate.create_profile.digest():
            return _decision(AuthorizationStatus.VALUE_MISMATCH,
                             candidate.signature_id)
    else:
        if type(grant.create_approved) is not bool:
            return _decision(AuthorizationStatus.INCOMPLETE_EVIDENCE,
                             candidate.signature_id)
        if definition.evidence_key not in facts:
            return _decision(AuthorizationStatus.MISSING_EVIDENCE,
                             candidate.signature_id)
        if facts[definition.evidence_key] != definition.evidence_value:
            return _decision(AuthorizationStatus.VALUE_MISMATCH,
                             candidate.signature_id)
        if (definition.forced_on_since_major is not None
                and snapshot.sql_major_version >= definition.forced_on_since_major
                and definition.requested_value == "OFF"):
            return _decision(AuthorizationStatus.VERSION_DEPENDENT_BEHAVIOR,
                             candidate.signature_id)
        if definition.requires_session_match:
            if definition.option not in session:
                return _decision(AuthorizationStatus.MISSING_EVIDENCE,
                                 candidate.signature_id)
            if session[definition.option] != definition.requested_value:
                return _decision(AuthorizationStatus.VALUE_MISMATCH,
                                 candidate.signature_id)

    return _decision(AuthorizationStatus.AUTHORIZED_SYNTHETIC,
                     candidate.signature_id)
