"""Análise incremental e estrita do subconjunto SQL, sem efeitos externos."""

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import NoReturn, TextIO

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


def _print_report(report: OfflineAnalysisReport) -> None:
    """Emite somente os campos públicos e sanitizados do relatório."""
    print(f"Status: {report.status.value}.")
    print(f"Processamento completo: {'sim' if report.processamento_completo else 'não'}.")
    print(f"Linhas físicas lidas: {report.linhas_fisicas_lidas}.")
    print(f"Linhas ignoradas pelo leitor: {report.linhas_ignoradas}.")
    print(f"Comandos classificados: {report.comandos_classificados}.")
    print(f"INSERT classificados: {report.inserts_classificados}.")
    print(f"IDENTITY_INSERT classificados: {report.identity_insert_classificados}.")
    print(f"SESSION_SET classificados: {report.session_set_classificados}.")
    if report.linha_primeira_recusa is not None:
        print(f"Linha da primeira recusa: {report.linha_primeira_recusa}.")
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
        required=True,
        help="destino lógico usado somente pela classificação",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Executa a interface de linha de comando e retorna seu código de saída."""
    arguments = _argument_parser().parse_args(argv)
    report = analyze_file(arguments.file, target_database=arguments.database)
    _print_report(report)
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
