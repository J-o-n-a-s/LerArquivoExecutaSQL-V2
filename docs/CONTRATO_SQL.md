# Contrato SQL da V2

Este documento descreve o subconjunto reconhecido pelo classificador atual. O
contrato é deliberadamente menor que T-SQL: formas não descritas aqui podem ser
recusadas mesmo quando seriam válidas no SQL Server.

## A. Comandos encaminhados ao executor

Somente três categorias produzem `SQLCommand`.

### `INSERT ... VALUES`

O formato aceito possui:

- palavra `INSERT`, com `INTO` opcional;
- alvo de uma a três partes;
- lista opcional e não vazia de colunas;
- cláusula `VALUES` com uma ou mais tuplas;
- mesma quantidade de valores em todas as tuplas e, quando há lista de colunas,
  correspondência entre valores e colunas;
- ponto e vírgula final opcional.

Valores escalares aceitos:

- números inteiros, decimais, exponenciais e hexadecimais reconhecidos pelo
  lexer, com sinal opcional;
- strings simples ou Unicode, com escape por aspas duplicadas;
- `NULL` e `DEFAULT`;
- `CAST` temporal restrito, descrito abaixo.

Não são aceitos `INSERT ... SELECT`, funções arbitrárias, subconsultas,
expressões aritméticas ou outras expressões de valor.

O `CAST` aceito tem uma string como único operando e destino `DATE`, `TIME` ou
`DATETIME2`. `TIME` e `DATETIME2` podem ter uma precisão de um algarismo entre 0
e 7; `DATE` não aceita precisão. O classificador valida a forma, não o conteúdo
calendárico da string.

### `SET IDENTITY_INSERT`

É aceito em alvo de uma a três partes, com estado `ON` ou `OFF`:

```sql
SET IDENTITY_INSERT [dbo].[TabelaSintetica] ON;
```

### Configurações de sessão

Somente estas três opções são encaminhadas, sempre com estado `ON`:

- `SET ANSI_NULLS ON`;
- `SET QUOTED_IDENTIFIER ON`;
- `SET ANSI_PADDING ON`.

Outras opções e o estado `OFF` são recusados.

## B. Metadados de contexto não executados

### `USE`

`USE` deve ocupar um lote exclusivo e conter um único identificador de banco,
com ponto e vírgula opcional. Ele não é enviado ao SQL Server. O leitor usa o
valor apenas para controlar o contexto lógico das operações seguintes.

Operações de dados e configurações de sessão fora do contexto do destino são
recusadas. Em alvos com três partes, o primeiro identificador também precisa
corresponder ao destino configurado.

### `GO`

`GO` é reconhecido em linha própria, fora de strings, identificadores e
comentários. A comparação não diferencia maiúsculas de minúsculas e admite
comentários na linha. Repetição (`GO 2`) e `GO;` não pertencem ao contrato.

`GO` apenas encerra o lote atual. Ele não é SQL enviado ao executor e não define
uma transação.

## C. DDL e metadados ignorados sob precondições externas

As formas desta seção podem ser reconhecidas e não emitidas. Isso pressupõe que
um processo externo já comprovou a preparação equivalente no banco de destino.

### `CREATE TABLE`

O classificador exige alvo, parênteses balanceados e uma definição não vazia.
Dentro da definição, ponto e vírgula ou token que inicie outra instrução provoca
recusa. Strings, identificadores delimitados e comentários permanecem opacos,
portanto palavras SQL dentro deles não criam falsos comandos.

Depois da definição são aceitos, nessa ordem, no máximo um `ON` e um
`TEXTIMAGE_ON`, cada qual seguido por um identificador simples. Outras cláusulas
de armazenamento são recusadas.

O conteúdo de colunas e constraints recebe validação lexical e estrutural, não
uma comprovação semântica completa.

### `ALTER TABLE`

O alvo deve ser seguido por uma destas ações iniciais:

- `ADD`, `ALTER`, `WITH`, `CHECK`, `NOCHECK`, `ENABLE` ou `DISABLE`;
- `DROP COLUMN`;
- `DROP CONSTRAINT`.

Parênteses precisam estar balanceados. Outra instrução no mesmo lote, mistura
após ponto e vírgula e iniciadores administrativos, de dados ou de controle são
recusados. A aceitação estrutural não comprova que a alteração já exista no
banco.

