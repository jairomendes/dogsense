# Arquitetura

## Visão geral

O MVP roda no mesmo host, mas separa vídeo, inferência, domínio transacional e
interface. Essa divisão permite mover o worker para um agente local no futuro sem
alterar o contrato público.

```mermaid
flowchart LR
    Camera["Câmera RTSP ou fonte controlada"] --> Media["MediaMTX / dog-camera"]
    Media -->|"WebRTC/WHEP"| Web["Web / React"]
    Media -->|"RTSP interno"| Worker["Video worker"]
    Worker --> Google["Google AI ou fake"]
    Google --> Engine["Validação + motor temporal"]
    Engine -->|"ingestão idempotente"| API["FastAPI"]
    API --> PG[("PostgreSQL + outbox")]
    API -->|"snapshot + WebSocket"| Web
    PG --> Jobs["Jobs de integração"]
    Jobs --> Snow["Snowflake ou fake"]
    Jobs --> Voice["ElevenLabs ou fake"]
    Jobs --> Sol["Solana Devnet ou fake"]
```

O pipeline de vídeo é independente da inferência. A indisponibilidade de Google
AI expira o último estado para `service_degraded`, mas não deve interromper o
player. Snowflake, voz e receipt são trabalhos derivados e não participam da
transação que publica o estado ao vivo.

Os diagramas abaixo foram gerados a partir do código em `services/`, `apps/web/`
e `infra/docker-compose.yml`. As fontes editáveis estão em
[`diagrams/`](diagrams/).

## Responsabilidades

| Componente | Responsabilidade | Não deve fazer |
|---|---|---|
| MediaMTX | receber uma publicação RTSP e distribuir RTSP/WebRTC | gravar mídia |
| Video worker | buffer limitado, amostragem, inferência e estabilização | persistir frames |
| API | autenticação, estado, eventos, outbox, WebSocket e integrações | retornar senha RTSP |
| PostgreSQL | fonte operacional da timeline e jobs | armazenar vídeo/áudio binário |
| Web | player, estado provável, evidências e ações acessíveis | acessar secrets |
| Snowflake | histórico pseudonimizado e agregações | dirigir a UI ao vivo |

## Redes e portas do Compose

A API participa das três redes. O worker fica só em `backend`. O PostgreSQL fica
só em `data`, sem porta publicada. Fonte:
[`diagrams/architecture.mmd`](diagrams/architecture.mmd).

```mermaid
architecture-beta
    group edge(internet)[rede edge]
    group backend(server)[rede backend]
    group data(database)[rede data interna]

    service browser(internet)[Navegador]
    service web(server)[web Nextjs] in edge
    service api(server)[API FastAPI] in edge
    service mtx(server)[MediaMTX] in edge
    service worker(server)[video worker] in backend
    service demo(server)[demo camera] in backend
    service pg(database)[PostgreSQL] in data
    service gemini(cloud)[Gemini ou fake]
    service snow(database)[Snowflake ou fake]
    service voice(internet)[ElevenLabs ou fake]
    service sol(internet)[Solana Devnet]
    junction ext

    browser:R --> L:web
    browser:B --> T:mtx
    web:B --> T:api
    demo:R --> L:mtx
    mtx:B --> T:worker
    worker:T --> B:api
    worker:R --> L:gemini
    api:B --> T:pg
    api:R --> L:ext
    ext:R --> L:snow
    ext:T --> B:voice
    ext:B --> T:sol
```

O banco não possui porta publicada. As portas públicas ligam em localhost por
padrão. `DOGSENSE_BIND_HOST=0.0.0.0` só deve ser usado após hardening de auth,
CORS/Origin e topologia WebRTC.

| Porta | Protocolo | Uso |
|---:|---|---|
| 3000 | HTTP | dashboard |
| 8000 | HTTP/WS | API, health e WebSocket |
| 8554 | RTSP/TCP | publicação/leitura local do relay |
| 8889 | HTTP | sinalização WHEP/WebRTC |
| 8189 | UDP | mídia WebRTC |
| 5432 | TCP interno | PostgreSQL, nunca publicado |
| 9997/9998 | HTTP interno | Control API/métricas do MediaMTX |

## Pipeline do video-worker

`VideoAnalysisWorker.run()` sobe três loops concorrentes: captura, agendamento
e inferência. A fila `LatestOnlyQueue` descarta janelas antigas enquanto uma
análise está em curso. Fonte:
[`diagrams/pipeline-flowchart.mmd`](diagrams/pipeline-flowchart.mmd).

