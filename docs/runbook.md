# Runbook operacional

## Escopo e prioridades

Este runbook cobre o stack Docker Compose do MVP. Priorize, nesta ordem:

1. proteger pessoas, credenciais e vídeo doméstico;
2. manter o stream e a UI disponíveis;
3. preservar a timeline transacional;
4. recuperar integrações derivadas sem duplicar efeitos.

| Severidade | Exemplo | Resposta |
|---|---|---|
| Sev-1 | exposição de frame/segredo, uso de Solana Mainnet | interromper integração, rotacionar e escalar imediatamente |
| Sev-2 | vídeo/estado indisponível na demo, perda/corrupção de evento | mitigar agora e preservar evidência sanitizada |
| Sev-3 | Snowflake/voz/receipt degradado | manter fake/pending e corrigir após fluxo principal |

## Verificação inicial

```bash
make ps
make health
docker compose logs --tail=200 api video-worker mediamtx postgres
docker compose config --quiet
```

Não execute `docker compose config` sem `--quiet` em uma gravação ou ticket: a
renderização completa pode conter segredos interpolados.

Health endpoints:

- `/health/live`: processo da API está respondendo;
- `/health/ready`: dependências obrigatórias para servir tráfego estão prontas;
- `/api/v1/integrations/status`: modo/estado dos provedores, sem credenciais.

Use `analysis_id`, `session_id`, `camera_id` e `event_id` para correlacionar. Não
cole logs brutos antes de verificar redaction.

## API não fica ready

1. `docker compose ps postgres api`;
2. confira somente o final dos logs: `docker compose logs --tail=100 postgres api`;
3. valide DSN pelo hostname interno `postgres`, não `localhost`;
4. execute `make migrate` se a mensagem indicar schema ausente;
5. reinicie apenas API: `docker compose restart api`;
6. confirme `/health/ready` e depois `make smoke`.

Não recrie o volume como tentativa inicial. Isso apaga a principal fonte da
timeline e pode mascarar uma migração defeituosa.

## Câmera offline ou sem primeiro frame

1. confirme energia/rede da câmera fora do aplicativo;
2. use o endpoint de teste; sucesso exige cinco frames válidos;
3. verifique `mediamtx` e o path `dog-camera`, sem imprimir a URL fonte;
4. confira codec H.264, transporte TCP e relógio do host;
5. aguarde o backoff de reconexão (1–30 s);
6. confirme que nenhum frame antigo aparece após o retorno.

Na demo controlada:

```bash
docker compose --profile controlled-video up --detach mediamtx demo-camera
```

Se o path já possuir outro publisher, pare a fonte anterior antes de iniciar a
nova. Nunca registre a URL RTSP completa no ticket.

## WebRTC falha, mas RTSP funciona

- confirme TCP `8889` e UDP `8189` no firewall local;
- no acesso remoto, defina `MEDIAMTX_WEBRTC_ADDITIONAL_HOSTS` com o IP anunciado;
- confirme HTTPS/origem segura quando exigida pelo navegador;
- fixe o navegador do ensaio e teste após mudança de rede;
- não exponha a Control API `9997`.

O fallback HLS está desabilitado no MVP para reduzir superfície e latência; só o
habilite após decisão arquitetural e teste de privacidade.

## Worker sem novos estados

1. confirme que MediaMTX recebe frames e que `last_frame_age` avança;
2. confira fila (máximo 1) e latência da inferência;
3. em demo, valide o path do cenário e sua sintaxe JSON;
4. reinicie apenas `video-worker` para recomeçar o cenário;
5. em modo real, volte temporariamente `DOGSENSE_AI_PROVIDER=fake` e reinicie o worker.

Timeout/JSON inválido não autoriza resultado fabricado. Após dez segundos sem
análise válida, o estado técnico deve ser `service_degraded`.

## PostgreSQL e outbox

- PostgreSQL não deve ter porta publicada no host;
- use `make psql` para inspeção local autorizada;
- monitore jobs pendentes, tentativas e próximo retry;
- antes de repetir um job, confirme a chave de idempotência;
- Snowflake nunca substitui a timeline PostgreSQL.

Backup lógico direcionado, sem incluir secrets ou mídia:

```bash
docker compose exec -T postgres pg_dump -U dogsense -d dogsense --format=custom > dogsense-db.backup
```

O arquivo contém dados do usuário: armazene-o cifrado, registre retenção e não o
anexe a tickets. Restauração deve ser ensaiada em ambiente isolado e requer uma
janela aprovada; não está automatizada por este repositório.

## Integrações externas

### Google AI

- sintomas: timeout, saída inválida, rate limit;
- mitigação: `DOGSENSE_AI_PROVIDER=fake`, restart do worker;
- confirme que vídeo continua e nenhum frame entrou no log;
- reabilite com canário e limite de custo.

### Snowflake

- sintomas: outbox crescente, auth/warehouse indisponível;
- mitigação: `SNOWFLAKE_MODE=fake` ou mantenha jobs pendentes;
- valide migration/views e `MERGE` por `EVENT_ID` em sandbox;
- ao recuperar, drene em lotes de até 20 e observe duplicatas.

### ElevenLabs

- sintomas: geração falha/rate limit/cache ausente;
- mitigação: texto continua na UI; `ELEVENLABS_MODE=fake`;
- não narre `summary` livre; reenvio usa a mesma chave de deduplicação.

### Solana

- sintomas: RPC indisponível, saldo, confirmação pendente;
- mitigação: receipt local `pending`, `SOLANA_MODE=fake`;
- confirme `SOLANA_NETWORK=devnet` antes de qualquer retry;
- verifique snapshot canônico/hash antes de republicar.

## Reinício seguro

Reinicie o menor componente possível:

```bash
docker compose restart video-worker
docker compose restart api
docker compose restart mediamtx
```

`make down` preserva volumes. Não use `down --volumes`, remoção recursiva ou prune
como diagnóstico. Exclusão material exige confirmação do usuário e alvo resolvido.

## Rotação de segredo

1. coloque a integração em fake ou suspenda novas ações;
2. revogue a credencial antiga no provedor;
3. grave a nova credencial no secret manager/`.env` local protegido;
4. reinicie somente o consumidor;
5. execute um canário sintético;
6. confirme que logs/config compartilhados não expõem o valor;
7. documente data, escopo e responsável sem registrar o segredo.

Rotacionar `ANALYTICS_HMAC_KEY` muda os pseudônimos; planeje reconciliação e
período de transição. Rotacionar a chave de criptografia da câmera requer
recriptografar valores existentes de modo versionado.

## Encerramento do incidente

- causa e janela temporal registradas;
- serviços e modos ativos identificados;
- smoke e cenário afetado aprovados;
- filas drenadas sem efeitos duplicados;
- segredo/frame ausente dos artefatos;
- ação preventiva com dono e prazo;
- política de retenção aplicada aos dados coletados durante o incidente.
