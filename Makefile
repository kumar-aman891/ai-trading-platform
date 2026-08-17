.PHONY: setup format format-check lint typecheck arch-check no-live-check \
        test test-safety test-integration coverage precommit ci clean

# Every gate here is what CI runs too — the workflow file is a thin wrapper
# over these targets (see .github/workflows/ci.yml).

setup:
	uv sync --all-packages --group dev

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

typecheck:
	uv run mypy

arch-check: no-live-check
	uv run lint-imports

# import-linter can't express "no module matching *live* may be imported"
# against a module that doesn't exist yet (ADR-005) — this grep is the
# mechanical stand-in until execution/live/ is deliberately created in a
# later phase, at which point it becomes a real import-linter contract.
no-live-check:
	@echo "Checking no execution/live/ package exists..."
	@if [ -d execution/live ]; then \
		echo "FAIL: execution/live/ exists — LIVE execution must not be implemented"; \
		exit 1; \
	fi
	@echo "OK: execution/live/ does not exist"

test:
	uv run pytest

test-safety:
	uv run pytest tests/safety -m safety --no-header

# Requires Docker - brings up docker-compose.test.yml, runs tests/integration/db,
# tears the stack down again. See tests/integration/README.md.
test-integration:
	sh ops/scripts/run_integration_tests.sh

coverage:
	uv run pytest --cov --cov-report=term-missing

precommit:
	uv run pre-commit run --all-files

ci: format-check lint typecheck arch-check test-safety coverage

clean:
	rm -rf .venv .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +
