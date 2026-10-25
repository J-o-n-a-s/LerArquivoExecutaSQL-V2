"""Subconjunto conservador de importação T-SQL.

Um lote limitado é validado antes de emitir comandos. GO delimita análise,
nunca transações. Não há driver, execução SQL ou lista de comandos aqui.
"""

import io
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import NoReturn, TextIO, cast

from preflight import (
    CreateDatabaseProfile,
    DatabaseFileProfile,
    DatabaseInstructionFacts,
    signature_by_id,
    signature_by_option,
)


MAX_LINE_CHARS = 65_536
MAX_STATEMENT_CHARS = 262_144
MAX_BATCH_CHARS = 1_048_576
MAX_COMMENT_DEPTH = 32
MAX_DDL_DEPTH = 64
_SESSION_OPTIONS = ("ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_PADDING")
_INDEX_BOOL_OPTIONS = {"PAD_INDEX", "STATISTICS_NORECOMPUTE", "SORT_IN_TEMPDB", "ONLINE", "ALLOW_ROW_LOCKS", "ALLOW_PAGE_LOCKS"}
_INDEX_OFF_OPTIONS = {"DROP_EXISTING", "IGNORE_DUP_KEY", "OPTIMIZE_FOR_SEQUENTIAL_KEY"}
_PROPERTY_PARAMS = {"name", "value", "level0type", "level0name", "level1type", "level1name", "level2type", "level2name"}
_DESIGNER_LAYOUT_PROPERTIES = {"MS_DiagramPane1", "MS_DiagramPane2"}
_DDL_STATEMENT_STARTERS = {
    "ALTER", "BACKUP", "BEGIN", "BREAK", "BULK", "CLOSE", "COMMIT",
    "CONTINUE", "CREATE", "DBCC", "DEALLOCATE", "DECLARE", "DELETE", "DENY",
    "DROP", "EXEC", "EXECUTE", "FETCH", "GO", "GOTO", "GRANT", "IF",
    "INSERT", "KILL", "MERGE", "OPEN", "PRINT", "RAISERROR", "RECONFIGURE",
    "RESTORE", "RETURN", "REVOKE", "ROLLBACK", "SAVE", "SELECT", "SET",
    "SHUTDOWN", "THROW", "TRUNCATE", "UPDATE", "USE", "WAITFOR", "WHILE",
}
_DATABASE_RECOGNIZED_REASON = "Instrução administrativa de banco reconhecida, mas não autorizada para descarte."
_DATABASE_INVALID_REASON = "Instrução administrativa de banco fora do contrato sintático restrito."
_DATABASE_WRONG_TARGET_REASON = "Alvo administrativo de banco diferente do destino configurado."
_VIEW_RECOGNIZED_REASON = "Definição de view reconhecida, mas exige equivalência externa comprovada."
_VIEW_INVALID_REASON = "Definição de view fora do contrato sintático restrito."
_VIEW_WRONG_CONTEXT_REASON = "Definição de view fora do contexto de banco de destino autorizado."
_DATABASE_SIMPLE_OPTIONS = {
    "ANSI_NULL_DEFAULT": "OFF",
    "ANSI_NULLS": "OFF",
    "ANSI_PADDING": "OFF",
    "ANSI_WARNINGS": "OFF",
    "ARITHABORT": "OFF",
    "CONCAT_NULL_YIELDS_NULL": "OFF",
    "NUMERIC_ROUNDABORT": "OFF",
    "QUOTED_IDENTIFIER": "OFF",
    "CURSOR_CLOSE_ON_COMMIT": "OFF",
    "CURSOR_DEFAULT": "GLOBAL",
    "RECURSIVE_TRIGGERS": "OFF",
    "AUTO_CLOSE": "OFF",
    "AUTO_SHRINK": "OFF",
    "AUTO_UPDATE_STATISTICS": "ON",
    "AUTO_UPDATE_STATISTICS_ASYNC": "OFF",
    "DATE_CORRELATION_OPTIMIZATION": "OFF",
    "PARAMETERIZATION": "SIMPLE",
    "ALLOW_SNAPSHOT_ISOLATION": "OFF",
    "READ_COMMITTED_SNAPSHOT": "OFF",
    "HONOR_BROKER_PRIORITY": "OFF",
    "TRUSTWORTHY": "OFF",
    "DB_CHAINING": "OFF",
    "RECOVERY": "FULL",
    "PAGE_VERIFY": "CHECKSUM",
}
_DATABASE_EQUALS_OPTIONS = {
    "QUERY_STORE": "OFF",
    "DELAYED_DURABILITY": "DISABLED",
    "ACCELERATED_DATABASE_RECOVERY": "OFF",
}
_DATABASE_FLAG_OPTIONS = {"DISABLE_BROKER", "MULTI_USER", "READ_WRITE"}


class SQLClassificationError(ValueError):
    """Recusa sanitizada associada à primeira linha relevante do comando."""

    def __init__(self, line: int, reason: str) -> None:
        self.line = line
        self.reason = reason  # Somente motivos fixos; nunca incluir texto SQL.
        super().__init__(f"Linha {line}: {reason}")


