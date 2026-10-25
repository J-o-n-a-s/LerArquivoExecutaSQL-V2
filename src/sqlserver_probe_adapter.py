"""Adaptador SQL Server read-only, sem driver e sem criação de conexão."""

from collections.abc import Mapping, Sequence
from typing import Protocol, TypeGuard, cast

from preflight import (
    ProbeConnectionError,
    ProbePermissionError,
    ProbeReadError,
    ProbeSection,
    ProbeTimeoutError,
    SUPPORTED_SYNTHETIC_MAJOR_VERSIONS,
)
from sqlserver_driver_errors import translate_driver_error


IDENTITY_QUERY = """SELECT
    CONVERT(nvarchar(128), SERVERPROPERTY('ServerName')) AS server_instance,
    CONVERT(nvarchar(128), DB_NAME()) AS database_name,
    CONVERT(int, DB_ID()) AS database_id,
    CONVERT(nvarchar(128), SERVERPROPERTY('ProductMajorVersion')) AS sql_major_version"""

CONFIGURATION_QUERY = """SELECT
    state_desc AS database_state,
    is_read_only,
    user_access_desc AS user_access,
    compatibility_level
FROM sys.databases
WHERE database_id = DB_ID()"""

SESSION_QUERY = """SELECT
    CONVERT(int, SESSIONPROPERTY('ANSI_NULLS')) AS ansi_nulls,
    CONVERT(int, SESSIONPROPERTY('ANSI_PADDING')) AS ansi_padding,
    CONVERT(int, SESSIONPROPERTY('ANSI_WARNINGS')) AS ansi_warnings,
    CONVERT(int, SESSIONPROPERTY('ARITHABORT')) AS arithabort,
    CONVERT(int, SESSIONPROPERTY('CONCAT_NULL_YIELDS_NULL')) AS concat_null_yields_null,
    CONVERT(int, SESSIONPROPERTY('NUMERIC_ROUNDABORT')) AS numeric_roundabort,
    CONVERT(int, SESSIONPROPERTY('QUOTED_IDENTIFIER')) AS quoted_identifier"""


_QUERIES = {
    ProbeSection.IDENTITY: IDENTITY_QUERY,
    ProbeSection.CONFIGURATION: CONFIGURATION_QUERY,
    ProbeSection.SESSION: SESSION_QUERY,
}

_COLUMNS = {
    ProbeSection.IDENTITY: (
        "server_instance", "database_name", "database_id", "sql_major_version",
    ),
    ProbeSection.CONFIGURATION: (
        "database_state", "is_read_only", "user_access", "compatibility_level",
    ),
    ProbeSection.SESSION: (
        "ansi_nulls", "ansi_padding", "ansi_warnings", "arithabort",
        "concat_null_yields_null", "numeric_roundabort", "quoted_identifier",
    ),
}

_SESSION_KEYS = (
    "ANSI_NULLS", "ANSI_PADDING", "ANSI_WARNINGS", "ARITHABORT",
    "CONCAT_NULL_YIELDS_NULL", "NUMERIC_ROUNDABORT", "QUOTED_IDENTIFIER",
)

_PASSTHROUGH_ERRORS = (
    ProbeConnectionError,
    ProbeTimeoutError,
    ProbePermissionError,
    ProbeReadError,
)


class _Cursor(Protocol):
    """Interface mínima do cursor injetado pelo chamador."""

    description: object

    def execute(self, statement: str) -> object: ...

    def fetchall(self) -> object: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    """Interface mínima da conexão emprestada ao adaptador."""

    def cursor(self) -> _Cursor: ...


def _valid_text(value: object) -> TypeGuard[str]:
    return (type(value) is str and bool(value.strip()) and value == value.strip()
            and not any(ord(char) < 32 for char in value))


def _read_error() -> ProbeReadError:
    """Cria a falha pública fixa usada para respostas inválidas."""
    return ProbeReadError("Resposta read-only inválida ou indisponível.")


