"""Descoberta e inventário incrementais sem autorização ou efeitos externos.

Este módulo consome as primitivas lexicais do classificador, mas nunca cria um
contexto de execução. Um lote recusado pelo contrato de importação pode ser
descrito e a análise só continua depois de uma fronteira ``GO`` confiável.
"""

import os
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from enum import Enum
from typing import TextIO

from sql_classifier import (
    SQLBatchReader,
    SQLClassificationError,
    SQLToken,
    classify_offline_batch,
    tokenize_sql,
)


DEFAULT_DISCOVERY_MAX_BATCHES = 256
PROGRESS_BATCH_INTERVAL = 128
_DATABASE_REFUSAL = (
    "Instrução administrativa de banco reconhecida, mas não autorizada para descarte."
)
_VIEW_REFUSAL = "Definição de view reconhecida, mas exige equivalência externa comprovada."
_ADMIN_DATABASES = {"master", "model", "msdb", "tempdb"}
_OTHER_FAMILIES = {
    "BACKUP", "BEGIN", "BREAK", "BULK", "CLOSE", "COMMIT", "CONTINUE",
    "DBCC", "DEALLOCATE", "DECLARE", "DELETE", "DENY", "DROP", "EXEC",
    "EXECUTE", "FETCH", "GOTO", "GRANT", "IF", "KILL", "MERGE", "OPEN",
    "PRINT", "RAISERROR", "RECONFIGURE", "RESTORE", "RETURN", "REVOKE",
    "ROLLBACK", "SAVE", "SELECT", "SHUTDOWN", "THROW", "TRUNCATE",
    "UPDATE", "WAITFOR", "WHILE",
}


class Coverage(str, Enum):
    """Quanto do arquivo pôde ser examinado com sincronização confiável."""

    COMPLETA = "completa"
    PRELIMINAR = "preliminar"
    PARCIAL = "parcial"


class DiscoveryStatus(str, Enum):
    """Conclusão lógica da descoberta de banco."""

    DETERMINADO = "determinado"
    AMBIGUO = "ambiguo"
    NAO_DETERMINADO = "nao_determinado"
    INTERROMPIDO = "interrompido"


class InventoryStatus(str, Enum):
    """Conclusão física do inventário."""

    CONCLUIDO = "concluido"
    PARCIAL = "parcial"
    INTERROMPIDO = "interrompido"


@dataclass(frozen=True)
class DatabaseDiscoveryReport:
    """Resultado sanitizado; nomes de banco são a única evidência exibível."""

    status: DiscoveryStatus
    banco_candidato: str | None
    bancos_candidatos: tuple[str, ...]
    confianca: str
    cobertura: Coverage
    linhas_fisicas_lidas: int
    lotes_analisados: int
    processamento_completo: bool
    motivo_interrupcao: str | None


@dataclass(frozen=True)
class InventoryReport:
    """Contadores offline sem texto SQL, caminhos ou valores de dados."""

    status: InventoryStatus
    cobertura: Coverage
    linhas_fisicas: int
    lotes: int
    comandos: int
    comandos_reconhecidos: int
    inserts: int
    identity_insert: int
    session_set: int
    use: int
    ddl_reconhecido: int
    create_database: int
    alter_database: int
    views: int
    outras_familias: int
    lotes_nao_classificaveis: int
    bancos_candidatos: tuple[str, ...]
    processamento_completo: bool
    motivo_interrupcao: str | None


@dataclass(frozen=True)
class InventoryProgressSnapshot:
    """Estado numérico mínimo para observação do inventário."""

    linhas_fisicas: int
    lotes: int
    processamento_completo: bool
    posicao_bytes_estimada: int | None
    tamanho_bytes: int | None


@dataclass(frozen=True)
class _BatchFacts:
    commands: int = 0
    inserts: int = 0
    identity: int = 0
    session: int = 0
    use: int = 0
    ddl: int = 0
    create_database: int = 0
    alter_database: int = 0
    views: int = 0
    other: int = 0
    classifiable: bool = True
    has_content: bool = True
    evidence: tuple[tuple[str, str], ...] = ()


def _word(token: SQLToken, expected: str | None = None) -> bool:
    if token.kind != "WORD":
        return False
    return expected is None or token.value.upper() == expected


