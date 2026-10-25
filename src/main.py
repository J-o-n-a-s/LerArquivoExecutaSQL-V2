"""Importador interativo e incremental de comandos SQL para SQL Server.

A configuração sensível vem do ambiente, enquanto o caminho do arquivo é
informado pelo usuário. O módulo preserva mensagens públicas sanitizadas e
confirma cada comando separadamente.
"""

import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import pyodbc
from sql_classifier import SQLClassificationError, SQLCommandReader


_CHAVE_CONFIRMACAO_POR_TIPO = {
    'INSERT': 'inserts_confirmados',
    'IDENTITY_INSERT': 'identity_insert_confirmados',
    'SESSION_SET': 'session_set_confirmados',
}
_ENV_SERVER = 'LER_SQL_SERVER'
_ENV_DATABASE = 'LER_SQL_DATABASE'
_ENV_DRIVER = 'LER_SQL_DRIVER'


class _SQLCursor(Protocol):
    """Operações de cursor usadas pelo fluxo de importação."""

    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _SQLConnection(Protocol):
    """Operações de conexão exigidas sem acoplar a tipagem ao driver."""

    def cursor(self) -> _SQLCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class ConfigurationError(ValueError):
    """Configuração ausente ou insegura, sem incorporar seu conteúdo."""


@dataclass(frozen=True)
class ImportConfiguration:
    """Configuração validada necessária para uma execução do importador."""

    server: str
    database: str
    driver: str
    file_path: str


def _is_control_character(char: str) -> bool:
    codepoint = ord(char)
    return codepoint < 0x20 or 0x7f <= codepoint <= 0x9f


def _validate_connection_value(value: object) -> str:
    """Valida um campo de conexão sem registrar seu conteúdo."""
    valid = (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 512
        and not any(_is_control_character(char) or char in ';{}' for char in value)
    )
    if not valid:
        raise ConfigurationError('Configuração obrigatória ausente ou inválida.')
    return cast(str, value)


def _validate_file_path(value: object) -> str:
    """Valida o formato básico do caminho sem acessar o arquivo."""
    valid = (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 32_767
        and not any(_is_control_character(char) for char in value)
    )
    if not valid:
        raise ConfigurationError('Caminho obrigatório ausente ou inválido.')
    return cast(str, value)


def _load_configuration(
        file_path: object,
        environment: Mapping[str, object] | None = None,
) -> ImportConfiguration:
    """Carrega e valida a configuração, propagando ``ConfigurationError``."""
    environment = os.environ if environment is None else environment
    return ImportConfiguration(
        server=_validate_connection_value(environment.get(_ENV_SERVER)),
        database=_validate_connection_value(environment.get(_ENV_DATABASE)),
        driver=_validate_connection_value(environment.get(_ENV_DRIVER)),
        file_path=_validate_file_path(file_path),
    )


def _build_connection_string(configuration: ImportConfiguration) -> str:
    """Monta a string ODBC somente a partir de valores já validados."""
    return (
        'Driver={' + configuration.driver + '};'
        'Server=' + configuration.server + ';'
        'Database=' + configuration.database + ';'
        'Trusted_Connection=yes;'
    )


def _conectar_sql(
        connection_string: str,
) -> tuple[_SQLConnection | None, bool]:
    """Tenta abrir a conexão e retorna ``(conexão, houve_erro)``.

    Falhas do driver são convertidas em mensagem pública sanitizada; a
    exceção original e a string de conexão nunca são exibidas.
    """
    ret_erro = False
    connection = None
    try:
        print('\nAguarde a conexão com o banco de dados SQL Server.')
        connection = pyodbc.connect(connection_string)
    except pyodbc.Error as ex:
        ret_erro = True
        print('\nNão foi possível conectar ao banco de dados SQL Server.')
        _log_error('conexão', ex)
    return connection, ret_erro


def _log_error(exception: str, p_erro: BaseException) -> None:
    """Exibe apenas a etapa e o tipo da falha, sem conteúdo privado."""
    # Mensagens do driver podem conter credenciais e o texto SQL completo.
    print(f'Falha em {exception}: {type(p_erro).__name__}.')


