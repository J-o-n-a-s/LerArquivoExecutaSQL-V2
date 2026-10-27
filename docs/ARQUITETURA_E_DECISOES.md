# Arquitetura e decisões da V2

## Objetivo e limites do importador

A V2 importa um subconjunto conservador de comandos de dados para SQL Server.
Ela foi projetada para trabalhar com um banco de destino previamente preparado,
no qual schema, tabelas, colunas, constraints, índices, triggers, permissões e
demais precondições já tenham sido verificados por um processo externo.

O programa não reconstrói integralmente um banco. Fazer isso exigiria interpretar
uma parcela muito maior de T-SQL e comprovar dependências, ordem de criação,
permissões e efeitos específicos do servidor. Em vez de presumir que essas
condições são válidas, a V2 mantém um contrato pequeno e recusa o que não consegue
classificar com segurança.

O contrato SQL detalhado está em [CONTRATO_SQL.md](CONTRATO_SQL.md).

## Fluxo de processamento

O fluxo é dividido em três responsabilidades:

1. **Leitura incremental:** `SQLCommandReader` lê o arquivo UTF-16 linha a linha,
   mantém apenas o lote atual em memória e reconhece linhas `GO` fora de strings,
   identificadores delimitados e comentários.
2. **Classificação:** o lote passa pelo lexer e pelo classificador conservador.
   O resultado pode ser um comando encaminhável, metadado de contexto, DDL
   reconhecido e ignorável sob precondições externas ou uma recusa.
3. **Execução:** somente comandos classificados como `INSERT`,
   `IDENTITY_INSERT` ou `SESSION_SET` chegam ao cursor do importador.

Essa separação reduz o acoplamento entre leitura e efeitos externos. O
classificador não importa `pyodbc`, não abre conexão e não executa SQL. O
analisador offline reutiliza o mesmo leitor e o mesmo classificador sem importar
a aplicação principal.

## Leitura incremental em UTF-16

O arquivo de entrada é aberto explicitamente como UTF-16. A leitura usa chamadas
limitadas de `readline()` e acumula somente o lote atual. Isso evita carregar o
arquivo inteiro em memória e permite interromper o processamento na primeira
recusa ou falha de leitura.

O estado lexical atravessa linhas para que `GO` dentro de string, identificador
delimitado ou comentário não seja confundido com separador. Linhas vazias,
comentários isolados e linhas `GO` são contabilizados como linhas ignoradas pelo
leitor; isso não significa que um comando SQL tenha sido executado ou descartado
no servidor.

## Validação integral do lote

Antes de emitir qualquer `SQLCommand`, o leitor percorre o lote inteiro uma
primeira vez. Nessa passagem ele valida sintaxe restrita, contexto lógico, alvos
qualificados e solicitações de sessão. Somente se toda a passagem for aceita o
lote é classificado novamente para emitir os comandos.

Esse desenho evita executar o primeiro comando de um lote para só depois
descobrir que existe conteúdo inválido no final. DDL misturado, instruções
múltiplas onde se exige lote exclusivo e estruturas incompletas falham de forma
fechada.

A validação é limitada ao lote corrente. O leitor continua incremental e não lê
todos os lotes futuros antes de liberar um lote já validado.

## `GO` não é uma transação

`GO` é reconhecido apenas como separador de lotes. Ele não é enviado ao SQL
Server, não abre transação, não executa `commit` e não garante atomicidade entre
lotes.

Essa distinção é importante: um arquivo pode conter vários lotes separados por
`GO`, enquanto a confirmação é feita individualmente para cada comando
encaminhado ao executor.

## Contexto lógico com `USE`

`USE` também não é enviado ao servidor. Em lote exclusivo, ele altera apenas o
contexto lógico mantido pelo leitor. Esse contexto é usado para impedir que
operações de dados ou configurações de sessão sejam autorizadas fora do banco de
destino informado.

Alvos com três partes têm o banco comparado ao destino configurado. Essa
verificação é textual e estrutural; ela não consulta catálogos nem comprova que o
objeto exista.

## Política conservadora para SQL desconhecido