def _identifier(token: SQLToken) -> str | None:
    if token.kind == "IDENT":
        closing = "]" if token.value.startswith("[") else '"'
        value = token.value[1:-1].replace(closing * 2, closing)
    elif token.kind == "WORD":
        value = token.value
    else:
        return None
    if not value or not all(character.isprintable() for character in value):
        return None
    return value


def _only_terminal_semicolon(tokens: list[SQLToken]) -> bool:
    semicolons = [index for index, token in enumerate(tokens) if token.kind == ";"]
    return not semicolons or semicolons == [len(tokens) - 1]


def _database_administration(
        tokens: list[SQLToken],
        *,
        classifier_validated: bool = False,
) -> _BatchFacts | None:
    if len(tokens) < 3 or not _word(tokens[0]) or not _word(tokens[1], "DATABASE"):
        return None
    verb = tokens[0].value.upper()
    if verb not in {"CREATE", "ALTER"}:
        return None
    name = _identifier(tokens[2])
    if name is None or name.upper() == "CURRENT":
        return None
    effective = tokens[:-1] if tokens[-1].kind == ";" else tokens
    minimal_create = verb == "CREATE" and len(effective) == 3
    if not classifier_validated and not minimal_create:
        return None
    return _BatchFacts(
        commands=1,
        ddl=1,
        create_database=int(verb == "CREATE"),
        alter_database=int(verb == "ALTER"),
        evidence=((name, verb.lower()),),
    )


def _use_statement(tokens: list[SQLToken]) -> _BatchFacts | None:
    if not tokens or not _word(tokens[0], "USE") or not _only_terminal_semicolon(tokens):
        return None
    effective = tokens[:-1] if tokens[-1].kind == ";" else tokens
    if len(effective) != 2:
        return None
    name = _identifier(effective[1])
    if name is None:
        return None
    return _BatchFacts(commands=1, use=1, evidence=((name, "use"),))


def _qualified_insert_evidence(tokens: list[SQLToken]) -> tuple[tuple[str, str], ...]:
    evidence: list[tuple[str, str]] = []
    for index, token in enumerate(tokens):
        if not _word(token, "INSERT"):
            continue
        cursor = index + 1
        if cursor < len(tokens) and _word(tokens[cursor], "INTO"):
            cursor += 1
        if cursor + 4 >= len(tokens):
            continue
        database = _identifier(tokens[cursor])
        if (database is not None and tokens[cursor + 1].kind == "."
                and _identifier(tokens[cursor + 2]) is not None
                and tokens[cursor + 3].kind == "."
                and _identifier(tokens[cursor + 4]) is not None):
            evidence.append((database, "insert"))
    return tuple(evidence)


def _single_other_family(
        tokens: list[SQLToken],
        *,
        classifier_validated: bool,
) -> bool:
    """Reconhece uma família somente após validação integral do classificador.

    O primeiro token e o balanceamento lexical, isoladamente, não provam que o
    lote contenha uma única instrução T-SQL.
    """
    if not classifier_validated:
        return False
    if not tokens or not _word(tokens[0]) or tokens[0].value.upper() not in _OTHER_FAMILIES:
        return False
    return True


def _inspect_batch(
        text: str,
        first_line: int,
        tokens: list[SQLToken] | None = None,
) -> _BatchFacts:
    """Classifica um lote sem reter comandos ou conteúdo após o retorno."""
    if tokens is None:
        tokens = list(tokenize_sql(text, first_line))
    if not tokens:
        return _BatchFacts(commands=0, has_content=False)

    # A forma mínima CREATE DATABASE <identificador> é fechada e pode ser
    # comprovada aqui. Formas com sufixo dependem da gramática administrativa
    # restrita do classificador abaixo.
    administration = _database_administration(tokens)
    if administration is not None:
        return administration
    use = _use_statement(tokens)
    if use is not None:
        return use

    command_counts = {"INSERT": 0, "IDENTITY_INSERT": 0, "SESSION_SET": 0}
    try:
        for command in classify_offline_batch(text, first_line):
            command_counts[command.kind] += 1
    except SQLClassificationError as error:
        if error.reason == _DATABASE_REFUSAL:
            validated = _database_administration(
                tokens,
                classifier_validated=True,
            )
            return validated or _BatchFacts(classifiable=False)
        if error.reason == _VIEW_REFUSAL:
            return _BatchFacts(commands=1, ddl=1, views=1)
        return _BatchFacts(classifiable=False)

    command_total = sum(command_counts.values())
    if command_total:
        return _BatchFacts(
            commands=command_total,
            inserts=command_counts["INSERT"],
            identity=command_counts["IDENTITY_INSERT"],
            session=command_counts["SESSION_SET"],
            evidence=_qualified_insert_evidence(tokens),
        )
    if _word(tokens[0]) and tokens[0].value.upper() in {"CREATE", "ALTER"}:
        return _BatchFacts(commands=1, ddl=1)
    if _single_other_family(tokens, classifier_validated=True):
        return _BatchFacts(commands=1, other=1)
    return _BatchFacts(classifiable=False)