@dataclass(frozen=True)
class SQLCommand:
    """Comando validado e pronto para execução sequencial pelo importador."""

    text: str
    line: int
    kind: str


@dataclass(frozen=True, repr=False)
class _DatabaseInstruction(DatabaseInstructionFacts):
    """Metadados seguros internos; nunca representa um comando executável."""


@dataclass(frozen=True, repr=False)
class _ViewDefinition:
    """Reconhecimento mínimo, sem nome, SQL ou autorização de descarte."""

    line: int
    body_kind: str


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int
    line: int


class _LineContext:
    """Estado lexical entre linhas, apenas para reconhecer fronteiras GO."""

    def __init__(self):
        self.quote = None
        self.depth = 0

    def scan(self, text, line):
        visible: list[str] = []

        def mark(char):
            # Somente precisamos saber se o código visível é exatamente GO.
            if char.isspace():
                if visible and visible[-1] != " " and len(visible) < 16:
                    visible.append(" ")
            elif len(visible) < 16:
                visible.append(char)

        if self.quote:
            mark("?")
        index = 0
        while index < len(text):
            char = text[index]
            pair = text[index:index + 2]
            if self.depth:
                if pair == "/*":
                    self.depth += 1
                    if self.depth > MAX_COMMENT_DEPTH:
                        raise SQLClassificationError(line, "Limite de comentários aninhados excedido.")
                    index += 2
                elif pair == "*/":
                    self.depth -= 1
                    index += 2
                else:
                    index += 1
            elif self.quote:
                closing = "]" if self.quote == "[" else self.quote
                if char == closing:
                    if pair == closing * 2:
                        index += 2
                    else:
                        self.quote = None
                        index += 1
                else:
                    index += 1
            elif pair == "--":
                break
            elif pair == "/*":
                mark(" ")
                self.depth = 1
                index += 2
            elif char in "'\"[":
                mark("?")
                self.quote = char
                index += 1
            else:
                mark(char)
                index += 1
        return "".join(visible).strip()


_NUMBER = re.compile(r"(?:0[xX][0-9a-fA-F]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")
_RESERVED = {"INSERT", "INTO", "VALUES", "SET", "GO", "CREATE", "ALTER", "SELECT", "EXEC", "EXECUTE", "BEGIN", "END", "UPDATE", "DELETE", "MERGE", "DROP", "ON", "OFF", "NULL", "DEFAULT"}


def _tokens(text, first_line):
    index = 0
    line = first_line
    while index < len(text):
        char = text[index]
        start = index
        token_line = line
        pair = text[index:index + 2]
        if char.isspace():
            line += char == "\n"
            index += 1
            continue
        if pair == "--":
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        if pair == "/*":
            depth = 1
            index += 2
            while index < len(text) and depth:
                pair = text[index:index + 2]
                if pair == "/*":
                    depth += 1
                    if depth > MAX_COMMENT_DEPTH:
                        raise SQLClassificationError(line, "Limite de comentários aninhados excedido.")
                    index += 2
                elif pair == "*/":
                    depth -= 1
                    index += 2
                else:
                    line += text[index] == "\n"
                    index += 1
            if depth:
                raise SQLClassificationError(token_line, "Comentário de bloco não encerrado.")
            continue
        unicode_string = char in "Nn" and pair[1:] == "'"
        if unicode_string or char in "'\"[":
            quote = "'" if unicode_string else char
            closing = "]" if quote == "[" else quote
            index += 2 if unicode_string else 1
            while index < len(text):
                if text[index] == closing:
                    if text[index:index + 2] == closing * 2:
                        index += 2
                    else:
                        index += 1
                        break
                else:
                    line += text[index] == "\n"
                    index += 1
            else:
                raise SQLClassificationError(token_line, "Literal ou identificador não encerrado.")
            kind = "STRING" if quote == "'" else "IDENT"
        elif char.isalpha() or char == "_":
            index += 1
            while index < len(text) and (text[index].isalnum() or text[index] in "_$#@"):
                index += 1
            kind = "WORD"
        elif char.isdecimal() or char == "." and index + 1 < len(text) and text[index + 1].isdecimal():
            number = _NUMBER.match(text, index)
            if number is None:
                raise SQLClassificationError(line, "Número fora do subconjunto suportado.")
            index = number.end()
            kind = "NUMBER"
        elif char in "(),.;+-":
            index += 1
            kind = char
        else:
            if ord(char) < 32 or char == "\ufeff":
                raise SQLClassificationError(line, "Token fora do subconjunto suportado.")
            # Símbolos de corpos DDL são opacos. A gramática de dados recusa
            # qualquer símbolo que não faça parte do seu subconjunto explícito.
            index += 1
            kind = "SYMBOL"
        yield _Token(kind, text[start:index], start, index, token_line)


