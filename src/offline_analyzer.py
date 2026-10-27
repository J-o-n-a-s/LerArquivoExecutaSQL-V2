"""Análise incremental e estrita do subconjunto SQL, sem efeitos externos."""

import argparse
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import NoReturn, TextIO

from offline_inspection import (
    Coverage,
    DatabaseDiscoveryReport,
    DiscoveryStatus,
    InventoryReport,
    InventoryProgressSnapshot,
    InventoryStatus,
    PROGRESS_BATCH_INTERVAL,
    discover_database_file,
    discover_database_stream,
    inventory_file,
    inventory_stream,
)
from sql_classifier import SQLClassificationError, SQLCommandReader


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        """Encerra com erro fixo para não repetir argumentos potencialmente privados."""
        self.print_usage(sys.stderr)
        self.exit(2, "Erro: argumentos inválidos.\n")


class AnalysisStatus(str, Enum):
    """Estados públicos e mutuamente exclusivos da análise offline."""

    CONCLUIDO = "concluido"
    RECUSADO = "recusado"
    INTERROMPIDO = "interrompido"


@dataclass(frozen=True)
class OfflineAnalysisReport:
    """Resultado imutável, agregado e sem conteúdo SQL da análise offline."""

    status: AnalysisStatus
    linhas_fisicas_lidas: int
    linhas_ignoradas: int
    comandos_classificados: int
    inserts_classificados: int
    identity_insert_classificados: int
    session_set_classificados: int
    linha_primeira_recusa: int | None
    motivo_recusa: str | None
    motivo_interrupcao: str | None
    processamento_completo: bool


def _build_report(
        status: AnalysisStatus,
        reader: SQLCommandReader,
        counts: Mapping[str, int],
        lines: int,
        *,
        refusal: SQLClassificationError | None = None,
        interruption: str | None = None,
) -> OfflineAnalysisReport:
    """Consolida contadores e diagnóstico sanitizado em um relatório."""
    return OfflineAnalysisReport(
        status=status,
        linhas_fisicas_lidas=lines,
        linhas_ignoradas=reader.ignored_lines,
        comandos_classificados=counts["commands"],
        inserts_classificados=counts["INSERT"],
        identity_insert_classificados=counts["IDENTITY_INSERT"],
        session_set_classificados=counts["SESSION_SET"],
        linha_primeira_recusa=None if refusal is None else refusal.line,
        motivo_recusa=None if refusal is None else refusal.reason,
        motivo_interrupcao=interruption,
        processamento_completo=status is AnalysisStatus.CONCLUIDO,
    )


def analyze_stream(
        stream: TextIO,
        *,
        target_database: str,
) -> OfflineAnalysisReport:
    """Classifica um stream incrementalmente, sem executar SQL.

    O stream deve fornecer texto já decodificado. A análise usa
    ``target_database`` apenas como contexto lógico, para na primeira recusa e
    converte interrupções ou falhas de leitura em relatório sanitizado.
    """
    lines = 0
    counts = {
        "commands": 0,
        "INSERT": 0,
        "IDENTITY_INSERT": 0,
        "SESSION_SET": 0,
    }

    def record_line(number: int) -> None:
        nonlocal lines
        lines = number

    reader = SQLCommandReader(
        stream,
        target_database=target_database,
        on_line=record_line,
    )
    try:
        for command in reader:
            counts["commands"] += 1
            counts[command.kind] += 1
    except SQLClassificationError as error:
        return _build_report(
            AnalysisStatus.RECUSADO,
            reader,
            counts,
            lines,
            refusal=error,
        )
    except KeyboardInterrupt:
        return _build_report(
            AnalysisStatus.INTERROMPIDO,
            reader,
            counts,
            lines,
            interruption="Análise interrompida pelo usuário.",
        )
    except (OSError, UnicodeError):
        return _build_report(
            AnalysisStatus.INTERROMPIDO,
            reader,
            counts,
            lines,
            interruption="Falha de leitura do arquivo.",
        )
    return _build_report(AnalysisStatus.CONCLUIDO, reader, counts, lines)


def _empty_interrupted_report(message: str) -> OfflineAnalysisReport:
    """Cria o relatório de interrupção ocorrida antes da abertura do stream."""
    return OfflineAnalysisReport(
        status=AnalysisStatus.INTERROMPIDO,
        linhas_fisicas_lidas=0,
        linhas_ignoradas=0,
        comandos_classificados=0,
        inserts_classificados=0,
        identity_insert_classificados=0,
        session_set_classificados=0,
        linha_primeira_recusa=None,
        motivo_recusa=None,
        motivo_interrupcao=message,
        processamento_completo=False,
    )