def _candidate_summary(
        evidence: dict[str, set[str]],
) -> tuple[DiscoveryStatus, str | None, tuple[str, ...], str]:
    evidence = {
        name: kinds
        for name, kinds in evidence.items()
        if name and all(character.isprintable() for character in name)
    }
    strong = {
        name for name, kinds in evidence.items()
        if kinds & {"create", "alter", "insert"}
    }
    if strong:
        relevant = set(strong)
        relevant.update(
            name for name, kinds in evidence.items()
            if "use" in kinds and name.casefold() not in _ADMIN_DATABASES
        )
    else:
        relevant = {
            name for name, kinds in evidence.items()
            if "use" in kinds and name.casefold() not in _ADMIN_DATABASES
        }
    candidates = tuple(sorted(relevant, key=lambda value: (value.casefold(), value)))
    if not candidates:
        return (
            DiscoveryStatus.NAO_DETERMINADO,
            None,
            (),
            "evidência insuficiente",
        )
    if len(candidates) > 1:
        return DiscoveryStatus.AMBIGUO, None, candidates, "evidências incompatíveis"
    candidate = candidates[0]
    kinds = evidence[candidate]
    if kinds & {"create", "alter"} or len(kinds) > 1:
        confidence = "evidência estrutural consistente"
    elif "insert" in kinds:
        confidence = "referência de banco qualificada"
    else:
        confidence = "contexto USE isolado"
    return DiscoveryStatus.DETERMINADO, candidate, candidates, confidence


def _record_evidence(
        target: dict[str, set[str]],
        facts: _BatchFacts,
) -> None:
    for name, kind in facts.evidence:
        target[name].add(kind)


def _discovery_report(
        reader: SQLBatchReader,
        evidence: dict[str, set[str]],
        batches: int,
        coverage: Coverage,
        interruption: str | None,
) -> DatabaseDiscoveryReport:
    status, candidate, candidates, confidence = _candidate_summary(evidence)
    if interruption is not None:
        status = DiscoveryStatus.INTERROMPIDO
        confidence = "análise interrompida"
    return DatabaseDiscoveryReport(
        status=status,
        banco_candidato=candidate,
        bancos_candidatos=candidates,
        confianca=confidence,
        cobertura=coverage,
        linhas_fisicas_lidas=reader.line_number,
        lotes_analisados=batches,
        processamento_completo=coverage is Coverage.COMPLETA,
        motivo_interrupcao=interruption,
    )


def discover_database_stream(
        stream: TextIO,
        *,
        max_batches: int = DEFAULT_DISCOVERY_MAX_BATCHES,
) -> DatabaseDiscoveryReport:
    """Descobre candidatos sem ler além do limite de lotes solicitado."""
    if max_batches < 1:
        raise ValueError("max_batches deve ser positivo")
    reader = SQLBatchReader(stream)
    evidence: dict[str, set[str]] = defaultdict(set)
    batches = 0
    boundaries = 0
    iterator: Iterator[tuple[str, int, int]] = iter(reader)
    try:
        while boundaries < max_batches:
            text, first_line, _ = next(iterator)
            boundaries += 1
            facts = _inspect_batch(text, first_line)
            if facts.has_content:
                batches += 1
            _record_evidence(evidence, facts)
        coverage = Coverage.COMPLETA if reader.reached_eof else Coverage.PRELIMINAR
        return _discovery_report(reader, evidence, batches, coverage, None)
    except StopIteration:
        return _discovery_report(reader, evidence, batches, Coverage.COMPLETA, None)
    except KeyboardInterrupt:
        return _discovery_report(
            reader,
            evidence,
            batches,
            Coverage.PARCIAL,
            "Análise interrompida pelo usuário.",
        )
    except (OSError, UnicodeError):
        return _discovery_report(
            reader,
            evidence,
            batches,
            Coverage.PARCIAL,
            "Falha de leitura do arquivo.",
        )
    except SQLClassificationError:
        return _discovery_report(
            reader,
            evidence,
            batches,
            Coverage.PARCIAL,
            "Falha lexical ou limite defensivo atingido.",
        )