class _Parser:
    def __init__(self, text, first_line):
        self.text = text
        self.tokens = iter(_tokens(text, first_line))
        self.current = next(self.tokens, None)
        self.last_end = 0
        self.first_line = first_line
        self.command_start = None

    def fail(self, reason) -> NoReturn:
        raise SQLClassificationError(self.current.line if self.current else self.first_line + self.text.count("\n"), reason)

    def is_word(self, word):
        return self.current is not None and self.current.kind == "WORD" and self.current.value.upper() == word

    def take(self):
        token = self.current
        if token is None:
            self.fail("Comando incompleto no final do lote.")
        if self.command_start is not None and token.end - self.command_start > MAX_STATEMENT_CHARS:
            self.fail("Limite de tamanho do comando excedido.")
        self.last_end = token.end
        self.current = next(self.tokens, None)
        return token

    def match(self, value):
        if self.current is not None and self.current.kind == value:
            self.take()
            return True
        return False

    def expect(self, value):
        if not self.match(value):
            self.fail("Estrutura de comando incompleta ou não suportada.")

    def word(self, value):
        if not self.is_word(value):
            self.fail("Palavra SQL esperada ausente ou fora do subconjunto.")
        self.take()

    def identifier(self):
        token = self.current
        if token is None or not (token.kind == "IDENT" or token.kind == "WORD" and token.value.upper() not in _RESERVED):
            self.fail("Identificador esperado ou não suportado.")
        if token.kind == "IDENT" and token.value in ('[]', '""'):
            self.fail("Identificador vazio não suportado.")
        self.take()
        if token.kind == "IDENT":
            closing = "]" if token.value[0] == "[" else '"'
            return token.value[1:-1].replace(closing * 2, closing)
        return token.value

    def target(self):
        parts = [self.identifier()]
        while self.match("."):
            if len(parts) == 3:
                self.fail("Alvo com mais de três partes não suportado.")
            parts.append(self.identifier())
        return tuple(parts)

    def delimited_identifier(self):
        if self.current is None or self.current.kind != "IDENT":
            self.fail(_VIEW_INVALID_REASON)
        return self.identifier()

    def scalar(self):
        if self.current is None:
            self.fail("Valor incompleto no final do lote.")
        if self.current.kind in ("+", "-"):
            self.take()
            self.expect("NUMBER")
        elif self.current.kind in ("NUMBER", "STRING") or self.is_word("NULL") or self.is_word("DEFAULT"):
            self.take()
        elif self.is_word("CAST"):
            self.temporal_cast()
        else:
            self.fail("Expressão de valor não suportada.")

    def temporal_cast(self):
        self.word("CAST")
        self.expect("(")
        # Somente strings simples/Unicode. Não chamar scalar() aqui: isso
        # aceitaria funções, outros operandos ou CAST aninhado no futuro.
        self.expect("STRING")
        self.word("AS")
        if not any(self.is_word(word) for word in ("DATETIME2", "DATE", "TIME")):
            self.fail("Tipo não autorizado para CAST temporal.")
        is_date = self.is_word("DATE")
        self.take()
        if self.match("("):
            if is_date:
                self.fail("DATE não aceita precisão no CAST temporal.")
            token = self.current
            if token is None or token.kind != "NUMBER" or token.value not in ("0", "1", "2", "3", "4", "5", "6", "7"):
                self.fail("Precisão de CAST temporal deve ser um algarismo de 0 a 7.")
            self.take()
            self.expect(")")
        self.expect(")")

    def insert(self):
        self.word("INSERT")
        if self.is_word("INTO"):
            self.take()
        target = self.target()
        columns = None
        if self.match("("):
            columns = 1
            self.identifier()
            while self.match(","):
                self.identifier()
                columns += 1
            self.expect(")")
        self.word("VALUES")
        row_width = None
        while True:
            self.expect("(")
            width = 1
            self.scalar()
            while self.match(","):
                self.scalar()
                width += 1
            self.expect(")")
            if columns is not None and columns != width or row_width is not None and row_width != width:
                self.fail("Quantidade de valores incompatível com as colunas ou linhas.")
            row_width = width
            if not self.match(","):
                break
        return target

    def identity(self):
        self.word("IDENTITY_INSERT")
        target = self.target()
        if not (self.is_word("ON") or self.is_word("OFF")):
            self.fail("Estado ON ou OFF esperado para IDENTITY_INSERT.")
        self.take()
        return target

    def session_setting(self):
        token = self.current
        if token is None or token.kind != "WORD" or token.value.upper() not in _SESSION_OPTIONS:
            self.fail("Configuração SET fora das opções de sessão autorizadas.")
        option = self.take().value.upper()
        if not (self.is_word("ON") or self.is_word("OFF")):
            self.fail("Estado ON ou OFF esperado para configuração de sessão.")
        if self.is_word("OFF"):
            self.fail("Configuração de sessão OFF sem garantia semântica nesta fase.")
        self.take()
        return option

    def symbol(self, value):
        if self.current is None or self.current.kind != "SYMBOL" or self.current.value != value:
            self.fail("Símbolo esperado ausente ou fora do subconjunto.")
        return self.take()

    def string_value(self):
        token = self.current
        self.expect("STRING")
        value = token.value
        if value[0] in "Nn":
            value = value[1:]
        return value[1:-1].replace("''", "'")

    def database_integer(self):
        token = self.current
        if (token is None or token.kind != "NUMBER" or not token.value.isascii()
                or not token.value.isdigit() or len(token.value) > 20
                or int(token.value) == 0):
            self.fail(_DATABASE_INVALID_REASON)
        self.take()
        return int(token.value)

    def database_string_kind(self):
        token = self.current
        self.expect("STRING")
        return "UNICODE" if token.value[0] in "Nn" else "PLAIN"

    def database_file(self, role, *, unlimited):
        self.expect("(")
        self.word("NAME")
        self.symbol("=")
        name_kind = self.database_string_kind()
        self.expect(",")
        self.word("FILENAME")
        self.symbol("=")
        filename_kind = self.database_string_kind()
        self.expect(",")
        self.word("SIZE")
        self.symbol("=")
        size = self.database_integer()
        self.word("KB")
        self.expect(",")
        self.word("MAXSIZE")
        self.symbol("=")
        if unlimited:
            self.word("UNLIMITED")
            max_size = None
            max_size_unit = "UNLIMITED"
        else:
            max_size = self.database_integer()
            self.word("GB")
            max_size_unit = "GB"
        self.expect(",")
        self.word("FILEGROWTH")
        self.symbol("=")
        growth = self.database_integer()
        self.word("KB")
        self.expect(")")
        return DatabaseFileProfile(role, name_kind, filename_kind, size, "KB",
                                   max_size, max_size_unit, growth, "KB")

    def database_definition(self, create, line):
        self.word("DATABASE")
        target = self.identifier()
        if target.upper() == "CURRENT":
            self.fail(_DATABASE_INVALID_REASON)
        if create:
            self.word("CONTAINMENT")
            self.symbol("=")
            self.word("NONE")
            self.word("ON")
            self.word("PRIMARY")
            primary = self.database_file("PRIMARY", unlimited=True)
            self.word("LOG")
            self.word("ON")
            log = self.database_file("LOG", unlimited=False)
            self.word("WITH")
            self.word("CATALOG_COLLATION")
            self.symbol("=")
            self.word("DATABASE_DEFAULT")
            definition = signature_by_id("db.create.observed_v1")
            profile = CreateDatabaseProfile("NONE", primary, log,
                                            "DATABASE_DEFAULT")
        else:
            self.word("SET")
            token = self.current
            if token is None or token.kind != "WORD":
                self.fail(_DATABASE_INVALID_REASON)
            option = self.take().value.upper()
            if option in _DATABASE_SIMPLE_OPTIONS:
                self.word(_DATABASE_SIMPLE_OPTIONS[option])
            elif option in _DATABASE_EQUALS_OPTIONS:
                self.symbol("=")
                self.word(_DATABASE_EQUALS_OPTIONS[option])
            elif option in _DATABASE_FLAG_OPTIONS:
                pass
            elif option == "COMPATIBILITY_LEVEL":
                self.symbol("=")
                token = self.current
                if token is None or token.kind != "NUMBER" or token.value != "150":
                    self.fail(_DATABASE_INVALID_REASON)
                self.take()
            elif option == "TARGET_RECOVERY_TIME":
                self.symbol("=")
                token = self.current
                if token is None or token.kind != "NUMBER" or token.value != "60":
                    self.fail(_DATABASE_INVALID_REASON)
                self.take()
                self.word("SECONDS")
            elif option == "FILESTREAM":
                self.expect("(")
                self.word("NON_TRANSACTED_ACCESS")
                self.symbol("=")
                self.word("OFF")
                self.expect(")")
            else:
                self.fail(_DATABASE_INVALID_REASON)
            definition = (signature_by_id("db.set.filestream_non_transacted_access.off")
                          if option == "FILESTREAM" else signature_by_option(option))
            profile = None
        self.match(";")
        if self.current:
            self.fail(_DATABASE_INVALID_REASON)
        return _DatabaseInstruction(definition.signature_id, definition.family,
                                    target, line, definition.option,
                                    definition.requested_value, profile)

    def index_definition(self):
        if self.is_word("UNIQUE"):
            self.take()
        if self.is_word("CLUSTERED") or self.is_word("NONCLUSTERED"):
            self.take()
        self.word("INDEX")
        self.identifier()
        self.word("ON")
        self.target()
        self.expect("(")
        while True:
            self.identifier()
            if self.is_word("ASC") or self.is_word("DESC"):
                self.take()
            if not self.match(","):
                break
        self.expect(")")
        if self.is_word("WITH"):
            self.take()
            self.expect("(")
            seen = set()  # No máximo as dez opções explicitamente permitidas.
            while True:
                token = self.current
                if token is None or token.kind != "WORD":
                    self.fail("Opção de índice não reconhecida.")
                option = token.value.upper()
                if option not in _INDEX_BOOL_OPTIONS | _INDEX_OFF_OPTIONS | {"FILLFACTOR"} or option in seen:
                    self.fail("Opção de índice desconhecida ou repetida.")
                seen.add(option)
                self.take()
                self.symbol("=")
                if option == "FILLFACTOR":
                    token = self.current
                    if (token is None or token.kind != "NUMBER" or not token.value.isascii()
                            or not token.value.isdigit() or len(token.value) > 3 or int(token.value) > 100):
                        self.fail("FILLFACTOR fora do intervalo inteiro suportado.")
                elif not (self.is_word("ON") or self.is_word("OFF")) or option in _INDEX_OFF_OPTIONS and not self.is_word("OFF"):
                    self.fail("Estado de opção de índice não autorizado.")
                self.take()
                if not self.match(","):
                    break
            self.expect(")")
        if self.is_word("ON"):
            self.take()
            self.identifier()

    def extended_property(self):
        self.take()  # EXEC/EXECUTE, somente no início de um lote exclusivo.
        if self.target() != ("sys", "sp_addextendedproperty"):
            self.fail("EXEC fora do metadado descritivo autorizado.")
        params: dict[str, str | None] = {}
        # Conjunto fechado de seis ou oito parâmetros literais.
        numeric_value = False  # Tipo preservado mesmo se @value vier antes de @name.
        while True:
            at = self.symbol("@")
            token = self.current
            if (token is None or token.kind != "WORD" or token.start != at.end
                    or token.value not in _PROPERTY_PARAMS or token.value in params):
                self.fail("Parâmetro de metadado desconhecido, dividido ou repetido.")
            name = self.take().value
            self.symbol("=")
            if name == "value" and self.is_word("NULL"):
                self.take()
                params[name] = None
            elif name == "value" and self.current is not None and self.current.kind == "NUMBER":
                if len(self.current.value) != 1 or self.current.value not in "0123456789":
                    self.fail("Contagem de designer exige um único algarismo ASCII.")
                params[name] = self.take().value
                numeric_value = True
            else:
                params[name] = self.string_value()
            if not self.match(","):
                break
        required = {"name", "value", "level0type", "level0name", "level1type", "level1name"}
        if set(params) not in (required, _PROPERTY_PARAMS):
            self.fail("Escopo de metadado incompleto ou não suportado.")
        description = (params["name"] == "MS_Description" and not numeric_value
                       and params["level1type"] == "TABLE"
                       and ("level2type" not in params or params["level2type"] == "COLUMN"))
        layout = (params["name"] in _DESIGNER_LAYOUT_PROPERTIES
                  and not numeric_value and params["value"] is not None)
        count = params["name"] == "MS_DiagramPaneCount" and numeric_value
        designer = (layout or count) and set(params) == required and params["level1type"] == "VIEW"
        if (not (description or designer) or params["level0type"] != "SCHEMA"
                or any(not cast(str, params[name]).strip()
                       for name in ("level0name", "level1name", "level2name")
                       if name in params)):
            self.fail("Propriedade ou escopo fora do metadado autorizado.")
        self.match(";")
        if self.current:
            self.fail("Metadado misturado ou múltiplo no mesmo lote.")

    def view_query(self, *, top_selects):
        depth = 0
        expect_set_select = False
        forbidden = {
            "ALTER", "BACKUP", "BEGIN", "BULK", "COMMIT", "CREATE",
            "DBCC", "DEALLOCATE", "DELETE", "DENY", "DROP", "EXEC",
            "EXECUTE", "FETCH", "GRANT", "IF", "INSERT", "KILL",
            "GO", "INTO", "MERGE", "OPEN", "PRINT", "RAISERROR", "RECONFIGURE",
            "RESTORE", "RETURN", "REVOKE", "ROLLBACK", "SET",
            "SHUTDOWN", "THROW", "TRUNCATE", "UPDATE", "USE",
            "WAITFOR", "WHILE",
        }
        set_operators = {"EXCEPT", "INTERSECT", "UNION"}
        while self.current:
            token = self.current
            word = token.value.upper() if token.kind == "WORD" else None
            if token.kind == "(":
                depth += 1
                if depth > MAX_DDL_DEPTH:
                    self.fail("Limite de profundidade estrutural do DDL excedido.")
            elif token.kind == ")":
                depth -= 1
                if depth < 0:
                    self.fail(_VIEW_INVALID_REASON)
            elif token.kind == ";":
                if depth or expect_set_select:
                    self.fail(_VIEW_INVALID_REASON)
                self.take()
                if self.current is not None:
                    self.fail(_VIEW_INVALID_REASON)
                break
            elif word in forbidden:
                self.fail(_VIEW_INVALID_REASON)
            elif depth == 0:
                if expect_set_select:
                    if word in {"ALL", "DISTINCT"}:
                        self.take()
                        continue
                    if word != "SELECT":
                        self.fail(_VIEW_INVALID_REASON)
                    expect_set_select = False
                    top_selects += 1
                elif word in set_operators:
                    expect_set_select = True
                elif word == "SELECT":
                    # Subconsultas ficam entre parênteses. Um SELECT top-level
                    # adicional só é válido após operador de conjunto.
                    self.fail(_VIEW_INVALID_REASON)
            self.take()
        if depth or expect_set_select or not top_selects:
            self.fail(_VIEW_INVALID_REASON)

    def view_ctes(self):
        # Somente o envelope CTE observado: nome, AS, consulta parentizada;
        # não interpretar as expressões SELECT internas.
        while True:
            self.identifier()
            self.word("AS")
            self.expect("(")
            if not self.is_word("SELECT"):
                self.fail(_VIEW_INVALID_REASON)
            self.take()
            depth = 1
            forbidden = {
                "ALTER", "CREATE", "DELETE", "DROP", "EXEC", "EXECUTE",
                "GO", "INSERT", "INTO", "MERGE", "SET", "TRUNCATE", "UPDATE", "USE",
            }
            while self.current and depth:
                token = self.current
                if token.kind == "(":
                    depth += 1
                    if depth > MAX_DDL_DEPTH:
                        self.fail("Limite de profundidade estrutural do DDL excedido.")
                elif token.kind == ")":
                    depth -= 1
                elif token.kind == ";" or token.kind == "WORD" and token.value.upper() in forbidden:
                    self.fail(_VIEW_INVALID_REASON)
                self.take()
            if depth:
                self.fail(_VIEW_INVALID_REASON)
            if not self.match(","):
                break
        if not self.is_word("SELECT"):
            self.fail(_VIEW_INVALID_REASON)
        self.take()

    def view_definition(self, line):
        self.word("VIEW")
        self.delimited_identifier()
        self.expect(".")
        self.delimited_identifier()
        if not self.is_word("AS"):
            self.fail(_VIEW_INVALID_REASON)
        self.take()
        if self.is_word("SELECT"):
            body_kind = "SELECT"
            self.take()
        elif self.is_word("WITH"):
            body_kind = "WITH"
            self.take()
            self.view_ctes()
        else:
            self.fail(_VIEW_INVALID_REASON)
        self.view_query(top_selects=1)
        return _ViewDefinition(line, body_kind)

    def ddl(self):
        prefixed = self.current.kind == "SYMBOL" and self.current.value == "\u200b"
        if prefixed:
            prefix = self.take()
            if (self.current is not None and self.current.kind == "SYMBOL"
                    and self.current.value == "\u200b"
                    and self.text[prefix.end:self.current.start] in ("", "\n", "\r\n")):
                # Par contíguo ou em linhas consecutivas, como observado na R5A.
                self.take()  # Nunca normalizar nem consumir um terceiro U+200B.
            if not self.is_word("CREATE"):
                self.fail("U+200B permitido somente antes de CREATE PROCEDURE.")
        create = self.is_word("CREATE")
        verb = self.take()
        if prefixed and not (self.is_word("PROC") or self.is_word("PROCEDURE")):
            self.fail("U+200B permitido somente antes de CREATE PROCEDURE.")
        if not prefixed and self.is_word("DATABASE"):
            try:
                return self.database_definition(create, verb.line)
            except SQLClassificationError as error:
                if error.reason.startswith("Limite de "):
                    raise
                raise SQLClassificationError(error.line, _DATABASE_INVALID_REASON) from None
        if create and self.is_word("OR"):
            self.take()
            self.word("ALTER")
            if self.is_word("VIEW"):
                self.fail(_VIEW_INVALID_REASON)
            if not any(self.is_word(word) for word in ("PROCEDURE", "PROC", "TRIGGER", "FUNCTION")):
                self.fail("CREATE OR ALTER fora das rotinas suportadas.")
        if not prefixed and self.is_word("VIEW"):
            if not create:
                self.fail(_VIEW_INVALID_REASON)
            self.command_start = verb.start
            try:
                return self.view_definition(verb.line)
            except SQLClassificationError as error:
                if error.reason.startswith("Limite de "):
                    raise
                raise SQLClassificationError(error.line, _VIEW_INVALID_REASON) from None
        if create and any(self.is_word(word) for word in ("UNIQUE", "CLUSTERED", "NONCLUSTERED", "INDEX")):
            self.index_definition()
            self.match(";")
            if self.current:
                self.fail("Índice misturado ou múltiplo no mesmo lote.")
            return
        if any(self.is_word(word) for word in ("PROCEDURE", "PROC", "TRIGGER", "FUNCTION")):
            routine_kind = self.take().value.upper()
            self.target()
            depth = 0
            forbidden = {"INSERT", "UPDATE", "DELETE", "MERGE", "SELECT", "EXEC", "EXECUTE", "CREATE", "ALTER", "SET", "USE", "BEGIN", "END", "RETURN"}
            while self.current and not (self.is_word("AS") and depth == 0):
                if self.current.kind == "SYMBOL" and self.current.value == "\u200b":
                    self.fail("U+200B inesperado no cabeçalho da rotina.")
                if self.current.kind == "WORD" and self.current.value.upper() in forbidden:
                    if routine_kind == "TRIGGER" and depth == 0 and self.current.value.upper() in {"INSERT", "UPDATE", "DELETE"}:
                        # Eventos do trigger são palavras SQL no cabeçalho,
                        # mas não podem ser seguidos de alvo ou cláusula INTO.
                        self.take()
                        if self.current is None or not (self.current.kind == "," or self.is_word("AS")):
                            self.fail("Cabeçalho de trigger ambíguo ou não suportado.")
                        continue
                    self.fail("Instrução de dados ou controle antes do corpo da rotina.")
                if self.current.kind == "(":
                    depth += 1
                elif self.current.kind == ")":
                    depth -= 1
                if not 0 <= depth <= MAX_DDL_DEPTH:
                    self.fail("Cabeçalho de rotina incompleto ou profundo demais.")
                self.take()
            if self.current is None:
                self.fail("Definição de rotina sem corpo AS reconhecível.")
            self.word("AS")
            if self.current is None:
                self.fail("Corpo de rotina ausente.")
            while self.current:
                self.take()
            return  # O corpo inteiro pertence à definição até GO/EOF.
        self.word("TABLE")
        self.target()
        if create:
            self.expect("(")
            depth = 1
            has_definition = False
            while self.current and depth:
                if self.current.kind == ";":
                    self.fail("DDL misturado com outra instrução no mesmo lote.")
                if (self.current.kind == "WORD"
                        and self.current.value.upper() in _DDL_STATEMENT_STARTERS):
                    self.fail("DDL misturado com outra instrução no mesmo lote.")
                if self.current.kind == "(":
                    depth += 1
                    if depth > MAX_DDL_DEPTH:
                        self.fail("Limite de profundidade estrutural do DDL excedido.")
                elif self.current.kind == ")":
                    depth -= 1
                else:
                    has_definition = True
                self.take()
            if depth:
                self.fail("Definição de tabela incompleta.")
            if not has_definition:
                self.fail("Definição de tabela vazia.")
            if self.is_word("ON"):
                self.take()
                self.identifier()
            if self.is_word("TEXTIMAGE_ON"):
                self.take()
                self.identifier()
        else:
            if not any(self.is_word(word) for word in ("ADD", "ALTER", "DROP", "WITH", "CHECK", "NOCHECK", "ENABLE", "DISABLE")):
                self.fail("ALTER TABLE fora do subconjunto ignorável.")
            action = self.take().value.upper()
            if self.current is None:
                self.fail("ALTER TABLE incompleto.")
            if action == "DROP":
                if not (self.is_word("COLUMN") or self.is_word("CONSTRAINT")):
                    self.fail("ALTER TABLE DROP fora das formas suportadas.")
                self.take()
                self.identifier()
            depth = 0
            while self.current and self.current.kind != ";":
                if (self.current.kind == "WORD"
                        and self.current.value.upper() in _DDL_STATEMENT_STARTERS):
                    self.fail("DDL misturado com outra instrução no mesmo lote.")
                if self.current.kind == "(":
                    depth += 1
                elif self.current.kind == ")":
                    depth -= 1
                if not 0 <= depth <= MAX_DDL_DEPTH:
                    self.fail("Estrutura ou profundidade de DDL não suportada.")
                self.take()
            if depth:
                self.fail("Definição de tabela incompleta.")
        self.match(";")
        if self.current:
            self.fail("DDL misturado ou múltiplo no mesmo lote.")