O classificador segue uma allowlist. Uma instrução não é aceita por parecer
inofensiva: ela precisa corresponder a uma forma implementada. SQL desconhecido,
misturado, incompleto ou ambíguo é recusado com um motivo fixo que não reproduz o
conteúdo do comando.

Strings, identificadores delimitados e comentários são tokenizados antes das
decisões estruturais. Assim, uma palavra como `DROP` dentro de um valor textual
não é tratada como uma segunda instrução. A mesma palavra como token de comando
em uma posição não autorizada provoca recusa.

## DDL reconhecido e precondições externas

Algumas formas restritas de DDL e metadados são reconhecidas para que um arquivo
possa conter preparação já aplicada ao banco. Quando aceitas, essas formas são
ignoradas pelo importador e nunca chegam ao cursor.

Esse reconhecimento não prova equivalência de schema. Ignorar um `CREATE TABLE`,
por exemplo, só é seguro se uma verificação externa já tiver confirmado que a
tabela de destino possui a estrutura necessária. O mesmo princípio vale para
índices, constraints, rotinas e metadados estendidos.

Formas administrativas de banco e definições de views podem ser reconhecidas
estruturalmente, mas continuam recusadas. Reconhecimento não é autorização.

## Execução, confirmação e falhas

Para cada comando emitido, o importador segue esta ordem:

1. chama `cursor.execute()`;
2. aguarda o retorno;
3. chama `connection.commit()`;
4. somente após o retorno do `commit`, contabiliza o comando como confirmado.

Não há uma única transação envolvendo o arquivo inteiro. Se uma recusa ou falha
ocorrer depois, commits anteriores não podem ser desfeitos pelo rollback do
comando atual. Por isso o programa informa quando a importação ficou parcial.

Se `execute()` falhar antes de retornar, o importador tenta rollback e registra
falha de execução. Se `commit()` lançar uma exceção, o servidor pode ter aplicado
a confirmação mesmo sem o cliente receber a resposta. O resultado é então
marcado como desconhecido. Configurações de sessão também podem persistir apesar
de rollback transacional.

O programa não repete comandos automaticamente nesses casos. Repetir um comando
cujo resultado é desconhecido poderia duplicar dados ou alterar novamente o
estado da sessão.

## Estatísticas

As estatísticas separam eventos diferentes:

- **linhas físicas lidas:** chamadas de leitura concluídas pelo leitor;
- **linhas ignoradas:** linhas vazias, comentários, separadores e DDL/metadados
  reconhecidos que não emitiram comando;
- **comandos emitidos:** objetos `SQLCommand` entregues ao executor;
- **execuções tentadas/com retorno:** chamadas a `execute()` e retornos
  observados;
- **commits tentados/com retorno:** chamadas a `commit()` e retornos observados;
- **comandos confirmados:** comandos cujo `execute()` e `commit()` retornaram;
- **confirmações por tipo:** `INSERT`, `IDENTITY_INSERT` e `SESSION_SET`;
- **falhas de execução:** falhas observadas durante `execute()`;
- **confirmações desconhecidas:** casos em que rollback não prova o resultado.

Um `INSERT` confirmado representa um comando, não uma linha afetada. Um único
comando pode conter várias tuplas, e o importador não usa `rowcount` para estimar
persistência.

## Analisador offline

O analisador offline possui três modos independentes. Todos abrem somente o
arquivo UTF-16 solicitado, usam leitura limitada por linha e conservam no máximo
o lote corrente. Nenhum abre conexão, executa SQL, cria transação, simula commit
ou consulta configurações do importador.

`SQLBatchReader` concentra a separação incremental de lotes e o estado lexical
de strings, identificadores, comentários e `GO`. O `SQLCommandReader` estrito
continua aplicando seu contrato fail-closed sobre esses lotes. As ferramentas
offline reutilizam `SQLBatchReader`, `tokenize_sql` e a classificação sintática,
evitando um segundo lexer.

### Descoberta de banco

