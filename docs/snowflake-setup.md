# Configuração do Snowflake no `.env`

Este guia habilita a integração real de analytics. O caminho de demonstração
continua válido com `SNOWFLAKE_MODE=fake` e não envia dados para fora do stack
local.

Não cole PEM, senha, HMAC ou identificadores reais em issue, chat ou terminal
gravado. `.env`, `secrets/` e arquivos `*.pem` / `*.key` já estão no `.gitignore`.

## Resultado esperado

O bloco Snowflake no `.env` deve ficar no formato abaixo. Os valores são
exemplos; substitua pelos da sua conta.

```dotenv
SNOWFLAKE_MODE=real
SNOWFLAKE_ACCOUNT=orgname-accountname
SNOWFLAKE_USER=DOGSENSE_WRITER
SNOWFLAKE_DATABASE=DOGSENSE
SNOWFLAKE_SCHEMA=BEHAVIOR
SNOWFLAKE_WAREHOUSE=DOGSENSE_WH
SNOWFLAKE_ROLE=DOGSENSE_WRITER_ROLE
SNOWFLAKE_PRIVATE_KEY=
SNOWFLAKE_PRIVATE_KEY_HOST_PATH=/home/jairomendes/dogsense/secrets/snowflake-rsa.p8
SNOWFLAKE_PRIVATE_KEY_PATH=/run/secrets/snowflake-private-key
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=
ANALYTICS_HMAC_KEY=<hex de 64 caracteres, gerado localmente>
```

A autenticação de runtime é por **chave RSA**, não por senha. Deixe
`SNOWFLAKE_PRIVATE_KEY` vazio: o Compose monta o arquivo PKCS#8 via
`SNOWFLAKE_PRIVATE_KEY_HOST_PATH` em `/run/secrets/snowflake-private-key`, somente
leitura. O adapter lê esse arquivo, não o conteúdo colado no `.env`.

## 1. Confirme que o `.env` existe

Na raiz do repositório:

```bash
make setup
```

Se o `.env` já existir, ele não é sobrescrito. O comando cria `secrets/` com
permissão `700`.

## 2. Identificador da conta

No Snowsight o identificador **não** é o e-mail de login.

Pela URL:

- `https://app.snowflake.com/<org>/<account>/...` → use `org-account` (hífen);
- `https://<locator>.snowflakecomputing.com` → use o locator, às vezes com
  região (`xy12345.us-east-1`).

No worksheet, como `ACCOUNTADMIN`:

```sql
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS ACCOUNT_IDENTIFIER;
```

Esse valor vai em `SNOWFLAKE_ACCOUNT`.

## 3. Warehouse, role e usuário de menor privilégio

Execute como `ACCOUNTADMIN`. Ajuste os nomes se necessário.

```sql
CREATE WAREHOUSE IF NOT EXISTS DOGSENSE_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

CREATE ROLE IF NOT EXISTS DOGSENSE_WRITER_ROLE;
CREATE USER IF NOT EXISTS DOGSENSE_WRITER
  TYPE = SERVICE
  DEFAULT_ROLE = DOGSENSE_WRITER_ROLE
  DEFAULT_WAREHOUSE = DOGSENSE_WH;

GRANT ROLE DOGSENSE_WRITER_ROLE TO USER DOGSENSE_WRITER;
GRANT USAGE ON WAREHOUSE DOGSENSE_WH TO ROLE DOGSENSE_WRITER_ROLE;
```

`TYPE = SERVICE` cria um usuário de integração sem login interativo, adequado
para key-pair. Não use `ACCOUNTADMIN` no runtime da API.

## 4. Chave RSA fora do Git

```bash
mkdir -p secrets
chmod 700 secrets

openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out secrets/snowflake-rsa.p8 -nocrypt
openssl rsa -in secrets/snowflake-rsa.p8 -pubout -out secrets/snowflake-rsa.pub
chmod 600 secrets/snowflake-rsa.p8
```

`SNOWFLAKE_PRIVATE_KEY_HOST_PATH` precisa ser o **caminho absoluto** desse `.p8`
no host, por exemplo `/home/jairomendes/dogsense/secrets/snowflake-rsa.p8`.
Caminho relativo (`./secrets/...`) é rejeitado pelo preflight.

Se o PKCS#8 tiver passphrase, coloque a mesma string em
`SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`. Sem passphrase, deixe a variável vazia.

## 5. Associe a chave pública ao usuário

Copie somente o miolo de `secrets/snowflake-rsa.pub` (sem as linhas `BEGIN` /
`END`) e execute:

```sql
ALTER USER DOGSENSE_WRITER SET RSA_PUBLIC_KEY='MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A...';
```

Confirme:

```sql
DESC USER DOGSENSE_WRITER;
```

O campo `RSA_PUBLIC_KEY_FP` deve aparecer preenchido.

## 6. Database, schema, tabela e views