def _commands(text, first_line, *, authorize=None, authorize_session=None,
              validate_database=None, validate_view=None):
    parser = _Parser(text, first_line)
    if parser.current is None:
        return
    if parser.is_word("EXEC") or parser.is_word("EXECUTE"):
        parser.command_start = parser.current.start
        parser.extended_property()
        return
    if (parser.is_word("CREATE") or parser.is_word("ALTER")
            or parser.current.kind == "SYMBOL" and parser.current.value == "\u200b"):
        instruction = parser.ddl()
        if isinstance(instruction, _DatabaseInstruction):
            if validate_database is not None:
                validate_database(instruction)
            raise SQLClassificationError(instruction.line, _DATABASE_RECOGNIZED_REASON)
        if isinstance(instruction, _ViewDefinition):
            if validate_view is not None:
                validate_view(instruction)
            raise SQLClassificationError(instruction.line, _VIEW_RECOGNIZED_REASON)
        return
    while parser.current:
        start = parser.current.start
        line = parser.current.line
        parser.command_start = start
        if parser.is_word("INSERT"):
            kind = "INSERT"
            target = parser.insert()
        elif parser.is_word("SET"):
            parser.take()
            if parser.is_word("IDENTITY_INSERT"):
                kind = "IDENTITY_INSERT"
                target = parser.identity()
            else:
                kind = "SESSION_SET"
                option = parser.session_setting()
        else:
            parser.fail("Instrução não suportada em lote de importação.")
        parser.match(";")
        if kind == "SESSION_SET":
            if authorize_session is not None:
                authorize_session(option, line)
        elif authorize is not None:
            authorize(target, line)
        yield SQLCommand(text[start:parser.last_end], line, kind)


