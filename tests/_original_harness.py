"""Observa os casos históricos pela aplicação real, com recursos simulados.

A classificação agora é um módulo real; não precisamos mais extrair o laço
por AST. Não há classificador alternativo ou driver ODBC neste adaptador.
"""

from dataclasses import dataclass

from tests._application_harness import SYNTHETIC_PATH, Scenario


@dataclass(frozen=True)
class Observation:
    statements: tuple[str, ...]
    commits: int
    total_lines: int
    accepted_lines: int
    rejected_lines: int
    messages: tuple[str, ...]
    exit_code: int
    output: str


class OriginalReader:
    def _observe(self, scenario, log):
        scenario.input.side_effect = [SYNTHETIC_PATH, log, "1"]
        result = scenario.run()
        if result.uncaught is not None:
            raise result.uncaught
        return Observation(
            statements=tuple(scenario.cursor.statements),
            commits=scenario.connection.commit_attempts,
            total_lines=result.namespace["num_linhas"],
            accepted_lines=result.namespace["linhas_inseridas"],
            rejected_lines=result.namespace["linhas_em_branco"],
            messages=tuple(line for line in result.output.splitlines() if line.startswith("Executando a linha ->")),
            exit_code=result.exit_code,
            output=result.output,
        )

    def observe_text(self, text, *, log="n"):
        return self._observe(Scenario(text=text), log)

    def observe_bytes(self, payload, *, log="n"):
        return self._observe(Scenario(payload=payload), log)
