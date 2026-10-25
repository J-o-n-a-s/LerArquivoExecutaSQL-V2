"""Executa o fonte real com entrada, arquivo e pyodbc estritamente simulados."""

import contextlib
import io
import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE_PATH = Path(__file__).resolve().parents[1] / "src" / "main.py"
SOURCE_CODE = compile(SOURCE_PATH.read_bytes(), str(SOURCE_PATH), "exec")
SYNTHETIC_PATH = "synthetic://fase2a"
SYNTHETIC_SERVER = "Servidor\\InstanciaSintetica"
SYNTHETIC_DATABASE = "DestinoAplicacaoSintetico"
SYNTHETIC_DRIVER = "Driver ODBC Sintetico"
PRIVATE = "SEGREDO_SINTETICO_NAO_EXIBIR"
STATEMENT = f"INSERT INTO [dbo].[Sintetico] ([nome]) VALUES (N'{PRIVATE}')"


class DatabaseError(Exception):
    pass


class ExecuteError(DatabaseError):
    pass


class CommitError(DatabaseError):
    pass


class RollbackError(DatabaseError):
    pass


class CloseError(Exception):
    pass


@dataclass
class ApplicationResult:
    exit_code: int | None
    output: str
    uncaught: BaseException | None
    namespace: dict


class SyntheticFile:
    def __init__(self, scenario, text, payload):
        self.scenario = scenario
        self.stream = (
            io.StringIO(text, newline=None)
            if payload is None
            else io.TextIOWrapper(io.BytesIO(payload), encoding="utf-16")
        )
        self.read_lines = 0
        self.close_attempts = 0

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def readline(self, size=-1):
        if self.read_lines == self.scenario.read_error_after:
            raise self.scenario.read_error
        line = self.stream.readline(size)
        if line:
            self.read_lines += 1
        return line

    def close(self):
        self.close_attempts += 1
        self.scenario.events.append("file.close")
        self.stream.close()
        if self.scenario.file_close_error is not None:
            raise self.scenario.file_close_error


class SyntheticCursor:
    def __init__(self, scenario):
        self.scenario = scenario
        self.statements = []
        self.close_attempts = 0

    def execute(self, statement):
        self.scenario.events.append("execute")
        self.statements.append(statement)
        self.scenario.pending.append(statement)
        if len(self.statements) == self.scenario.execute_error_at:
            raise self.scenario.execute_error

    def close(self):
        self.close_attempts += 1
        self.scenario.events.append("cursor.close")
        if self.scenario.cursor_close_error is not None:
            raise self.scenario.cursor_close_error


class SyntheticConnection:
    def __init__(self, scenario):
        self.scenario = scenario
        self.commit_attempts = 0
        self.rollback_attempts = 0
        self.close_attempts = 0

    def cursor(self):
        self.scenario.events.append("cursor")
        if self.scenario.cursor_error is not None:
            raise self.scenario.cursor_error
        return self.scenario.cursor

    def commit(self):
        self.scenario.events.append("commit")
        self.commit_attempts += 1
        failing = self.commit_attempts == self.scenario.commit_error_at
        if not failing or self.scenario.commit_applied_before_error:
            self.scenario.confirmed.extend(self.scenario.pending)
            self.scenario.pending.clear()
        if failing:
            raise self.scenario.commit_error

    def rollback(self):
        self.scenario.events.append("rollback")
        self.rollback_attempts += 1
        if self.scenario.rollback_error is not None:
            raise self.scenario.rollback_error
        self.scenario.pending.clear()

    def close(self):
        self.close_attempts += 1
        self.scenario.events.append("connection.close")
        if self.scenario.connection_close_error is not None:
            raise self.scenario.connection_close_error


class Scenario:
    def __init__(self, *, text=STATEMENT + "\n", payload=None, **options):
        defaults = {
            "target_database": None,
            "environment": None,
            "expected_path": SYNTHETIC_PATH,
            "open_error": None,
            "connect_error": None,
            "cursor_error": None,
            "read_error": OSError(PRIVATE),
            "read_error_after": None,
            "execute_error": ExecuteError(PRIVATE),
            "execute_error_at": None,
            "commit_error": CommitError(PRIVATE),
            "commit_error_at": None,
            "commit_applied_before_error": False,
            "rollback_error": None,
            "file_close_error": None,
            "cursor_close_error": None,
            "connection_close_error": None,
        }
        unknown = set(options) - set(defaults)
        if unknown:
            raise AssertionError(f"Opções de simulação desconhecidas: {unknown}")
        for name, value in (defaults | options).items():
            setattr(self, name, value)
        if self.environment is None:
            self.environment = {
                "LER_SQL_SERVER": SYNTHETIC_SERVER,
                "LER_SQL_DATABASE": self.target_database or SYNTHETIC_DATABASE,
                "LER_SQL_DRIVER": SYNTHETIC_DRIVER,
            }
        self.events = []
        self.pending = []
        self.confirmed = []
        self.file = SyntheticFile(self, text, payload)
        self.cursor = SyntheticCursor(self)
        self.connection = SyntheticConnection(self)
        self.open = Mock(side_effect=self._open)
        self.connect = Mock(side_effect=self._connect)
        self.input = Mock(side_effect=[SYNTHETIC_PATH, "n", "1"])
        self.print = print
        self.pyodbc = types.ModuleType("pyodbc")
        self.pyodbc.Error = DatabaseError
        self.pyodbc.connect = self.connect

    def _open(self, path, mode, *, encoding):
        if (path, mode, encoding) != (self.expected_path, "r", "utf-16"):
            raise AssertionError("Bloqueada abertura fora da entrada sintética UTF-16.")
        self.events.append("open")
        if self.open_error is not None:
            raise self.open_error
        return self.file

    def _connect(self, connection_string):
        self.events.append("connect")
        if self.connect_error is not None:
            raise self.connect_error
        return self.connection

    def run(self, *, as_main=True):
        # Ler o fonte acontece antes de substituir qualquer operação. Nunca
        # importamos main nem resolvemos um driver ODBC instalado no ambiente.
        output = io.StringIO()
        namespace = {
            "__name__": "__main__" if as_main else "phase2a_under_test",
            "__file__": str(SOURCE_PATH),
            "open": self.open,
            "input": self.input,
            "print": self.print,
        }
        exit_code = 0
        uncaught = None
        with (patch.dict(sys.modules, {"pyodbc": self.pyodbc}),
              patch.dict(os.environ, self.environment, clear=True),
              contextlib.redirect_stdout(output),
              contextlib.redirect_stderr(output)):
            try:
                exec(SOURCE_CODE, namespace)
            except SystemExit as error:
                exit_code = error.code
            except BaseException as error:
                uncaught = error
                exit_code = None
        return ApplicationResult(exit_code, output.getvalue(), uncaught, namespace)
