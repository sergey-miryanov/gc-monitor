.PHONY: test build install

test:
	pytest

build:
	python -m build

install:
	pip install -e .