def _interrupted_discovery(message: str) -> DatabaseDiscoveryReport:
    return DatabaseDiscoveryReport(
        status=DiscoveryStatus.INTERROMPIDO,
        banco_candidato=None,
        bancos_candidatos=(),
        confianca="análise interrompida",
        cobertura=Coverage.PARCIAL,
        linhas_fisicas_lidas=0,
        lotes_analisados=0,
        processamento_completo=False,
        motivo_interrupcao=message,
    )


def discover_database_file(path: str) -> DatabaseDiscoveryReport:
    """Abre somente o arquivo UTF-16 solicitado e descobre seus candidatos."""
    report: DatabaseDiscoveryReport | None = None
    try:
        with open(path, "r", encoding="utf-16") as stream:
            report = discover_database_stream(stream)
        assert report is not None
        return report
    except KeyboardInterrupt:
        message = "Análise interrompida pelo usuário."
    except (OSError, UnicodeError):
        message = "Falha de leitura do arquivo."
    if report is None:
        return _interrupted_discovery(message)
    return replace(
        report,
        status=DiscoveryStatus.INTERROMPIDO,
        confianca="análise interrompida",
        cobertura=Coverage.PARCIAL,
        processamento_completo=False,
        motivo_interrupcao=message,
    )


def _new_inventory_counts() -> dict[str, int]:
    return {
        "lotes": 0,
        "comandos": 0,
        "inserts": 0,
        "identity": 0,
        "session": 0,
        "use": 0,
        "ddl": 0,
        "create_database": 0,
        "alter_database": 0,
        "views": 0,
        "other": 0,
        "unclassifiable": 0,
    }


def _add_facts(counts: dict[str, int], facts: _BatchFacts) -> None:
    counts["comandos"] += facts.commands
    counts["inserts"] += facts.inserts
    counts["identity"] += facts.identity
    counts["session"] += facts.session
    counts["use"] += facts.use
    counts["ddl"] += facts.ddl
    counts["create_database"] += facts.create_database
    counts["alter_database"] += facts.alter_database
    counts["views"] += facts.views
    counts["other"] += facts.other


def _inventory_report(
        reader: SQLBatchReader,
        counts: dict[str, int],
        evidence: dict[str, set[str]],
        coverage: Coverage,
        interruption: str | None,
) -> InventoryReport:
    candidates = _candidate_summary(evidence)[2]
    if coverage is Coverage.COMPLETA:
        status = InventoryStatus.CONCLUIDO
    elif interruption == "Análise interrompida pelo usuário.":
        status = InventoryStatus.INTERROMPIDO
    else:
        status = InventoryStatus.PARCIAL
    return InventoryReport(
        status=status,
        cobertura=coverage,
        linhas_fisicas=reader.line_number,
        lotes=counts["lotes"],
        comandos=counts["comandos"],
        comandos_reconhecidos=counts["comandos"],
        inserts=counts["inserts"],
        identity_insert=counts["identity"],
        session_set=counts["session"],
        use=counts["use"],
        ddl_reconhecido=counts["ddl"],
        create_database=counts["create_database"],
        alter_database=counts["alter_database"],
        views=counts["views"],
        outras_familias=counts["other"],
        lotes_nao_classificaveis=counts["unclassifiable"],
        bancos_candidatos=candidates,
        processamento_completo=coverage is Coverage.COMPLETA,
        motivo_interrupcao=interruption,
    )


def _notify_progress(
        callback: Callable[[InventoryProgressSnapshot], None] | None,
        reader: SQLBatchReader,
        counts: dict[str, int],
        *,
        complete: bool,
        byte_position: Callable[[], int] | None,
        total_bytes: int | None,
) -> None:
    """Publica estado agregado sem permitir que falhas de saída afetem a análise."""
    if callback is None:
        return
    position = None
    if byte_position is not None:
        try:
            position = byte_position()
        except (OSError, ValueError):
            position = None
    snapshot = InventoryProgressSnapshot(
        linhas_fisicas=reader.line_number,
        lotes=counts["lotes"],
        processamento_completo=complete,
        posicao_bytes_estimada=position,
        tamanho_bytes=total_bytes,
    )
    try:
        callback(snapshot)
    except Exception:
        # Progresso é observacional: falha do destino de stderr não altera o
        # inventário nem seus códigos de saída.
        return


