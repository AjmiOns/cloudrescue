.PHONY: help init up down logs ps test lint incidents demo chaos-unhealthy chaos-hang chaos-crash chaos-stop tf-init tf-plan tf-apply tf-destroy
.DEFAULT_GOAL := help

help: ## Show this help
	grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-18s %s\n", $$1, $$2}'

init: ## Create .env with random secrets
	scripts/init-env.sh

up: init ## Build and start the stack
	docker compose up -d --build

down: ## Stop the stack (keeps volumes)
	docker compose down

logs: ## Follow monitor + guard logs
	docker compose logs -f monitor docker-guard

ps: ## Show container status
	docker compose ps

test: ## Run unit tests
	python -m pytest -m "not e2e"

lint: ## Lint Python code
	python -m ruff check .

incidents: ## Show recent incidents
	docker compose exec -T monitor python cli.py incidents

demo: ## Break the service and watch it heal
	scripts/demo.sh unhealthy

chaos-unhealthy: ## /health returns 503
	scripts/chaos.sh unhealthy

chaos-hang: ## /health times out
	scripts/chaos.sh hang

chaos-crash: ## process exits
	scripts/chaos.sh crash

chaos-stop: ## container stopped
	scripts/chaos.sh stop

tf-init: ## terraform init
	terraform -chdir=infrastructure/terraform init

tf-plan: ## terraform plan
	terraform -chdir=infrastructure/terraform plan -out=tfplan

tf-apply: ## terraform apply (uses the saved plan)
	terraform -chdir=infrastructure/terraform apply tfplan

tf-destroy: ## terraform destroy
	terraform -chdir=infrastructure/terraform destroy