def analyze_file(path: str, *, target_database: str) -> OfflineAnalysisReport:
    """Analisa um arquivo local conforme o contrato UTF-16.

    O arquivo é aberto somente para leitura e fechado pelo gerenciador de
    contexto. Erros de abertura, decodificação, leitura ou interrupção são
    representados no relatório, sem incluir caminho nem conteúdo SQL.
    """
    report = None
    try:
        with open(path, "r", encoding="utf-16") as stream:
            report = analyze_stream(stream, target_database=target_database)
        return report
    except KeyboardInterrupt:
        message = "Análise interrompida pelo usuário."
    except (OSError, UnicodeError):
        message = "Falha de leitura do arquivo."
    if report is None:
        return _empty_interrupted_report(message)
    return replace(
        report,
        status=AnalysisStatus.INTERROMPIDO,
        motivo_interrupcao=message,
        processamento_completo=False,
    )


def format_count(value: int) -> str:
    """Formata contador com separação de milhar sem depender de locale."""
    return f"{value:,}".replace(",", ".")


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class InventoryProgressReporter:
    """Emite progresso limitado por tempo, separado do relatório final."""

    def __init__(
            self,
            stream: TextIO,
            *,
            clock: Callable[[], float] = time.monotonic,
            interval_seconds: float = 5.0,
    ) -> None:
        self.stream = stream
        self.clock = clock
        self.interval_seconds = interval_seconds
        self.started_at: float | None = None
        self.last_update: float | None = None
        self.completed = False

    def _print(self, message: str) -> None:
        try:
            print(message, file=self.stream, flush=True)
        except (OSError, UnicodeError):
            return

    def start(self) -> None:
        if self.started_at is not None:
            return
        now = self.clock()
        self.started_at = self.last_update = now
        self._print("Inventário offline iniciado.")

    @staticmethod
    def _percentage(snapshot: InventoryProgressSnapshot) -> int | None:
        if snapshot.processamento_completo and snapshot.tamanho_bytes is not None:
            return 100
        if (snapshot.posicao_bytes_estimada is None
                or snapshot.tamanho_bytes is None
                or snapshot.tamanho_bytes <= 0):
            return None
        percentage = int(
            snapshot.posicao_bytes_estimada * 100 / snapshot.tamanho_bytes,
        )
        return min(99, max(0, percentage))

    def update(self, snapshot: InventoryProgressSnapshot) -> None:
        if self.started_at is None:
            self.start()
        now = self.clock()
        assert self.started_at is not None
        assert self.last_update is not None
        if (not snapshot.processamento_completo
                and now - self.last_update < self.interval_seconds):
            return
        percentage = self._percentage(snapshot)
        progress = (
            "percentual indisponível"
            if percentage is None
            else f"{percentage}% estimado"
        )
        self._print(
            f"Progresso: {progress} | "
            f"linhas: {format_count(snapshot.linhas_fisicas)} | "
            f"lotes: {format_count(snapshot.lotes)} | "
            f"decorrido: {_format_elapsed(now - self.started_at)}",
        )
        self.last_update = now
        self.completed = snapshot.processamento_completo

    def finish(self, report: InventoryReport) -> None:
        """Finaliza sem apagar atualizações nem substituir o relatório stdout."""
        if self.started_at is None:
            return
        if report.processamento_completo:
            if not self.completed:
                self.update(InventoryProgressSnapshot(
                    linhas_fisicas=report.linhas_fisicas,
                    lotes=report.lotes,
                    processamento_completo=True,
                    posicao_bytes_estimada=None,
                    tamanho_bytes=None,
                ))
            return
        now = self.clock()
        self._print(
            "Inventário offline não concluído | "
            f"linhas: {format_count(report.linhas_fisicas)} | "
            f"lotes: {format_count(report.lotes)} | "
            f"decorrido: {_format_elapsed(now - self.started_at)}",
        )


