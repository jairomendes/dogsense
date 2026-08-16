SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

COMPOSE ?= docker compose
API_URL ?= http://127.0.0.1:8000

.PHONY: help setup config build up demo down logs ps health smoke migrate seed test lint psql preflight-real

help: ## Lista os comandos disponíveis.
	@awk 'BEGIN {FS = ":.*## "; printf "DogSense Live\n\n"} /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Cria .env local sem sobrescrever arquivos existentes e valida o Compose.
	@bash scripts/bootstrap.sh

config: ## Valida e renderiza silenciosamente a configuração Compose.
	@$(COMPOSE) config --quiet

build: ## Constrói as imagens locais.
	@$(COMPOSE) build

up: setup ## Sobe o stack padrão em segundo plano (sem câmera sintética).
	@$(COMPOSE) up --build --detach
	@bash scripts/wait-for-health.sh "$(API_URL)/health/ready" 60

demo: ## Sobe demo determinística com câmera sintética e integrações fake.
	@bash scripts/demo.sh

down: ## Para os contêineres; preserva banco e cache de áudio.
	@$(COMPOSE) --profile controlled-video down

logs: ## Acompanha logs de todos os serviços.
	@$(COMPOSE) --profile controlled-video logs --follow --tail=200

ps: ## Mostra o estado dos serviços.
	@$(COMPOSE) --profile controlled-video ps

health: ## Consulta readiness da API sem imprimir credenciais.
	@curl --silent --show-error --fail "$(API_URL)/health/ready"
	@echo

smoke: ## Valida endpoints básicos do stack em execução.
	@bash scripts/smoke.sh

migrate: ## Aplica o schema PostgreSQL idempotente do MVP.
	@$(COMPOSE) exec -T postgres sh -c 'psql --set ON_ERROR_STOP=1 --username "$$POSTGRES_USER" --dbname "$$POSTGRES_DB" --file /docker-entrypoint-initdb.d/01_api_snapshot.sql'

seed: ## Reaplica o seed idempotente da demonstração.
	@$(COMPOSE) run --rm api python -m app.seed

test: ## Executa as suítes dos três componentes em contêineres.
	@status=0; \
	$(COMPOSE) run --rm --no-deps api pytest -q || status=1; \
	$(COMPOSE) run --rm --no-deps video-worker pytest -q || status=1; \
	$(COMPOSE) run --rm --no-deps web npm test || status=1; \
	exit $$status

lint: ## Executa verificações estáticas disponíveis.
	@status=0; \
	$(COMPOSE) run --rm --no-deps api ruff check app tests || status=1; \
	$(COMPOSE) run --rm --no-deps video-worker ruff check app tests || status=1; \
	$(COMPOSE) run --rm --no-deps web npm run lint || status=1; \
	exit $$status

psql: ## Abre psql dentro do contêiner, sem publicar a porta do banco.
	@$(COMPOSE) exec postgres psql -U "$${POSTGRES_USER:-dogsense}" -d "$${POSTGRES_DB:-dogsense}"

preflight-real: ## Confere se integrações marcadas como real têm configuração mínima.
	@bash scripts/preflight-real.sh