### `CREATE INDEX`

São reconhecidos `CREATE INDEX` e as combinações opcionais `UNIQUE`, `CLUSTERED`
ou `NONCLUSTERED`. O contrato exige:

- nome do índice, `ON`, alvo e uma lista não vazia de colunas;
- direção opcional `ASC` ou `DESC` por coluna;
- cláusula `WITH` opcional com opções únicas da allowlist;
- `ON` final opcional com um identificador simples de armazenamento.

Opções booleanas que aceitam `ON` ou `OFF`:

- `PAD_INDEX`;
- `STATISTICS_NORECOMPUTE`;
- `SORT_IN_TEMPDB`;
- `ONLINE`;
- `ALLOW_ROW_LOCKS`;
- `ALLOW_PAGE_LOCKS`.

Opções aceitas somente com `OFF`:

- `DROP_EXISTING`;
- `IGNORE_DUP_KEY`;
- `OPTIMIZE_FOR_SEQUENTIAL_KEY`.

`FILLFACTOR` aceita inteiro ASCII entre 0 e 100. Opções repetidas, filtros,
`INCLUDE`, chaves por expressão, particionamento e famílias especiais de índice
ficam fora do contrato.

### Rotinas

São reconhecidas formas `CREATE`, `ALTER` e `CREATE OR ALTER` de:

- `PROCEDURE` ou `PROC`;
- `TRIGGER`;
- `FUNCTION`.

O cabeçalho deve apresentar alvo e um `AS` reconhecível sem instruções de dados
ou controle indevidas antes do corpo. O corpo precisa ser não vazio e é tratado
como opaco até `GO` ou fim do arquivo; nada dentro dele é emitido pelo importador.

Existe compatibilidade lexical limitada para um ou dois caracteres `U+200B`
antes de `CREATE PROCEDURE`. Em outras posições, esse prefixo é recusado.

Reconhecer uma rotina não comprova que uma definição equivalente exista no
destino.

### Metadados estendidos

Em lote exclusivo, `EXEC` ou `EXECUTE` pode reconhecer somente
`sys.sp_addextendedproperty`, com parâmetros nomeados literais do conjunto
fechado:

- `@name`, `@value`;
- `@level0type`, `@level0name`;
- `@level1type`, `@level1name`;
- opcionalmente `@level2type`, `@level2name`.

O escopo aceito se limita a descrições `MS_Description` de tabela/coluna e aos
metadados de layout de view explicitamente reconhecidos. Parâmetros desconhecidos,
repetidos, incompletos, expressões e outros procedimentos são recusados.

## D. Comandos explicitamente recusados

Entre as famílias recusadas estão:

- `CREATE VIEW`, `ALTER VIEW` e `CREATE OR ALTER VIEW`: algumas formas podem ser
  reconhecidas estruturalmente, mas exigem equivalência externa e não são
  ignoradas automaticamente;
- `CREATE DATABASE` e `ALTER DATABASE`: formas administrativas restritas podem
  ser reconhecidas, mas não são autorizadas para descarte nem execução;
- `CREATE USER`, `ALTER USER`, criação/alteração de roles e associação de
  membros;
- `GRANT`, `DENY` e `REVOKE`;
- comandos e procedimentos Full-Text;
- `SELECT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`, `BACKUP`, `RESTORE`, `DBCC`,
  controle transacional e SQL dinâmico;
- `EXEC`/`EXECUTE` fora do metadado estendido restrito;
- opções `SET` não listadas e as três opções permitidas quando usadas com
  `OFF`;
- índices, DDL e expressões que não correspondam às formas restritas acima.

Essa relação resume famílias relevantes, mas não funciona como blacklist
exaustiva. A regra efetiva é a allowlist: o que não corresponde ao contrato é
recusado.

## E. Estruturas ambíguas ou misturadas

DDL ignorável, metadado estendido e `USE` exigem exclusividade conforme suas
regras. Exemplos recusados incluem:

- DDL seguido por `INSERT` no mesmo lote;
- `INSERT` seguido por DDL no mesmo lote;
- duas instruções DDL onde se espera uma;
- `CREATE TABLE` com uma instrução embutida na definição;
- `ALTER TABLE` seguido, com ou sem ponto e vírgula, por outra família de
  instrução;