class SQLCommandReader:
    """Lê texto SQL incrementalmente e emite apenas comandos autorizados.

    Cada lote delimitado por ``GO`` é validado por inteiro antes da primeira
    emissão. O leitor conserva somente o lote corrente, registra solicitações
    de sessão e invalida o contexto após qualquer exceção ou interrupção.

    Args:
        file: Stream textual que fornece ``readline()`` incremental.
        target_database: Banco lógico usado para validar ``USE`` e alvos.
        on_line: Callback opcional chamado após cada linha física lida.

    Raises:
        SQLClassificationError: Se o contexto, os limites ou o SQL estiverem
            fora do subconjunto conservador.
    """

    def __init__(
            self,
            file: TextIO,
            *,
            target_database: str | None = None,
            on_line: Callable[[int], None] | None = None,
    ) -> None:
        self.file = file
        self.on_line = on_line
        self.line_number = 0
        self.ignored_lines = 0
        # Registro limitado das solicitações emitidas, não do estado efetivo
        # do servidor/driver. A pré-validação não altera este registro.
        self._session_requests = dict.fromkeys(_SESSION_OPTIONS)
        self._target_database = target_database
        # O nome vem da mesma configuração ODBC; não aceitar parâmetros
        # adicionais ou nomes que exigiriam alterar o escape da conexão.
        valid = (isinstance(target_database, str) and bool(target_database.strip())
                 and not any(ord(char) < 32 or char in ";{}" for char in target_database))
        self._context_state = "implicit" if valid else "unknown"

    @property
    def context_state(self) -> str:
        """Retorna o estado lógico do banco sem consultar servidor externo."""
        return self._context_state

    @property
    def session_requests(self) -> dict[str, str | None]:
        """Retorna uma cópia das opções de sessão solicitadas e reconhecidas."""
        return self._session_requests.copy()

    def _authorize_session(self, option: str, line: int) -> None:
        if self._context_state not in ("implicit", "target"):
            raise SQLClassificationError(line, "Configuração de sessão fora do contexto de destino autorizado.")

    def _record_session(self, option: str, line: int) -> None:
        self._authorize_session(option, line)
        self._session_requests[option] = "ON"

    def _authorize_target(self, target: tuple[str, ...], line: int) -> None:
        if self._context_state not in ("implicit", "target"):
            raise SQLClassificationError(line, "Operação de dados fora do contexto de destino autorizado.")
        if len(target) == 3 and target[0] != self._target_database:
            raise SQLClassificationError(line, "Qualificador de banco diferente do destino autorizado.")

    def _validate_database_target(
            self,
            instruction: _DatabaseInstruction,
    ) -> None:
        # O alvo é explícito e comparado como o identificador Unicode já
        # decodificado. O contexto USE não autoriza nem bloqueia esta análise.
        if instruction.target != self._target_database:
            raise SQLClassificationError(instruction.line,
                                         _DATABASE_WRONG_TARGET_REASON)

    def _validate_view_context(self, definition: _ViewDefinition) -> None:
        if self._context_state not in ("implicit", "target"):
            raise SQLClassificationError(definition.line,
                                         _VIEW_WRONG_CONTEXT_REASON)

    def _use(self, text: str, first_line: int) -> bool:
        parser = _Parser(text, first_line)
        if not parser.is_word("USE"):
            return False
        parser.command_start = parser.current.start
        parser.take()
        database = parser.identifier()
        parser.match(";")
        if parser.current is not None:
            parser.fail("USE exige um único identificador em lote exclusivo.")
        self._context_state = "target" if database == self._target_database else "external"
        return True

    def _batches(self) -> Iterator[tuple[str, int, int]]:
        context = _LineContext()
        buffer = io.StringIO()
        size = 0
        start_line = 1
        code_lines = 0
        while True:
            line = self.file.readline(MAX_LINE_CHARS + 1)
            if not line:
                if size:
                    yield buffer.getvalue(), start_line, code_lines
                return
            self.line_number += 1
            if self.on_line:
                self.on_line(self.line_number)
            if len(line) > MAX_LINE_CHARS:
                raise SQLClassificationError(self.line_number, "Limite de tamanho da linha excedido.")
            visible = context.scan(line, self.line_number)
            if visible.upper() == "GO" and not context.quote and not context.depth:
                self.ignored_lines += 1
                batch = buffer.getvalue()
                buffer = io.StringIO()
                old_start, old_code = start_line, code_lines
                size = code_lines = 0
                start_line = self.line_number + 1
                yield batch, old_start, old_code
                continue
            if not visible:
                self.ignored_lines += 1
            else:
                code_lines += 1
            size += len(line)
            if size > MAX_BATCH_CHARS:
                raise SQLClassificationError(self.line_number, "Limite de tamanho do lote excedido.")
            buffer.write(line)

    def __iter__(self) -> Iterator[SQLCommand]:
        """Emite comandos na ordem de origem após validar o lote completo.

        Uma falha ou interrupção invalida permanentemente o contexto desta
        instância. Nenhum comando do lote corrente é emitido antes de sua
        primeira passagem integral de validação.
        """
        try:
            if self._context_state in ("unknown", "invalid"):
                raise SQLClassificationError(self.line_number or 1, "Destino ausente, inválido ou leitor sem contexto confiável.")
            for text, first_line, code_lines in self._batches():
                if self._use(text, first_line):
                    self.ignored_lines += code_lines
                    continue
                has_commands = False
                # Primeira passagem: validar sintaxe, contexto e TODOS os
                # qualificadores antes de emitir qualquer comando deste lote.
                for command in _commands(text, first_line, authorize=self._authorize_target,
                                         authorize_session=self._authorize_session,
                                         validate_database=self._validate_database_target,
                                         validate_view=self._validate_view_context):
                    has_commands = True
                if not has_commands:
                    self.ignored_lines += code_lines
                    continue
                yield from _commands(text, first_line, authorize=self._authorize_target,
                                     authorize_session=self._record_session,
                                     validate_database=self._validate_database_target,
                                     validate_view=self._validate_view_context)
        except (Exception, KeyboardInterrupt):
            self._context_state = "invalid"
            raise
