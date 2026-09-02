.PHONY: help test build install typecheck typecheck-pyrefly typecheck-mypy

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

test: ## Run tests
	pytest

build: ## Build the package
	python -m build

install: ## Install the package in editable mode
	pip install -e .

typecheck: typecheck-pyrefly typecheck-mypy ## Run all type checks

typecheck-pyrefly: ## Run type checking with Pyrefly
	poetry run pyrefly check

typecheck-mypy: ## Run type checking with mypy
	poetry run mypy src/ tests/
