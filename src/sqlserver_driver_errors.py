"""Tradução conservadora e sanitizada de SQLSTATE para erros do probe."""

from typing import TypeGuard

from preflight import (
    ProbeConnectionError,
    ProbePermissionError,
    ProbeReadError,
    ProbeTimeoutError,
)


_MISSING = object()

_CONNECTION_STATES = frozenset({
    "08001", "08002", "08003", "08004", "08007", "08S01",
})
_TIMEOUT_STATES = frozenset({"HYT00", "HYT01"})
_PERMISSION_STATES = frozenset({"28000"})

_PUBLIC_MESSAGES = {
    ProbeConnectionError: "Falha de conexão ODBC classificada.",
    ProbeTimeoutError: "Timeout ODBC classificado.",
    ProbePermissionError: "Autenticação ou permissão ODBC recusada.",
    ProbeReadError: "Falha de leitura ODBC não classificada.",
}


def _valid_sqlstate(value: object) -> TypeGuard[str]:
    """Aceita apenas o formato ASCII estrito de cinco caracteres SQLSTATE."""
    return (
        type(value) is str
        and len(value) == 5
        and value.isascii()
        and all(character.isdigit() or "A" <= character <= "Z"
                for character in value)
    )


def _args_sqlstate(error: BaseException) -> str | None:
    """Obtém um SQLSTATE somente do primeiro argumento seguro da exceção."""
    try:
        args = error.args
    except Exception:
        return None
    if type(args) is not tuple or not args:
        return None
    return args[0] if _valid_sqlstate(args[0]) else None


def extract_sqlstate(error: BaseException) -> str | None:
    """Extrai um SQLSTATE inequívoco sem copiar texto do driver.

    O atributo explícito e o primeiro argumento, quando ambos existem, devem
    coincidir. Valores ausentes, malformados ou conflitantes retornam ``None``.
    """
    try:
        explicit = getattr(error, "sqlstate", _MISSING)
    except Exception:
        return None

    from_args = _args_sqlstate(error)
    if explicit is _MISSING or explicit is None:
        return from_args
    if not _valid_sqlstate(explicit):
        return None
    if from_args is not None and from_args != explicit:
        return None
    return explicit


def translate_driver_error(
        error: BaseException,
) -> ProbeConnectionError | ProbeTimeoutError | ProbePermissionError | ProbeReadError:
    """Traduz uma falha do driver para uma exceção pública sanitizada.

    ``KeyboardInterrupt`` e ``SystemExit`` são sempre propagados. As demais
    falhas recebem mensagem fixa e conservam apenas o SQLSTATE validado.
    """
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        raise error

    sqlstate = extract_sqlstate(error)
    error_type: type[
        ProbeConnectionError
        | ProbeTimeoutError
        | ProbePermissionError
        | ProbeReadError
    ]
    if sqlstate in _CONNECTION_STATES:
        error_type = ProbeConnectionError
    elif sqlstate in _TIMEOUT_STATES:
        error_type = ProbeTimeoutError
    elif sqlstate in _PERMISSION_STATES:
        error_type = ProbePermissionError
    else:
        error_type = ProbeReadError

    translated = error_type(_PUBLIC_MESSAGES[error_type])
    translated.sqlstate = sqlstate
    return translated


__all__ = ["extract_sqlstate", "translate_driver_error"]