class SQLServerReadOnlyProbeAdapter:
    """Expõe leituras permitidas e possui somente o cursor que cria.

    A conexão recebida permanece emprestada e nunca é fechada pelo adaptador.
    Resultados são validados e convertidos em estruturas sem conteúdo SQL.
    """

    def __init__(
            self,
            connection: _Connection,
            connection_fingerprint: str,
    ) -> None:
        """Cria um cursor próprio após validar conexão e fingerprint."""
        if (not _valid_text(connection_fingerprint)
                or not callable(getattr(connection, "cursor", None))):
            raise ValueError("Conexão ou fingerprint inválido para o adaptador.")
        self._connection_fingerprint = connection_fingerprint
        self._cursor = connection.cursor()
        self._closed = False

    def __repr__(self) -> str:
        return ("SQLServerReadOnlyProbeAdapter(owns_cursor=True, "
                f"closed={self._closed}, values=<redacted>)")

    def _rows(self, section: ProbeSection) -> tuple[dict[str, object], ...]:
        """Executa uma consulta fixa e valida colunas e cardinalidade das linhas."""
        try:
            self._cursor.execute(_QUERIES[section])
            description = self._cursor.description
            rows = self._cursor.fetchall()
        except (KeyboardInterrupt, SystemExit):
            raise
        except _PASSTHROUGH_ERRORS:
            raise
        except Exception as error:
            raise translate_driver_error(error) from None

        try:
            if type(description) not in (list, tuple):
                raise _read_error()
            description_items = cast(Sequence[object], description)
            columns = tuple(
                cast(str, cast(Sequence[object], item)[0]).lower()
                for item in description_items
                if (type(item) in (list, tuple) and item
                    and type(cast(Sequence[object], item)[0]) is str)
            )
            if columns != _COLUMNS[section] or len(columns) != len(description_items):
                raise _read_error()
            if type(rows) not in (list, tuple):
                raise _read_error()
            row_items = cast(Sequence[object], rows)
            mapped: list[dict[str, object]] = []
            for row in row_items:
                if isinstance(row, (str, bytes, bytearray, memoryview, Mapping)):
                    raise _read_error()
                row_values = cast(Sequence[object], row)
                if len(row_values) != len(columns):
                    raise _read_error()
                values = tuple(row_values[index] for index in range(len(columns)))
                mapped.append(dict(zip(columns, values)))
            return tuple(mapped)
        except ProbeReadError:
            raise
        except Exception:
            raise _read_error() from None

    def _identity(self) -> tuple[dict[str, object], ...]:
        converted: list[dict[str, object]] = []
        for row in self._rows(ProbeSection.IDENTITY):
            server = row["server_instance"]
            database = row["database_name"]
            database_id = row["database_id"]
            major_text = row["sql_major_version"]
            if (not _valid_text(server) or not _valid_text(database)
                    or type(database_id) is not int or database_id <= 0
                    or not _valid_text(major_text)
                    or not major_text.isascii() or not major_text.isdecimal()):
                raise _read_error()
            major = int(major_text)
            converted.append({
                "server_instance": server,
                "database_name": database,
                "database_id": database_id,
                "connection_fingerprint": self._connection_fingerprint,
                "sql_major_version": major,
                "version_supported": major in SUPPORTED_SYNTHETIC_MAJOR_VERSIONS,
            })
        return tuple(converted)

    def _configuration(self) -> tuple[dict[str, object], ...]:
        converted: list[dict[str, object]] = []
        for row in self._rows(ProbeSection.CONFIGURATION):
            state = row["database_state"]
            read_only = row["is_read_only"]
            user_access = row["user_access"]
            compatibility = row["compatibility_level"]
            if (not _valid_text(state) or type(read_only) is not bool
                    or not _valid_text(user_access)
                    or type(compatibility) is not int or compatibility <= 0):
                raise _read_error()
            facts = (
                ("COMPATIBILITY_LEVEL", str(compatibility)),
                ("DATABASE_STATE", state),
                ("UPDATEABILITY", "READ_ONLY" if read_only else "READ_WRITE"),
                ("USER_ACCESS", user_access),
            )
            converted.append({"stable_facts": facts})
        return tuple(converted)

    def _session(self) -> tuple[dict[str, object], ...]:
        converted: list[dict[str, object]] = []
        for row in self._rows(ProbeSection.SESSION):
            values = tuple(row[column] for column in _COLUMNS[ProbeSection.SESSION])
            if any(type(value) is not int or value not in (0, 1)
                   for value in values):
                raise _read_error()
            converted.append({
                "session_facts": tuple(
                    (key, "ON" if value == 1 else "OFF")
                    for key, value in zip(_SESSION_KEYS, values)
                ),
            })
        return tuple(converted)

    def read(self, section: ProbeSection) -> tuple[dict[str, object], ...]:
        """Lê uma seção permitida ou falha de modo fechado.

        O adaptador deve estar aberto. Seções de identidade, configuração e
        sessão executam consultas constantes; a seção operacional retorna uma
        tupla vazia e qualquer valor fora do enum é recusado.
        """
        if self._closed:
            raise _read_error()
        if section is ProbeSection.IDENTITY:
            return self._identity()
        if section is ProbeSection.CONFIGURATION:
            return self._configuration()
        if section is ProbeSection.SESSION:
            return self._session()
        if section is ProbeSection.OPERATIONAL:
            return ()
        raise _read_error()

    def close(self) -> None:
        """Fecha o cursor uma única vez sem fechar a conexão emprestada."""
        if self._closed:
            return
        self._closed = True
        self._cursor.close()
