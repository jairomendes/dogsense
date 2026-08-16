# Privacidade, segurança e uso responsável

## Princípio

Vídeo doméstico é dado sensível. DogSense minimiza coleta e retenção: analisa uma
janela curta, mantém pixels em memória e persiste somente eventos consolidados.
O sistema descreve sinais observáveis; não diagnostica doença, dor ou ansiedade.

## Fluxo de dados

| Dado | Destino | Retenção padrão | Regra |
|---|---|---:|---|
| stream RTSP | MediaMTX local + worker | nenhuma | gravação desabilitada |
| frames amostrados | memória do worker / Google AI real | duração da chamada | nunca em logs ou disco |
| estado/evento | PostgreSQL local | até exclusão pelo usuário | fonte operacional |
| evento pseudonimizado | Snowflake real | política da conta; sugerido 30 dias no MVP | HMAC com chave dedicada |
| texto allowlisted | ElevenLabs real | conforme política do provedor | sem texto livre do modelo |
| áudio gerado | volume local | até 24 h | arquivo protegido, não no banco |
| hash + ID técnico | Solana Devnet | público e permanente | sem PII ou mídia |
| logs estruturados | host Docker | rotação 3 × 10 MiB | redaction obrigatória |

Fakes não enviam dados para Google AI, Snowflake, ElevenLabs ou Solana.

## Antes de filmar

- obtenha consentimento de qualquer pessoa que possa entrar no enquadramento;
- evite portas, janelas, telas, documentos e áreas íntimas;
- crie na câmera uma conta somente leitura e exclusiva para DogSense;
- confirme a política e região de processamento de cada provedor habilitado;
- defina propósito, retenção, responsável e processo de exclusão;
- não use o produto como substituto de supervisão ou atendimento veterinário;
- não provoque estresse para criar dados ou demonstrar alertas.

## Credenciais

- `.env`, `secrets/`, PEMs e keypairs são ignorados pelo Git;
- `.env` criado pelo bootstrap recebe permissão `0600` quando suportado;
- senha da câmera deve ser separada da URL sanitizada e criptografada;
- a API nunca retorna a senha; logs usam apenas `camera_id`;
- use uma chave HMAC diferente de JWT e da criptografia de câmera;
- keypair Solana fica apenas no backend, com saldo mínimo e somente Devnet;
- segredos de produção pertencem a um secret manager, não ao Compose.

Evite colocar segredo diretamente na linha de comando: histórico do shell e lista
de processos podem registrá-lo. Nunca envie `.env` em chat ou issue.

## Conteúdo proibido em logs e telemetria

- frame, thumbnail ou base64;
- URL RTSP completa, usuário ou senha;
- token JWT, token interno ou API key;
- chave privada Snowflake ou Solana;
- áudio binário;
- nome/endereço doméstico;
- prompt com imagem serializada.

Erros devem registrar identificadores opacos, código, provedor, latência, versão
de contrato e correlation ID. Antes de compartilhar um bundle de diagnóstico,
faça busca por `rtsp://`, `Authorization`, `api_key`, `BEGIN PRIVATE KEY` e blocos
base64 longos.

## Pseudonimização analítica

IDs previsíveis não podem receber hash simples. Use:

```text
hex(HMAC-SHA-256(ANALYTICS_HMAC_KEY, tenant_id + ":" + entity_id))
```

A chave é exclusiva do ambiente, rotacionável e não é enviada ao Snowflake. A
pseudonimização reduz vínculo direto, mas não torna o conjunto automaticamente
anônimo; retenção e controle de acesso continuam necessários.

## Solana é pública

Um receipt confirmado não pode ser apagado. A representação canônica inclui
somente identificador técnico, ID pseudonimizado, timestamps, estado, atividade,
confiança, sinais permitidos e versão do prompt. Publique apenas o memo:

```text
DOGSENSE:v1:{event_id}:{sha256}
```

Não publique receipt de evento aberto. Antes da primeira transação real, inspecione
o JSON canônico e faça um ensaio com um evento inteiramente sintético.

## Segurança de rede

O Compose liga serviços publicados a `127.0.0.1` e mantém PostgreSQL em rede
interna. Para acesso na LAN, não basta trocar o bind: habilite JWT, remova o token
local, restrinja CORS/Origin, proteja endpoints administrativos, configure ICE e
filtre firewall. Não exponha RTSP ou MediaMTX Control API à internet.

URLs de câmera devem aceitar apenas `rtsp`/`rtsps`, bloquear destinos proibidos e
ser testadas sem retornar a URL ao cliente. Isso reduz SSRF e vazamento de senha.

## Direitos e exclusão

Exclusão deve alcançar, conforme o modo ativo:

1. eventos e feedback no PostgreSQL;
2. jobs/outbox associados;
3. cache de áudio local;
4. linhas pseudonimizadas no Snowflake;
5. backups dentro de sua janela de retenção.

Receipts já publicados na Solana não são apagáveis; essa limitação deve ser
explicada antes do consentimento. O runbook omite um comando genérico de remoção
de volumes para evitar perda acidental. A operação deve resolver e confirmar o
alvo exato antes de excluir dados.

## Resposta a incidente de privacidade

1. interrompa novas chamadas do provedor afetado mudando apenas seu modo para
   `fake` e reiniciando o componente;
2. preserve logs já sanitizados e correlation IDs, sem copiar mídia;
3. revogue/rotacione a credencial potencialmente exposta;
4. identifique tipo de dado, período, destino e titulares afetados;
5. aplique o processo jurídico/organizacional de notificação;
6. valide redaction e retenção antes de reabilitar o modo real.

