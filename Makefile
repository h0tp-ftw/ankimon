# Ankimon dev harness — one-command setup, verify, and tooling.
# Dev-only: the shipped .ankiaddon is built from src/Ankimon/ only; none of this ships.
.DEFAULT_GOAL := help
.PHONY: help setup check doctor tier2

help: ## show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  make %-8s %s\n", $$1, $$2}'

setup: ## one-time: fetch the poke_engine submodule (Tier-1 needs nothing else)
	git submodule update --init --recursive

check: setup ## run the full Tier-1 harness gate (no Anki/Qt) — exactly what CI runs
	python3 harness/check.py

doctor: ## diagnose the dev environment (python version, submodule)
	python3 harness/check.py --doctor

tier2: ## one-time: build the offscreen-Qt env for Tier-2 (real windows)
	bash harness/setup_tier2.sh