- comando incompleto, parênteses desequilibrados ou estado lexical não
  terminado.

O leitor valida o lote inteiro antes de emitir o primeiro comando daquele lote.
Uma recusa em lote posterior, porém, não desfaz comandos de lotes anteriores que
já tenham sido confirmados.

O corpo de uma rotina reconhecida é a exceção intencional: ele é opaco até o
próximo `GO` ou fim do arquivo e nunca é emitido. Um comando posterior deve estar
em outro lote para voltar a ser classificado pelo importador.

## F. Limites físicos

O leitor aplica estes limites defensivos:

| Elemento | Limite e alcance |
|---|---:|
| Linha física | 65.536 caracteres |
| Comando acompanhado individualmente | 262.144 caracteres |
| Lote | 1.048.576 caracteres |
| Comentários de bloco aninhados | 32 níveis |
| Estrutura DDL parentizada | 64 níveis |

O limite de comando é verificado nas formas em que o parser acompanha o início
da instrução individual, incluindo comandos encaminháveis, `USE`, metadado
estendido e view reconhecida. Ele não se aplica universalmente a todo DDL opaco:
determinadas formas ignoradas de tabela, índice, rotina ou administração ficam
limitadas pelo teto do lote e pelas suas validações estruturais específicas.

Exceder um limite causa recusa. Estar dentro dele não torna uma construção
automaticamente aceita; todas as demais regras continuam válidas.

## G. Exemplos sintéticos

### Encaminhados

```sql
SET ANSI_NULLS ON;
GO
SET IDENTITY_INSERT [dbo].[TabelaSintetica] ON;
GO
INSERT INTO [dbo].[TabelaSintetica] ([id], [instante])
VALUES (1, CAST(N'2026-01-02T03:04:05' AS DATETIME2(7))),
       (2, NULL);
GO
SET IDENTITY_INSERT [dbo].[TabelaSintetica] OFF;
```

### Reconhecidos e ignorados sob precondições externas

```sql
CREATE TABLE [dbo].[TabelaSintetica] (
    [id] INT,
    [nome] NVARCHAR(80) DEFAULT N'exemplo',
    CHECK ([id] >= 0)
);
GO
ALTER TABLE [dbo].[TabelaSintetica] ADD [ativo] INT;
GO
CREATE INDEX [IX_TabelaSintetica_id]
ON [dbo].[TabelaSintetica] ([id] ASC)
WITH (ONLINE = OFF, FILLFACTOR = 90);
```

### Recusados

```sql
-- INSERT ... SELECT não pertence ao subconjunto.
INSERT INTO [dbo].[TabelaSintetica] ([id]) SELECT 1;
```

```sql
-- DDL e dados não podem compartilhar este lote.
ALTER TABLE [dbo].[TabelaSintetica] ADD [x] INT
INSERT INTO [dbo].[TabelaSintetica] ([id]) VALUES (1);
```

```sql
-- Administração e permissões não são autorizadas.
GRANT CONTROL TO [UsuarioSintetico];
```

## H. Classificação não é compatibilidade operacional

Classificação aceita significa apenas que o texto pertence ao subconjunto acima.
Ela não prova:

- validade semântica integral no SQL Server;
- existência ou equivalência de tabelas, colunas, constraints e índices;
- permissões do usuário conectado;
- compatibilidade com uma versão específica do servidor ou driver;
- ausência de efeitos de triggers ou outras configurações externas;
- sucesso de execução, commit ou importação integral do arquivo.

A análise offline aplica exatamente essa classificação sem consultar o banco. A
validação operacional precisa ser feita separadamente em um ambiente controlado.

## I. Descoberta e inventário não ampliam a autorização

A descoberta de banco e o inventário offline podem reconhecer nomes, famílias e
formas que permanecem recusadas por este contrato. Esse reconhecimento serve
somente para relatório. Ele não transforma DDL, views, administração ou outras
famílias em `SQLCommand`, não configura o destino do importador e não permite que
um lote inventariado alcance o cursor.

No inventário, uma recusa pode ser seguida por análise de outro lote apenas
porque uma nova fronteira lexical `GO` cria um contexto offline independente. O
leitor estrito continua inválido após a primeira recusa e não é retomado.