Sem `--database`, o analisador reúne somente nomes associados a evidências
estruturais: `CREATE DATABASE`, `ALTER DATABASE`, `USE` isolado e banco explícito
em `INSERT` com alvo de três partes. Tokens de string e comentário nunca viram
evidência. Um `USE master` é mantido como contexto administrativo e não concorre
com um destino corroborado por evidência mais forte.

Antes de entrar no relatório público, o nome precisa ser inteiramente
imprimível. Quebras de linha, controles C0/C1, escapes de terminal e outros
caracteres não imprimíveis invalidam a evidência, impedindo que um identificador
delimitado manipule a apresentação do terminal.

Evidências estruturais fortes divergentes, ou um `USE` não administrativo
divergente, produzem ambiguidade. Sem evidência, o banco não é determinado. A
descoberta tem limite padrão de 256 fronteiras de lote. Atingir esse teto produz
cobertura preliminar, nunca uma afirmação de unicidade sobre o restante do
arquivo.

### Inventário offline

O inventário percorre o arquivo completo quando as fronteiras permanecem
confiáveis. Cada lote é tokenizado antes da classificação. Comandos aceitos no
modo estrito são contados sem guardar seu texto; DDL, views e administração de
banco reconhecidos são descritos, mas não autorizados nem convertidos em
comandos executáveis.

Uma palavra inicial conhecida não basta para declarar um comando. Famílias para
as quais não existe gramática integral reutilizável ficam como conteúdo não
classificável quando a quantidade de instruções não pode ser provada. Formas
administrativas com sufixo só fornecem contagem e evidência quando o
classificador restrito valida o lote completo; a única exceção offline direta é
a forma fechada `CREATE DATABASE <identificador>` sem conteúdo adicional.

Uma recusa sintática não reutiliza o `SQLCommandReader` invalidado. O lote é
registrado como não classificável e o inventário só tenta continuar depois da
fronteira `GO` que já foi reconhecida pelo leitor lexical. Falha lexical,
estouro de linha/lote ou perda de sincronização encerra a contagem segura e
marca cobertura parcial. Contadores de lotes anteriores são preservados.

“Lotes” e “comandos” não são equivalentes. Um lote pode conter vários comandos;
por outro lado, um lote ambíguo pode ser contabilizado sem que se atribua a ele
uma quantidade inventada de comandos. “Completo” significa que o EOF foi
alcançado, mesmo que existam lotes explicitamente não classificáveis.

### Validação estrita

Com `--database`, o comportamento anterior permanece: o destino lógico é
informado explicitamente e o mesmo `SQLCommandReader` para na primeira recusa.
Descoberta e inventário não alimentam silenciosamente esse destino.

O relatório informa se a análise foi concluída, recusada ou interrompida, além de
linhas lidas, linhas ignoradas, comandos classificados por tipo e a primeira
recusa. O processamento para nessa primeira recusa; portanto, um resultado
parcial não é inventário completo dos lotes posteriores.

A análise offline é recomendada antes da importação, mas não verifica o schema
real, permissões, versão do servidor, triggers, constraints nem efeitos do
driver. Conclusão offline significa apenas que o texto percorrido pertence ao
subconjunto reconhecido.

## Componentes sintéticos e operação real

Os módulos de política DATA-ONLY e preflight modelam solicitações, evidências e
decisões sintéticas para testes fail-closed. O adaptador de probe modela leituras
limitadas e traduz erros de driver de forma sanitizada.

Esses componentes não estão ligados ao fluxo de execução do importador e não
liberam DDL, views ou administração. A aplicação principal usa apenas o
classificador para decidir quais comandos podem chegar ao cursor. Uma futura
integração operacional exigiria desenho, evidência e testes próprios; a presença
dos módulos não deve ser interpretada como autorização já disponível.

## Consequências práticas

- Prepare e valide o banco de destino por um processo separado.
- Execute primeiro a análise offline e trate a primeira recusa.
- Considere que comandos anteriores podem já estar confirmados quando ocorrer
  uma falha posterior.
- Não repita automaticamente um comando com confirmação desconhecida.
- Trate DDL ignorado como uma precondição a comprovar, nunca como schema criado
  pela V2.
- Não use classificação aceita como prova de compatibilidade com uma instalação
  específica do SQL Server.
