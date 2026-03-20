.PHONY: test build install typecheck typecheck-pyright typecheck-mypy

test:
	pytest

build:
	python -m build

install:
	pip install -e .

typecheck: typecheck-pyright typecheck-mypy

typecheck-pyright:
	poetry run pyright

typecheck-mypy:
	poetry run mypy src/ tests/