Execute nesta ordem, com um role que possa criar objetos (`ACCOUNTADMIN` ou
equivalente). No Snowsight: **Projects → Worksheets → + → SQL File**.

1. [`snowflake/migrations/001_behavior_schema.sql`](../snowflake/migrations/001_behavior_schema.sql)
2. [`snowflake/views/001_dog_state_hourly.sql`](../snowflake/views/001_dog_state_hourly.sql)
3. [`snowflake/views/002_dog_state_daily.sql`](../snowflake/views/002_dog_state_daily.sql)

Depois conceda só o necessário ao writer:

```sql
GRANT USAGE ON DATABASE DOGSENSE TO ROLE DOGSENSE_WRITER_ROLE;
GRANT USAGE ON SCHEMA DOGSENSE.BEHAVIOR TO ROLE DOGSENSE_WRITER_ROLE;
GRANT USAGE ON SCHEMA DOGSENSE.ANALYTICS TO ROLE DOGSENSE_WRITER_ROLE;
GRANT INSERT, UPDATE, SELECT ON TABLE DOGSENSE.BEHAVIOR.STATE_EVENTS TO ROLE DOGSENSE_WRITER_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA DOGSENSE.ANALYTICS TO ROLE DOGSENSE_WRITER_ROLE;
```

A API grava eventos primeiro no PostgreSQL e envia um `MERGE` idempotente por
`EVENT_ID`. O Snowflake recebe IDs pseudonimizados e metadados consolidados;
nunca recebe URL RTSP, nome do tutor, frames ou vídeo.

## 7. Variáveis do `.env`

| Variável | Origem | Observação |
|---|---|---|
| `SNOWFLAKE_MODE` | `real` | Só depois dos passos 2–6 |
| `SNOWFLAKE_ACCOUNT` | passo 2 | `org-account` ou locator |
| `SNOWFLAKE_USER` | `DOGSENSE_WRITER` | Mesmo nome do `CREATE USER` |
| `SNOWFLAKE_DATABASE` | `DOGSENSE` | Padrão do `.env.example` |
| `SNOWFLAKE_SCHEMA` | `BEHAVIOR` | Padrão do `.env.example` |
| `SNOWFLAKE_WAREHOUSE` | `DOGSENSE_WH` | Obrigatório no preflight |
| `SNOWFLAKE_ROLE` | `DOGSENSE_WRITER_ROLE` | Recomendado |
| `SNOWFLAKE_PRIVATE_KEY` | vazio | Não cole o PEM aqui |
| `SNOWFLAKE_PRIVATE_KEY_HOST_PATH` | caminho absoluto do `.p8` | Arquivo local existente e não vazio |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | `/run/secrets/snowflake-private-key` | Caminho dentro do contêiner; não altere |
| `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | vazio ou senha do PKCS#8 | Só se a chave tiver passphrase |
| `ANALYTICS_HMAC_KEY` | passo 8 | Não pode permanecer o valor de demo |

Deixe Google AI, ElevenLabs e Solana em `fake` até o Snowflake estar estável.
Cada integração liga de forma independente.

## 8. Chave HMAC analítica

O placeholder `dogsense-demo-analytics-key-change-me` é rejeitado pelo preflight.
Gere um valor exclusivo, diferente de `JWT_SECRET` e de
`CREDENTIAL_ENCRYPTION_KEY`:

```bash
openssl rand -hex 32
```

Cole o resultado em `ANALYTICS_HMAC_KEY`. Essa chave só pseudonimiza IDs
enviados ao Snowflake. Rotacioná-la muda os hashes e exige reconciliação do
histórico analítico. Ver [`docs/privacy.md`](privacy.md) e
[`docs/runbook.md`](runbook.md).

## 9. Validação

```bash
make preflight-real
docker compose up --build --detach
make health
```

O preflight confirma presença; nunca imprime os valores. Com
`SNOWFLAKE_MODE=real`, exige:

- `SNOWFLAKE_ACCOUNT`
- `SNOWFLAKE_USER`
- `SNOWFLAKE_WAREHOUSE`
- arquivo em `SNOWFLAKE_PRIVATE_KEY_HOST_PATH`
- `ANALYTICS_HMAC_KEY` que não seja placeholder

Se a API já estiver no ar:

```bash
docker compose restart api
```

Consulte `http://localhost:8000/health/ready` e
`/api/v1/integrations/status`. A integração `snowflake` deve aparecer
`available`. Se vier `degraded`, o restante do produto continua; volte para
`SNOWFLAKE_MODE=fake` para isolar o problema, conforme o runbook.

## O que não fazer

- não cole PEM, senha ou HMAC em chat, issue ou commit;
- não use `ACCOUNTADMIN` no runtime;
- não preencha `SNOWFLAKE_PRIVATE_KEY` com o conteúdo da chave;
- não publique o PostgreSQL nem exponha o `.env` na rede;
- não envie frames, URL RTSP ou identificadores previsíveis do animal ao
  Snowflake.