def _imprimir_estatisticas(estatisticas: Mapping[str, int]) -> None:
    """Imprime os contadores públicos acumulados pela importação."""
    print(f'Linhas físicas lidas: {estatisticas["linhas_fisicas_lidas"]}.')
    print(f'Linhas ignoradas pelo leitor: {estatisticas["linhas_ignoradas"]}.')
    print(f'Comandos emitidos pelo leitor: {estatisticas["comandos_emitidos"]}.')
    print(f'Execuções tentadas: {estatisticas["execucoes_tentadas"]}.')
    print(f'Execuções com retorno: {estatisticas["execucoes_retornadas"]}.')
    print(f'Commits tentados: {estatisticas["commits_tentados"]}.')
    print(f'Commits com retorno: {estatisticas["commits_retornados"]}.')
    print(f'Comandos confirmados: {estatisticas["comandos_confirmados"]}.')
    print(f'INSERT confirmados: {estatisticas["inserts_confirmados"]}.')
    print(f'IDENTITY_INSERT confirmados: {estatisticas["identity_insert_confirmados"]}.')
    print(f'SESSION_SET confirmados: {estatisticas["session_set_confirmados"]}.')
    print(f'Falhas de execução: {estatisticas["falhas_execucao"]}.')
    print('Confirmações com resultado desconhecido: '
          f'{estatisticas["confirmacoes_desconhecidas"]}.')