```mermaid
flowchart TD
    Start(["VideoAnalysisWorker.run()"]) --> Capture["_capture_loop()"]
    Start --> Schedule["_schedule_loop()"]
    Start --> Inference["_inference_loop()"]

    Capture --> Source["OpenCVFrameSource.frames()"]
    Source -->|frame JPEG em memoria| Repeat{"fingerprint e JPEG repetidos?"}
    Repeat -->|nao| Buffer["FrameRingBuffer.append()"]
    Repeat -->|sim por stream_freeze_seconds| Unstable["engine.stream_unstable()"]
    Buffer -->|capacidade cheia| Drop["frames_dropped"]
    Source -->|excecao ou stream acabou| Offline["engine.camera_offline()"]
    Offline -->|backoff 1s a 30s| Capture

    Schedule --> Latest{"ultimo frame congelado?"}
    Latest -->|sim| Unstable
    Latest -->|nao| Sample["TemporalSampler.select()"]
    Sample -->|menos que min_frames| Skip["windows_insufficient"]
    Sample -->|6 a 8 frames em 4s| Queue["LatestOnlyQueue.put_latest()"]
    Queue -->|janela antiga descartada| DropWin["windows_dropped"]

    Inference --> GetWin["windows.get()"]
    GetWin --> Analyze["BehaviorAnalyzer.analyze()"]
    Analyze -->|GeminiAdapter ou DeterministicFakeAdapter| Stale{"sequence invalida?"}
    Stale -->|sim| Invalidate["invalidated_results"]
    Stale -->|nao| Engine["TemporalStateEngine.process()"]
    Engine -->|ANALYZING sem estado estavel| Hold["retorna None local"]
    Engine -->|snapshot consolidado| Publish["HttpAnalysisPublisher.publish()"]
    Analyze -->|InferenceError| Fail["engine.record_inference_failure()"]
    Fail -->|ainda abaixo de 10s| Hold
    Fail -->|degradation_seconds| Degraded["SERVICE_DEGRADED"]
    Degraded --> Publish
    Unstable --> Publish
    Offline --> Publish

    Publish --> Ingest["POST /api/v1/internal/analyses"]
    Ingest --> Store["MemoryStore/PostgresStore.ingest()"]
    Store -->|accepted| Hub["WebSocketHub.publish()"]
    Hub --> Dash["Dashboard WS /api/v1/live/dogs/:dog_id"]
    Store --> Outbox["eventos encerrados"]
    Outbox --> Jobs["integration_worker()"]
    Jobs --> Snow["Snowflake MERGE EVENT_ID"]
```

## Fluxo de uma atualização

1. MediaMTX recebe ou reconecta o stream `dog-camera`.
2. O worker mantém uma janela de quatro segundos, amostra até oito frames e
   descarta janelas antigas quando uma análise ainda está em curso.
3. O adaptador fake ou Google AI retorna `behavior-analysis-v1`.
4. O payload é validado; um resultado inválido não altera o estado.
5. O motor aplica média exponencial (`alpha=0,35`), confiança mínima, margem e
   duas observações consecutivas.
6. A API rejeita análises repetidas ou atrasadas por identidade/sequência.
7. PostgreSQL persiste estado, evento e outbox antes da publicação WebSocket.
8. Jobs fazem retry idempotente das integrações sem bloquear vídeo ou UI.

A sequência correspondente, incluindo WHEP no dashboard e o outbox, está em
[`diagrams/execution-sequence.mmd`](diagrams/execution-sequence.mmd).

