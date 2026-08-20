.ONESHELL:

HOSTNAME_AUTHENTIK :=
HOSTNAME_METRICS :=

.PHONY: install
install:
	curl -L get.docker.com | sudo bash
	sudo usermod -a -G docker $(shell whoami)

.PHONY: authentik
authentik:
	uv run gen-blueprint.py -o authentik/blueprints/test-data.yaml -c runner/tests/users.json
	cd authentik
	echo "PG_PASS=$(openssl rand -base64 36 | tr -d '\n')" >> .env
	echo "AUTHENTIK_SECRET_KEY=$(openssl rand -base64 60 | tr -d '\n')" >> .env
	echo "AUTHENTIK_ERROR_REPORTING__ENABLED=true" >> .env
	docker compose pull
	docker compose up -d

.PHONY: metrics
metrics:
	cd metrics
	HOSTNAME_AUTHENTIK=$(HOSTNAME_AUTHENTIK) envsubst prometheus.yml
	docker compose pull
	docker compose up -d