def inventory_stream(
        stream: TextIO,
        *,
        on_progress: Callable[[InventoryProgressSnapshot], None] | None = None,
        _byte_position: Callable[[], int] | None = None,
        _total_bytes: int | None = None,
) -> InventoryReport:
    """Percorre lotes completos e continua após recusas em fronteiras seguras."""
    reader = SQLBatchReader(stream)
    counts = _new_inventory_counts()
    evidence: dict[str, set[str]] = defaultdict(set)
    try:
        for text, first_line, _ in reader:
            try:
                tokens = list(tokenize_sql(text, first_line))
            except SQLClassificationError:
                counts["lotes"] += 1
                counts["unclassifiable"] += 1
                return _inventory_report(
                    reader,
                    counts,
                    evidence,
                    Coverage.PARCIAL,
                    "Falha lexical ou perda de sincronização.",
                )
            if not tokens:
                continue
            counts["lotes"] += 1
            facts = _inspect_batch(text, first_line, tokens)
            if facts.classifiable:
                _add_facts(counts, facts)
                _record_evidence(evidence, facts)
            else:
                counts["unclassifiable"] += 1
            if counts["lotes"] % PROGRESS_BATCH_INTERVAL == 0:
                _notify_progress(
                    on_progress,
                    reader,
                    counts,
                    complete=False,
                    byte_position=_byte_position,
                    total_bytes=_total_bytes,
                )
        report = _inventory_report(
            reader, counts, evidence, Coverage.COMPLETA, None,
        )
        _notify_progress(
            on_progress,
            reader,
            counts,
            complete=True,
            byte_position=_byte_position,
            total_bytes=_total_bytes,
        )
        return report
    except KeyboardInterrupt:
        return _inventory_report(
            reader,
            counts,
            evidence,
            Coverage.PARCIAL,
            "Análise interrompida pelo usuário.",
        )
    except (OSError, UnicodeError):
        return _inventory_report(
            reader,
            counts,
            evidence,
            Coverage.PARCIAL,
            "Falha de leitura do arquivo.",
        )
    except SQLClassificationError:
        return _inventory_report(
            reader,
            counts,
            evidence,
            Coverage.PARCIAL,
            "Falha lexical ou limite defensivo atingido.",
        )


def _interrupted_inventory(message: str) -> InventoryReport:
    reader = SQLBatchReader(_EmptyStream())
    return _inventory_report(
        reader,
        _new_inventory_counts(),
        defaultdict(set),
        Coverage.PARCIAL,
        message,
    )


class _EmptyStream:
    def readline(self, size: int = -1) -> str:
        return ""


def _file_progress_source(
        stream: TextIO,
) -> tuple[Callable[[], int] | None, int | None]:
    """Obtém estimativa binária sem usar o cookie opaco de TextIOWrapper."""
    try:
        total_bytes = os.fstat(stream.fileno()).st_size
        binary_buffer = stream.buffer  # type: ignore[attr-defined]
        return binary_buffer.tell, total_bytes
    except (AttributeError, OSError, ValueError):
        return None, None


def inventory_file(
        path: str,
        *,
        on_progress: Callable[[InventoryProgressSnapshot], None] | None = None,
) -> InventoryReport:
    """Abre somente o arquivo UTF-16 solicitado e produz seu inventário."""
    report: InventoryReport | None = None
    try:
        with open(path, "r", encoding="utf-16") as stream:
            byte_position, total_bytes = _file_progress_source(stream)
            report = inventory_stream(
                stream,
                on_progress=on_progress,
                _byte_position=byte_position,
                _total_bytes=total_bytes,
            )
        assert report is not None
        return report
    except KeyboardInterrupt:
        message = "Análise interrompida pelo usuário."
    except (OSError, UnicodeError):
        message = "Falha de leitura do arquivo."
    if report is None:
        return _interrupted_inventory(message)
    status = (
        InventoryStatus.INTERROMPIDO
        if message == "Análise interrompida pelo usuário."
        else InventoryStatus.PARCIAL
    )
    return replace(
        report,
        status=status,
        cobertura=Coverage.PARCIAL,
        processamento_completo=False,
        motivo_interrupcao=message,
    )