def _interactive_progress_enabled(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def _print_report(report: OfflineAnalysisReport) -> None:
    """Emite somente os campos públicos e sanitizados do relatório."""
    print(f"Status: {report.status.value}.")
    print(f"Processamento completo: {'sim' if report.processamento_completo else 'não'}.")
    print(f"Linhas físicas lidas: {format_count(report.linhas_fisicas_lidas)}.")
    print(f"Linhas ignoradas pelo leitor: {format_count(report.linhas_ignoradas)}.")
    print(f"Comandos classificados: {format_count(report.comandos_classificados)}.")
    print(f"INSERT classificados: {format_count(report.inserts_classificados)}.")
    print(f"IDENTITY_INSERT classificados: {format_count(report.identity_insert_classificados)}.")
    print(f"SESSION_SET classificados: {format_count(report.session_set_classificados)}.")
    if report.linha_primeira_recusa is not None:
        print(f"Linha da primeira recusa: {format_count(report.linha_primeira_recusa)}.")
        print(f"Motivo da recusa: {report.motivo_recusa}")
    if report.motivo_interrupcao is not None:
        print(f"Motivo da interrupção: {report.motivo_interrupcao}")


def _exit_code(report: OfflineAnalysisReport) -> int:
    """Traduz o estado da análise para o código de saída do processo."""
    if report.status is AnalysisStatus.CONCLUIDO:
        return 0
    if report.status is AnalysisStatus.RECUSADO:
        return 2
    if report.motivo_interrupcao == "Análise interrompida pelo usuário.":
        return 130
    return 1


def _argument_parser() -> argparse.ArgumentParser:
    """Cria o parser cuja saída de erro não reproduz argumentos privados."""
    parser = _SanitizedArgumentParser(
        description="Classifica um arquivo SQL local sem executar comandos.",
    )
    parser.add_argument("--file", required=True, help="arquivo SQL UTF-16")
    parser.add_argument(
        "--database",
        required=False,
        help="destino lógico usado somente pela classificação",
    )
    parser.add_argument(
        "--inventory",
        action="store_true",
        help="percorre o arquivo e inventaria lotes offline",
    )
    return parser


def _print_discovery_report(report: DatabaseDiscoveryReport) -> None:
    """Emite apenas nomes de banco e diagnósticos públicos da descoberta."""
    print("Modo: descoberta de banco")
    if report.status is DiscoveryStatus.AMBIGUO:
        print("Banco candidato: ambíguo")
    elif report.banco_candidato is None:
        print("Banco candidato: banco não determinado")
    else:
        print(f"Banco candidato: {report.banco_candidato}")
    if report.status is DiscoveryStatus.AMBIGUO:
        print(f"Bancos candidatos: {', '.join(report.bancos_candidatos)}")
    print(f"Confiança: {report.confianca}")
    print(f"Cobertura: {report.cobertura.value}")
    print(f"Linhas físicas lidas: {format_count(report.linhas_fisicas_lidas)}")
    print(f"Lotes analisados: {format_count(report.lotes_analisados)}")
    if report.motivo_interrupcao is not None:
        print(f"Motivo da interrupção: {report.motivo_interrupcao}")
    print("Conexão com SQL Server: nenhuma")


def _discovery_exit_code(report: DatabaseDiscoveryReport) -> int:
    if report.status is DiscoveryStatus.DETERMINADO:
        return 0
    if report.status is DiscoveryStatus.AMBIGUO:
        return 3
    if report.status is DiscoveryStatus.NAO_DETERMINADO:
        return 4
    if report.motivo_interrupcao == "Análise interrompida pelo usuário.":
        return 130
    return 1


def _print_inventory_report(report: InventoryReport) -> None:
    """Emite contadores agregados, sem conteúdo SQL ou valores de dados."""
    print("Modo: inventário offline")
    print(f"Status: {report.status.value}")
    print(f"Cobertura: {report.cobertura.value}")
    print(f"Processamento completo: {'sim' if report.processamento_completo else 'não'}")
    print(f"Linhas físicas: {format_count(report.linhas_fisicas)}")
    print(f"Lotes: {format_count(report.lotes)}")
    print(f"Comandos com contagem segura: {format_count(report.comandos)}")
    print(f"Comandos reconhecidos: {format_count(report.comandos_reconhecidos)}")
    print(f"INSERT: {format_count(report.inserts)}")
    print(f"IDENTITY_INSERT: {format_count(report.identity_insert)}")
    print(f"SESSION_SET: {format_count(report.session_set)}")
    print(f"USE: {format_count(report.use)}")
    print(f"DDL reconhecido: {format_count(report.ddl_reconhecido)}")
    print(f"CREATE DATABASE: {format_count(report.create_database)}")
    print(f"ALTER DATABASE: {format_count(report.alter_database)}")
    print(f"Views reconhecidas: {format_count(report.views)}")
    print(f"Outras famílias: {format_count(report.outras_familias)}")
    print(f"Lotes não classificáveis: {format_count(report.lotes_nao_classificaveis)}")
    if report.bancos_candidatos:
        print(f"Bancos candidatos: {', '.join(report.bancos_candidatos)}")
    else:
        print("Bancos candidatos: banco não determinado")
    if report.motivo_interrupcao is not None:
        print(f"Motivo da interrupção: {report.motivo_interrupcao}")
    print("Conexão com SQL Server: nenhuma")


def _inventory_exit_code(report: InventoryReport) -> int:
    if report.status is InventoryStatus.CONCLUIDO:
        return 0
    if report.status is InventoryStatus.INTERROMPIDO:
        return 130
    return 3


def main(argv: list[str] | None = None) -> int:
    """Executa a interface de linha de comando e retorna seu código de saída."""
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.inventory and arguments.database is not None:
        parser.exit(2, "Erro: --inventory e --database são incompatíveis.\n")
    if arguments.inventory:
        progress = (
            InventoryProgressReporter(sys.stderr)
            if _interactive_progress_enabled(sys.stderr)
            else None
        )
        if progress is not None:
            progress.start()
        inventory = inventory_file(
            arguments.file,
            on_progress=None if progress is None else progress.update,
        )
        if progress is not None:
            progress.finish(inventory)
        _print_inventory_report(inventory)
        return _inventory_exit_code(inventory)
    if arguments.database is None:
        discovery = discover_database_file(arguments.file)
        _print_discovery_report(discovery)
        return _discovery_exit_code(discovery)
    report = analyze_file(arguments.file, target_database=arguments.database)
    _print_report(report)
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