```mermaid
sequenceDiagram
    autonumber
    actor Tutor as Navegador
    participant Web as Dashboard
    participant MTX as MediaMTX
    participant Cap as CaptureLoop
    participant Sch as ScheduleLoop
    participant Inf as InferenceLoop
    participant AI as GeminiOuFake
    participant Eng as TemporalStateEngine
    participant API as FastAPI
    participant Store as PostgresStore
    participant Hub as WebSocketHub
    participant Jobs as IntegrationWorker

    Tutor->>Web: abre localhost:3000
    Web->>MTX: WHEP POST /dog-camera/whep
    MTX-->>Web: SDP answer e trilha WebRTC
    Web->>API: GET /api/v1/dogs/:id/state/current
    Web->>API: WS /api/v1/live/dogs/:id
    API-->>Web: snapshot e recent_events

    MTX->>Cap: RTSP interno dog-camera
    Cap->>Cap: FrameRingBuffer.append(frame)
    Sch->>Sch: buffer.recent(4s)
    Sch->>Sch: TemporalSampler.select 2 fps
    Sch->>Inf: LatestOnlyQueue.put_latest(window)

    Inf->>AI: analyzer.analyze(window)
    alt timeout ou payload invalido
        AI-->>Inf: InferenceError
        Inf->>Eng: record_inference_failure()
        Note over Eng: apos 10s vira service_degraded
    else resultado behavior-analysis-v1
        AI-->>Inf: InferenceResult
        Inf->>Eng: process(analysis)
        Note over Eng: EWMA alpha=0.35, limiar 0.65, margem 0.10, 2 observacoes
    end

    Inf->>API: POST /api/v1/internal/analyses
    API->>Store: ingest(AnalysisIngest)
    alt analysis_id duplicado
        Store-->>API: accepted=false duplicate=true
    else transition_seq ou captured_at obsoleto
        Store-->>API: 409 IngestConflict
    else aceito
        Store->>Store: current_state e evento aberto ou fechado
        Store-->>API: IngestResponse
        API->>Hub: live_state_updated ou monitoring_status_updated
        Hub-->>Web: mensagem WS
    end

    opt evento encerrado e nao sincronizado
        Jobs->>Store: list_unsynced_events()
        Jobs->>Jobs: analytics.sync_event()
        Jobs->>Store: mark_event_synced()
    end
```

## Motor temporal

`TemporalStateEngine` separa status técnico de monitoramento e estado
comportamental estável. Candidatos só viram evento depois de EWMA
(`alpha=0,35`), limiar `0,65`, margem `0,10` e duas observações consecutivas.
Fonte: [`diagrams/temporal-state.mmd`](diagrams/temporal-state.mmd).

```mermaid
stateDiagram-v2
    [*] --> starting : VideoAnalysisWorker.run

    starting --> analyzing : frames validos no ring buffer
    analyzing --> camera_offline : captura falha ou stream acaba
    analyzing --> stream_unstable : freeze atinge stream_freeze_seconds
    analyzing --> dog_not_visible : cao ausente ou dogs_detected 0
    analyzing --> multiple_dogs_detected : dogs_detected maior que 1
    analyzing --> insufficient_visibility : quality ou body_visibility abaixo de 0.50
    analyzing --> service_degraded : falha de inferencia por 10s
    analyzing --> starting : worker.stop

    camera_offline --> analyzing : reconexao com backoff
    stream_unstable --> analyzing : frames novos e distintos
    dog_not_visible --> analyzing : visibilidade recuperada
    multiple_dogs_detected --> analyzing : um unico cao visivel
    insufficient_visibility --> analyzing : qualidade suficiente
    service_degraded --> analyzing : inferencia valida

    camera_offline --> starting : worker.stop
    stream_unstable --> starting : worker.stop
    service_degraded --> starting : worker.stop

    note right of camera_offline : fecha evento comportamental
    note right of stream_unstable : nao fecha o evento aberto
    note right of dog_not_visible : fecha evento apos 10s de ausencia

    state analyzing {
        [*] --> candidate_pending
        candidate_pending --> candidate_pending : score abaixo do limiar ou margem
        candidate_pending --> stable_state : 2 observacoes consecutivas
        stable_state --> candidate_pending : novo candidato qualificado
        stable_state --> stable_state : observe no evento aberto
        note right of stable_state : relaxed, engaged, alert, stress_signals ou indeterminate
        note right of candidate_pending : EWMA 0.35, limiar 0.65, margem 0.10
    }
```

## Fonte de verdade e consistência

- PostgreSQL é a fonte da timeline e do estado atual.
- O WebSocket é uma notificação; após reconectar, o cliente busca um snapshot.
- Snowflake é eventual e usa `MERGE` por `EVENT_ID`.
- Áudio usa chave de deduplicação por evento, idioma, voz, modelo e template.
- Receipt usa `(event_id, canonical_version, network)` e somente evento encerrado.
- Toda janela carrega `analysis_id`, `session_id` e `camera_id` para correlação.

## Modelo de dados

O domínio da API vive em memória no processo único e é persistido atomicamente
em `dogsense_api_snapshot.payload` (JSONB). Snowflake recebe só eventos
pseudonimizados, sem FK física para o PostgreSQL. Fonte:
[`diagrams/domain-er.mmd`](diagrams/domain-er.mmd).

