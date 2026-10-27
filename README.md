# LerArquivoExecutaSQL V2

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

Bem-vindo! O LerArquivoExecutaSQL V2 é um importador incremental de dados para
SQL Server. Ele lê arquivos SQL em UTF-16, classifica cada lote de acordo com um
subconjunto conservador de T-SQL e encaminha ao banco somente os comandos
explicitamente aceitos.

A V2 também oferece descoberta automática do provável banco e inventário
incremental offline, além da validação estrita que localiza a primeira recusa.
Esses modos não exigem SQL Server, `pyodbc` ou driver ODBC.

## Origem e motivação

O projeto nasceu de um problema concreto: a implementação original precisava
processar um arquivo com mais de 9 GB e mais de 11 milhões de comandos SQL. No
ambiente disponível naquele momento, não foi possível abrir e executar o arquivo
no SQL Server Management Studio por falta de memória. Dividi-lo manualmente em
muitos arquivos também não era uma solução prática.

A resposta foi criar um programa Python que lesse o conteúdo incrementalmente e
automatizasse o processamento. Essa primeira implementação continua disponível
no [repositório da V1](https://github.com/J-o-n-a-s/LerArquivoExecutaSQL).

A V2 é uma evolução direta desse trabalho. Ela mantém a leitura incremental e
acrescenta classificação conservadora, validação integral de cada lote,
tratamento mais preciso de falhas e interrupções, estatísticas com significados
explícitos, proteção de informações privadas, análise offline e uma suíte ampla
de testes automatizados. V1 e V2 permanecem projetos independentes; o histórico
Git antigo não precisa ser importado para o repositório da V2.

## Principais funcionalidades

- leitura incremental de arquivos UTF-16, sem carregar todo o conteúdo em
  memória;
- separação de lotes por `GO`, respeitando strings, identificadores e
  comentários;
- validação completa do lote atual antes de emitir seu primeiro comando;
- suporte restrito a `INSERT ... VALUES`, `IDENTITY_INSERT`, três configurações
  de sessão e formas temporais de `CAST`;
- uso de `USE` apenas para controlar o contexto lógico do classificador;
- reconhecimento conservador de DDL e metadados que podem ser ignorados somente
  quando o banco já foi preparado e verificado externamente;
- recusa de comandos desconhecidos, ambíguos ou misturados;
- execução e confirmação individuais, com indicação de resultados parciais ou
  de confirmação desconhecida;
- estatísticas separadas de leitura, classificação, execução e commit;
- analisador offline com descoberta de banco, inventário incremental e
  validação estrita, independente de conexão com SQL Server.

Consulte [Arquitetura e decisões](docs/ARQUITETURA_E_DECISOES.md) para entender o
porquê do desenho e [Contrato SQL](docs/CONTRATO_SQL.md) para conhecer o
subconjunto aceito em detalhes.

## Requisitos e modos de uso

O Python de referência é o 3.13.17. O projeto declara Python 3.13 ou superior em
`pyproject.toml`; isso não constitui promessa de compatibilidade com toda versão
futura. O Python 3.12.14 passa pela suíte offline atual como compatibilidade
secundária de melhor esforço, mas fica fora do requisito formal do projeto.

### Análise offline

Necessita apenas de:

- Python compatível;
- código do projeto;
- arquivo SQL UTF-16;
- nome lógico do banco de destino somente no modo estrito com `--database`.

A descoberta e o inventário não exigem nome de banco. A descoberta é escolhida
pela ausência de `--database`; o inventário é escolhido por `--inventory`.

Não necessita de SQL Server, `pyodbc`, driver ODBC ou conexão de rede com banco.

### Importação real

Além do código e do arquivo UTF-16, necessita de:

- [`pyodbc`](https://pypi.org/project/pyodbc/);
- um [Microsoft ODBC Driver para SQL Server](https://learn.microsoft.com/pt-br/sql/connect/odbc/download-odbc-driver-for-sql-server)
  compatível com o ambiente;
- banco SQL Server previamente preparado;
- permissões adequadas e autenticação integrada configurada;
- validação externa das precondições de schema, segurança e operação.

`pyodbc` é o pacote Python que fornece a interface ODBC. O Microsoft ODBC Driver
é um componente separado do sistema. Instalar um deles não instala
automaticamente o outro.

## Instalação

As instruções abaixo priorizam Windows e PowerShell, coerentemente com o uso
atual de autenticação integrada.

1. No GitHub, obtenha o código pelo **Download ZIP** ou use o botão **Code** para
   copiar a URL e clonar o repositório.
2. Extraia o ZIP, se necessário, e abra o PowerShell na pasta que contém
   `main.py`, `src/` e `tests/`.
3. Confira o Python:

   ```powershell
   python --version
   ```

4. Para usar somente o analisador offline e os testes, não instale dependências
   externas.
5. Para realizar uma importação real, instale `pyodbc` no ambiente Python
   escolhido:

   ```powershell
   python -m pip install pyodbc
   ```

6. Instale separadamente um driver ODBC para SQL Server e confirme seu nome
   exato no sistema. O projeto não seleciona um driver legado como fallback.

## Configuração da importação

O importador não contém servidor, banco, driver ou caminho padrão. Defina as
três variáveis de ambiente antes de iniciar:

| Variável | Conteúdo |
|---|---|
| `LER_SQL_SERVER` | servidor ou instância de destino |
| `LER_SQL_DATABASE` | banco de destino previamente preparado |
| `LER_SQL_DRIVER` | nome exato do driver ODBC instalado, sem chaves |

Exemplo exclusivamente sintético em PowerShell:

```powershell
$env:LER_SQL_SERVER = "SERVIDOR-SINTETICO\INSTANCIA"
$env:LER_SQL_DATABASE = "BancoSintetico"
$env:LER_SQL_DRIVER = "NOME-EXATO-DO-DRIVER-ODBC"
python main.py
```

A connection string é construída com autenticação integrada:
`Trusted_Connection=yes`. A aplicação não recebe senha por argumento ou variável
própria e não acrescenta parâmetros fornecidos pelo usuário. Valores ausentes,
com whitespace externo, caracteres de controle, ponto e vírgula ou chaves são
recusados antes da abertura do arquivo e da conexão.

O caminho do arquivo é solicitado interativamente e não possui fallback.

### Segurança da conexão

Não desabilite criptografia ou validação de certificado como solução padrão para
erros de conexão. Versões do driver podem adotar comportamentos diferentes de
criptografia. Se surgir um erro de certificado, verifique a configuração TLS e a
confiança no certificado do servidor conforme a documentação oficial da
Microsoft. A aplicação não adiciona opções inseguras à connection string.

## Como utilizar

### Analisar o arquivo sem conexão

Há três modos offline. A ausência de `--database` seleciona somente a descoberta
de banco:

```powershell
python src/offline_analyzer.py --file "C:\DadosSinteticos\entrada.sql"
```

O modo examina evidências estruturais como `CREATE DATABASE`, `ALTER DATABASE`,
`USE` e alvos `INSERT` com três partes. Comentários e strings são tratados pelo
mesmo lexer do importador. Bancos administrativos, como `master`, não substituem
um destino corroborado por criação, alteração ou alvo qualificado. Evidências
incompatíveis produzem resultado ambíguo; ausência de evidência produz
`banco não determinado`.

A descoberta examina no máximo 256 fronteiras de lote por execução. Se atingir
esse limite, a cobertura é **preliminar**: o candidato vale apenas para o trecho
examinado e não afirma unicidade no restante do arquivo. Chegar ao EOF produz
cobertura completa; erro lexical, de leitura ou limite defensivo produz cobertura
parcial.

Para percorrer o arquivo e inventariar inclusive famílias que o contrato de
importação reconhece mas recusa:

```powershell
python src/offline_analyzer.py --file "C:\DadosSinteticos\entrada.sql" --inventory
```

O inventário separa linhas físicas, lotes, comandos com contagem segura,
`INSERT`, `IDENTITY_INSERT`, configurações de sessão, `USE`, DDL, administração
de banco, views, outras famílias e lotes não classificáveis. Um lote recusado
não é autorizado: a análise cria um contexto offline novo no lote seguinte e só
continua depois de um `GO` reconhecido fora de strings e comentários. Se a
estrutura não permite contar comandos com segurança, nenhum número é inventado;
o lote é registrado como não classificável. Reconhecer superficialmente uma
palavra inicial como `SELECT`, `UPDATE` ou `EXEC` não comprova que o lote possua
uma única instrução. A contagem só é publicada quando a estrutura inteira foi
validada pelo classificador restrito ou corresponde a uma forma offline fechada.

#### Validação prática do inventário offline

Em 26/10/2026, o inventário foi validado com um arquivo SQL real de mais de
9 GB, examinado integralmente em modo offline. O resultado observado foi:

- cobertura: completa;
- linhas físicas: 11.193.365;
- lotes: 111.672;
- comandos com contagem segura: 11.069.053;
- `INSERT` reconhecidos: 11.068.070;
- lotes não classificáveis: 25;
- conexão com SQL Server: nenhuma.

Cobertura completa significa que o arquivo chegou ao fim, não que todos os
comandos foram classificados. Os 25 lotes não classificáveis continuam exigindo
investigação antes de qualquer plano de importação. Essa validação não demonstra
que a importação real funcione com SQL Server e nenhum registro foi gravado em
banco de dados.

Em terminal interativo, o inventário apresenta uma mensagem inicial e atualiza o
progresso aproximadamente a cada cinco segundos. As atualizações mostram linhas
físicas, lotes, tempo decorrido e, quando a posição binária está disponível, um
percentual estimado. Esse percentual usa a posição do buffer binário, que pode
estar adiantada pela leitura antecipada; por isso não representa uma medição
exata e nunca chega a 100% antes da confirmação do fim do arquivo.

O progresso é enviado para `stderr`, enquanto o relatório final permanece em
`stdout`. Quando `stderr` não é um terminal, como em redirecionamentos e
pipelines, as atualizações são desativadas automaticamente e a saída anterior
permanece estável. Em uma interrupção tratada, as atualizações já exibidas não
são apagadas e o relatório parcial continua sendo apresentado.

O modo estrito existente continua disponível e é a etapa recomendada antes de
qualquer tentativa de importação:

```powershell
python src/offline_analyzer.py --file "C:\DadosSinteticos\entrada.sql" --database "BancoSintetico"
```

O destino lógico serve somente para validar `USE` e alvos qualificados. O
analisador não importa a aplicação principal, não cria conexão, não executa SQL
e não simula commit.

O relatório apresenta status, processamento completo ou parcial, linhas lidas e
ignoradas, comandos classificados por tipo e a primeira recusa. A análise para
nessa recusa; lotes posteriores não são inventariados.

Nos três modos offline, todos os contadores usam ponto como separador de milhar,
independentemente da configuração regional do sistema. Por exemplo,
`11.193.365 linhas` e `11.069.053 comandos`. Essa formatação afeta somente a
apresentação textual; os valores numéricos internos permanecem inalterados.

`--inventory` e `--database` não podem ser combinados. Nenhum modo offline lê as
variáveis de conexão, abre conexão, executa SQL, altera o arquivo ou preenche a
configuração usada por `main.py`.

| Código | Descoberta | Inventário | Validação estrita |
|---:|---|---|---|
| 0 | candidato encontrado | arquivo integralmente percorrido | análise concluída |
| 1 | falha sanitizada de abertura/leitura | falha sanitizada antes do inventário | falha sanitizada de abertura/leitura |
| 2 | argumentos inválidos | argumentos inválidos | instrução recusada ou argumentos inválidos |
| 3 | candidatos ambíguos | cobertura parcial | — |
| 4 | banco não determinado | — | — |
| 130 | interrupção pelo usuário | interrupção pelo usuário | interrupção pelo usuário |

Um inventário parcial preserva apenas contadores já validados antes da perda de
sincronização, falha ou limite.

### Iniciar o importador interativo

Depois de configurar o ambiente e revisar o resultado offline:

Antes da importação, crie um backup verificável do banco de destino e confirme
que ele pode ser recuperado. O backup é importante porque cada comando possui
confirmação individual e não há rollback global do arquivo. Ele não substitui a
verificação prévia de schema, permissões e demais precondições do destino.

```powershell
python main.py
```

O fluxo atual é:

1. informar o caminho completo do arquivo UTF-16;
2. escolher se deseja visualizar o progresso de leitura;
3. confirmar a opção de iniciar ou finalizar;
4. validar configuração e caminho;
5. abrir o arquivo;
6. conectar ao banco configurado e criar o cursor;
7. ler e validar cada lote;
8. executar somente os comandos emitidos pelo contrato;
9. apresentar resultado e estatísticas, fechando cada recurso de forma
   independente.

O processamento pode parar na primeira recusa. Cada comando é executado e
confirmado individualmente, portanto uma falha posterior não desfaz commits
anteriores. O programa não promete rollback do arquivo inteiro.

Se `commit()` falhar, o servidor pode ter confirmado o comando sem que o cliente
tenha recebido a resposta. O resultado é marcado como desconhecido e o comando
não deve ser repetido automaticamente.

## Como funciona o processamento

`GO` delimita lotes, mas não é enviado ao servidor e não representa uma
transação. `USE` muda somente o contexto lógico usado para conferir o destino.
O leitor faz uma primeira passagem de validação por todo o lote; somente depois
faz a passagem que pode emitir `INSERT`, `IDENTITY_INSERT` ou `SESSION_SET`.

DDL reconhecido não é executado. Ele pode ser ignorado apenas como preparação
cuja equivalência já foi comprovada externamente. SQL desconhecido ou misturado
é recusado de forma fechada.

Os limites defensivos incluem 65.536 caracteres por linha e 1.048.576 por lote.
O teto de 262.144 caracteres por comando aplica-se às formas acompanhadas
individualmente pelo parser; determinadas formas opacas de DDL são delimitadas
pelo teto do lote, não por esse limite de comando. O contrato técnico detalha os
limites de profundidade e o alcance de cada verificação.

### Estatísticas

O resumo diferencia linhas físicas, linhas ignoradas, comandos emitidos,
tentativas e retornos de `execute()`, tentativas e retornos de `commit()`,
comandos confirmados por tipo, falhas de execução e confirmações desconhecidas.

Um “INSERT confirmado” é um comando cujo `execute()` e `commit()` retornaram.
Não é contagem de registros: um comando pode conter várias tuplas, e a aplicação
não usa `rowcount` para estimar persistência.

## Limitações e cuidados importantes

- A V2 não é um reconstrutor universal de bancos SQL Server.
- O banco de destino precisa estar preparado e validado externamente.
- Nem todo T-SQL pertence ao contrato.
- Administração de banco, views, users, roles, permissões e Full-Text continuam
  recusados.
- DDL reconhecido e ignorado depende de precondições externas não verificadas
  pela aplicação.
- A suíte automatizada é offline; não houve validação prática desta V2 com SQL
  Server real.
- Uma recusa posterior não desfaz comandos já confirmados.
- Confirmação desconhecida não deve ser tratada como falha segura para repetição.
- A análise offline não consulta schema, permissões, versão do servidor,
  triggers, constraints ou comportamento do driver.
- Classificação concluída não garante que qualquer arquivo SQL seja integralmente
  importável no ambiente real.

Não inclua arquivos SQL de entrada, backups, credenciais, connection strings ou
logs locais em contribuições públicas.

## Estrutura e documentação

```text
.
├── main.py
├── src/
│   ├── main.py
│   ├── offline_analyzer.py
│   ├── offline_inspection.py
│   ├── sql_classifier.py
│   ├── data_only_policy.py
│   ├── preflight.py
│   ├── sqlserver_probe_adapter.py
│   └── sqlserver_driver_errors.py
├── tests/
├── docs/
│   ├── ARQUITETURA_E_DECISOES.md
│   └── CONTRATO_SQL.md
├── pyproject.toml
└── LICENSE
```

- [Arquitetura e decisões](docs/ARQUITETURA_E_DECISOES.md)
- [Contrato SQL](docs/CONTRATO_SQL.md)

Os módulos de política DATA-ONLY, preflight e probe representam componentes
sintéticos, fail-closed e ainda não integrados ao importador operacional. Sua
presença não autoriza descarte ou execução adicional de SQL.

## Testes automatizados

Na raiz do projeto:

```powershell
python -B -m unittest discover -s tests -t . -v
```

Os testes usam arquivos em memória, dependências simuladas e biblioteca padrão.
Não exigem SQL Server, `pyodbc` ou driver ODBC e não executam arquivos SQL reais.

## Bibliotecas e tecnologias

### Biblioteca padrão Python

O código utiliza módulos da biblioteca padrão como `argparse`, `collections.abc`,
`dataclasses`, `datetime`, `enum`, `hashlib`, `io`, `json`, `os`, `pathlib`,
`re`, `runpy`, `sys`, `time` e `typing`. Eles acompanham o Python e não precisam
ser instalados separadamente com `pip`.

### Dependência externa

- `pyodbc`: necessário somente para a importação real.

### Componentes do ambiente

- Microsoft ODBC Driver para SQL Server;
- banco SQL Server de destino previamente preparado.

O analisador offline e a suíte de testes não dependem desses componentes
externos.

## Como contribuir

Contribuições de documentação, correções, testes e melhorias são bem-vindas. Um
fluxo simples é:

1. consultar problemas e propostas na área **Issues**;
2. fazer fork do repositório V2;
3. clonar o próprio fork;
4. criar uma branch para a alteração;
5. implementar a melhoria e adicionar testes quando necessário;
6. executar a suíte offline;
7. fazer commit e push no próprio fork;
8. abrir um pull request explicando o que mudou e como foi validado.

Evite ampliar a execução genérica de SQL sem contrato conservador e testes.
Também não inclua arquivos SQL privados, dumps, backups, credenciais ou
connection strings. Não é necessário instalar SQL Server para contribuir com a
documentação, o classificador ou os testes offline.

Se o projeto for útil, uma estrela no repositório será bem-vinda.

## Projeto original

A V1 permanece disponível em
[J-o-n-a-s/LerArquivoExecutaSQL](https://github.com/J-o-n-a-s/LerArquivoExecutaSQL).
Ela registra o ponto de partida e a motivação original. A V2 preserva essa origem
e evolui o processamento com regras e verificações mais rigorosas, sem modificar
o repositório anterior.

## Licença

O projeto é disponibilizado sob os termos descritos na [licença MIT](LICENSE).
O aviso de copyright e as condições da licença devem ser preservados conforme o
arquivo da licença.

[contributors-shield]: https://img.shields.io/github/contributors/J-o-n-a-s/LerArquivoExecutaSQL-V2.svg?style=for-the-badge
[contributors-url]: https://github.com/J-o-n-a-s/LerArquivoExecutaSQL-V2/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/J-o-n-a-s/LerArquivoExecutaSQL-V2.svg?style=for-the-badge
[forks-url]: https://github.com/J-o-n-a-s/LerArquivoExecutaSQL-V2/network/members
[stars-shield]: https://img.shields.io/github/stars/J-o-n-a-s/LerArquivoExecutaSQL-V2.svg?style=for-the-badge
[stars-url]: https://github.com/J-o-n-a-s/LerArquivoExecutaSQL-V2/stargazers
[issues-shield]: https://img.shields.io/github/issues/J-o-n-a-s/LerArquivoExecutaSQL-V2.svg?style=for-the-badge
[issues-url]: https://github.com/J-o-n-a-s/LerArquivoExecutaSQL-V2/issues
[license-shield]: https://img.shields.io/github/license/J-o-n-a-s/LerArquivoExecutaSQL-V2.svg?style=for-the-badge
[license-url]: https://github.com/J-o-n-a-s/LerArquivoExecutaSQL-V2/blob/main/LICENSE
