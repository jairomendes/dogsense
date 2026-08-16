# Cenários determinísticos

O adaptador fake lê um manifesto:

```json
{
  "loop": true,
  "steps": [
    {
      "at_seconds": 0,
      "analysis": { "schema_version": "behavior-analysis-v1" }
    }
  ]
}
```

`at_seconds` é relativo ao início do adaptador. O último passo aplicável permanece
ativo até o próximo passo. Todo `analysis` precisa satisfazer o contrato completo;
o cenário não ignora validação.

- `demo-tour.json`: percurso narrativo completo;
- `relaxed-loop.json`: estado estável para smoke/UI;
- `stress-signals-loop.json`: persistência, voz e receipt.

Selecione pelo `.env` com o caminho interno do contêiner e reinicie somente o
worker. Cenários são dados sintéticos e não podem ser apresentados como resultado
do Google AI ou como interpretação do vídeo sintético.
