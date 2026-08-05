# Governed Edge AI — project-wide QA targets
#
# Canonical rule: every module under this repo must have a test/ directory
# and must pass `make test` before code is committed.
#
# Usage:
#   make smoke          fast sanity pass (all modules)
#   make test           full test + coverage (all modules)
#   make lint           ruff check (all Python modules)
#   make qa             lint + test (CI gate)
#   make audit-test     audit-service only
#   make audit-smoke    audit-service smoke only
#   make audit-lint     audit-service lint only

PYTHON   := python3
AUDIT    := audit-service

.PHONY: smoke test lint qa audit-test audit-smoke audit-lint _check-deps

# ---------------------------------------------------------------------------
# Top-level targets (extend as modules are added)
# ---------------------------------------------------------------------------

smoke: audit-smoke

test: audit-test

lint: audit-lint

qa: lint test

# ---------------------------------------------------------------------------
# audit-service
# ---------------------------------------------------------------------------

_check-deps:
	@cd $(AUDIT) && $(PYTHON) -m pytest --version >/dev/null 2>&1 || \
		(echo "Installing test deps..." && pip install -q -r requirements.txt)

audit-smoke: _check-deps
	@echo "==> audit-service: smoke tests"
	cd $(AUDIT) && $(PYTHON) -m pytest -m smoke -v --no-cov

audit-test: _check-deps
	@echo "==> audit-service: full test suite + coverage"
	cd $(AUDIT) && $(PYTHON) -m pytest

audit-lint: _check-deps
	@echo "==> audit-service: ruff lint"
	cd $(AUDIT) && $(PYTHON) -m ruff check . --exclude tests/

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | \
		awk -F: '{printf "  make %-20s\n", $$1}'