```mermaid
erDiagram
    DOG ||--o{ CAMERA : possui
    DOG ||--o{ MONITORING_SESSION : inicia
    CAMERA ||--o{ MONITORING_SESSION : alimenta
    DOG ||--o| CURRENT_STATE : exibe
    DOG ||--o{ STATE_EVENT : acumula
    CAMERA ||--o{ STATE_EVENT : observa
    MONITORING_SESSION ||--o{ STATE_EVENT : correlaciona
    STATE_EVENT ||--o{ FEEDBACK : recebe
    STATE_EVENT ||--o{ SPEECH_ASSET : narra
    STATE_EVENT ||--o{ RECEIPT : assina
    MONITORING_SESSION ||--o{ PROCESSED_ANALYSIS : deduplica
    API_SNAPSHOT ||--|| DOG : serializa
    SF_STATE_EVENTS }o..o| STATE_EVENT : "MERGE por EVENT_ID"

    DOG {
        string id PK
        string name
        string timezone
        datetime created_at
        datetime updated_at
    }

    CAMERA {
        string id PK
        string dog_id FK
        string name
        boolean active
        string source_type
        string rtsp_url_redacted
        boolean has_credentials
        string encrypted_credentials
        string status
        datetime last_checked_at
    }

    MONITORING_SESSION {
        string id PK
        string dog_id FK
        string camera_id FK
        string status
        datetime started_at
        datetime stopped_at
    }

    CURRENT_STATE {
        string dog_id PK
        string camera_id FK
        string session_id FK
        string monitoring_status
        string activity_label
        string state_label
        float confidence
        int sequence
        datetime captured_at
    }

    STATE_EVENT {
        string id PK
        string dog_id FK
        string camera_id FK
        string session_id FK
        datetime started_at
        datetime ended_at
        string activity
        string state
        float confidence_avg
        float confidence_max
        datetime snowflake_synced_at
        int sample_count
    }

    FEEDBACK {
        string id PK
        string event_id FK
        boolean correct
        string corrected_state
        string comment
        datetime created_at
    }

    SPEECH_ASSET {
        string id PK
        string event_id FK
        string language
        string text
        string status
        datetime expires_at
        string error
    }

    RECEIPT {
        string id PK
        string event_id FK
        string network
        string canonical_version
        string event_hash
        string memo
        string transaction_signature
        string status
        string verification_status
    }

    PROCESSED_ANALYSIS {
        uuid analysis_id PK
        string session_id FK
        boolean accepted
        boolean duplicate
        int sequence
        string event_id
    }

    API_SNAPSHOT {
        boolean singleton PK
        json payload
        datetime updated_at
    }

    SF_STATE_EVENTS {
        string EVENT_ID PK
        string DOG_ID_HASH
        string CAMERA_ID_HASH
        string STATE
        string ACTIVITY
        float CONFIDENCE_AVG
        string EVENT_HASH
        datetime STARTED_AT
        datetime ENDED_AT
    }
```

## Modos e degradação

O modo demo combina MediaMTX e PostgreSQL reais com fonte sintética e adaptadores
fake. Cada integração pode ser promovida separadamente para `real`.

| Falha | Estado/ação | Continua funcionando |
|---|---|---|
| câmera/relay | `camera_offline`, reconexão com backoff | API, UI e histórico |
| stream congelado | `stream_unstable`, descarta frames antigos | UI e diagnóstico |
| IA timeout/inválida | retry único, depois `service_degraded` | vídeo e último snapshot por 10 s |
| Snowflake | outbox pendente com backoff | vídeo, estado e timeline PostgreSQL |
| ElevenLabs | mensagem textual e retry permitido | todo o fluxo visual |
| Solana | receipt `pending`/`failed` | evento e demais integrações |

## Limites de recursos

- uma análise simultânea por câmera;
- ring buffer e fila limitados;
- janela mais recente vence;
- logs rotacionados no Docker (`3 × 10 MiB` por serviço);
- cache de áudio em volume, TTL esperado de 24 horas;
- gravação de MediaMTX explicitamente desligada.

## Evolução

Na topologia futura, o Edge Agent permanece ao lado da câmera, conserva a
credencial RTSP localmente e envia apenas eventos/telemetria necessários por uma
conexão de saída. A API e os contratos atuais continuam sendo a fronteira entre
observação local e serviços remotos.
