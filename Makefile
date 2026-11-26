.PHONY: lint test check

lint:
	ruff check .
	shellcheck install.sh

test:
	python3 -m pytest -q

check: lint test