if __name__ == '__main__':
    # Variáveis internas do projeto
    num_linhas = 0
    # Alias legado mantido apenas para compatibilidade dos harnesses históricos.
    # Seu valor é quantidade de comandos confirmados, nunca linhas ou registros.
    linhas_inseridas = 0
    linhas_em_branco = 0
    estatisticas = {
        'linhas_fisicas_lidas': 0,
        'linhas_ignoradas': 0,
        'comandos_emitidos': 0,
        'execucoes_tentadas': 0,
        'execucoes_retornadas': 0,
        'commits_tentados': 0,
        'commits_retornados': 0,
        'comandos_confirmados': 0,
        'inserts_confirmados': 0,
        'identity_insert_confirmados': 0,
        'session_set_confirmados': 0,
        'falhas_execucao': 0,
        'confirmacoes_desconhecidas': 0,
    }
    conn = None
    cursor = None
    file = None
    codigo_saida = 0
    estado_comando = None
    processamento_concluido = False
    leitor_sql = None
    linha_comando = None
    database = None
    etapa = 'interação'
    try:
        print('+------------------------------------------------------------------------------------------------------+')
        print('| PROGRAMA PARA REALIZAR A LEITURA DE ARQUIVOS SQL COM O INTUITO DE EXECUTAR OS COMANDOS NO SQL SERVER |')
        print('+------------------------------------------------------------------------------------------------------+')
        caminho_arquivo_sql = input('\nDigite o caminho completo, com nome e extensão, do arquivo que deseja ler:\n-> ')
        while True:
            log = input('\nDeseja ver o log de execução?\n'
                        'S - Sim;\n'
                        'N - Não;\n'
                        '-> ')
            if (log.lower() == 's') or (log.lower() == 'n'):
                break
            else:
                print('\nPor gentileza digite uma opção válida.')
        while True:
            info = input('\nPor gentileza digite a opção desejada entre as opções:\n'
                         '1 - Iniciar;\n'
                         '2 - Finalizar;\n'
                         '-> ')
            if info == '2':
                break
            elif info == '1':
                # Captura a hora do início da execução para medir o tempo total.
                start_t = time.time()
                etapa = 'validação da configuração'
                configuracao = _load_configuration(caminho_arquivo_sql)
                database = configuracao.database
                etapa = 'abertura do arquivo'
                print('\nTentando ler o arquivo informado.')
                file = open(configuracao.file_path, 'r', encoding='utf-16')
                etapa = 'conexão'
                conn, x_erro = _conectar_sql(_build_connection_string(configuracao))
                if x_erro:
                    codigo_saida = 1
                    break
                print('\nBanco de dados conectado com sucesso.\n')
                etapa = 'criação do cursor'
                cursor = conn.cursor()
                etapa = 'leitura do arquivo'
                def registrar_linha(numero: int) -> None:
                    """Atualiza a linha física observada pelo leitor incremental."""
                    global num_linhas
                    num_linhas = numero
                    estatisticas['linhas_fisicas_lidas'] = numero
                    if log.lower() == 's':
                        print(f'Executando a linha -> {num_linhas}.')

                leitor_sql = SQLCommandReader(file, target_database=database, on_line=registrar_linha)
                for comando in leitor_sql:
                    estatisticas['comandos_emitidos'] += 1
                    linha_comando = comando.line
                    etapa = 'execução do comando'
                    estado_comando = 'executando'
                    estatisticas['execucoes_tentadas'] += 1
                    cursor.execute(comando.text)
                    estatisticas['execucoes_retornadas'] += 1
                    etapa = 'confirmação do comando'
                    estado_comando = 'confirmando'
                    estatisticas['commits_tentados'] += 1
                    conn.commit()
                    estatisticas['commits_retornados'] += 1
                    estatisticas['comandos_confirmados'] += 1
                    estatisticas[_CHAVE_CONFIRMACAO_POR_TIPO[comando.kind]] += 1
                    linhas_inseridas += 1
                    estado_comando = 'confirmado'
                    etapa = 'leitura do arquivo'
                processamento_concluido = True
                break
            else:
                print('\nValor digitado é inválido.')
    except (Exception, KeyboardInterrupt) as erro:
        codigo_saida = 130 if isinstance(erro, KeyboardInterrupt) else 1
        _log_error(etapa, erro)
        if isinstance(erro, SQLClassificationError):
            print(f'Classificação SQL recusada na linha {erro.line}: {erro.reason}')
        print(f'Última linha lida: {num_linhas}.')
        if isinstance(erro, KeyboardInterrupt):
            print('\nImportação interrompida.')
        if estado_comando in ('executando', 'confirmando'):
            if estado_comando == 'executando':
                estatisticas['falhas_execucao'] += 1
            # Um commit que lançou erro pode já ter sido aplicado no servidor.
            # SET de sessão pode persistir apesar de rollback transacional.
            comando_sessao = comando.kind == 'SESSION_SET'
            confirmacao_desconhecida = estado_comando == 'confirmando' or comando_sessao
            try:
                conn.rollback()
            except (Exception, KeyboardInterrupt) as erro_rollback:
                _log_error('rollback', erro_rollback)
                confirmacao_desconhecida = True
            if comando_sessao:
                print('Estado da configuração de sessão desconhecido; rollback não comprova restauração de SET.')
            if confirmacao_desconhecida:
                estatisticas['confirmacoes_desconhecidas'] += 1
                print(f'Resultado de confirmação desconhecido para o comando da linha {linha_comando}.')
                print('O rollback não comprova se o comando foi confirmado. Não repetir automaticamente.')
            else:
                print(f'Comando da linha {linha_comando} revertido por rollback.')
    finally:
        if leitor_sql is not None:
            linhas_em_branco = leitor_sql.ignored_lines
            estatisticas['linhas_ignoradas'] = leitor_sql.ignored_lines
        # Cada fechamento é independente: uma falha não impede os demais e
        # não substitui o diagnóstico ou código de saída da falha original.
        for recurso, descricao in ((cursor, 'do cursor'), (conn, 'da conexão'), (file, 'do arquivo')):
            if recurso is not None:
                try:
                    recurso.close()
                except (Exception, KeyboardInterrupt) as erro_fechamento:
                    _log_error('encerramento ' + descricao, erro_fechamento)
                    if codigo_saida == 0:
                        codigo_saida = 130 if isinstance(erro_fechamento, KeyboardInterrupt) else 1
    if codigo_saida == 0 and processamento_concluido:
        print()
        _imprimir_estatisticas(estatisticas)
        print(f'Processo finalizado às {time.strftime("%d/%m/%Y %H:%M:%S")}.'
              f'\nTempo total de execução {(time.time() - start_t):.2f} segundos.')
    elif codigo_saida != 0:
        if processamento_concluido:
            print('Processamento concluído; falha no encerramento de recursos.')
        elif linhas_inseridas:
            print('Importação parcialmente concluída; commits anteriores não podem ser desfeitos pelo rollback.')
        else:
            print('Importação não concluída.')
        _imprimir_estatisticas(estatisticas)
    print('\nAté logo!\n')
    sys.exit(codigo_saida)
