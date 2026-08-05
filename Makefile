# Governed Edge AI — project-wide QA targets
#
# Canonical rule: every module under this repo must have a test/ directory
# and must pass `make test` before code is committed.
#
# Usage:
#   make smoke            fast sanity pass (all modules)
#   make test             full test + coverage (all modules)
#   make lint             ruff check (all Python modules)
#   make qa               lint + test (CI gate)
#   make audit-test       audit-service only
#   make audit-smoke      audit-service smoke only
#   make audit-lint       audit-service lint only
#   make linux-test       linux-stack only
#   make linux-smoke      linux-stack smoke only
#   make linux-lint       linux-stack lint only

PYTHON      := python3
AUDIT       := audit-service
LINUX       := linux-stack

.PHONY: smoke test lint qa \
        audit-test audit-smoke audit-lint \
        linux-test linux-smoke linux-lint \
        _check-audit-deps _check-linux-deps

# ---------------------------------------------------------------------------
# Top-level targets (extend as modules are added)
# ---------------------------------------------------------------------------

smoke: audit-smoke linux-smoke

test: audit-test linux-test

lint: audit-lint linux-lint

qa: lint test

# ---------------------------------------------------------------------------
# audit-service
# ---------------------------------------------------------------------------

_check-audit-deps:
	@cd $(AUDIT) && $(PYTHON) -m pytest --version >/dev/null 2>&1 || \
		(echo "Installing audit-service deps..." && pip install -q -r requirements.txt)

audit-smoke: _check-audit-deps
	@echo "==> audit-service: smoke tests"
	cd $(AUDIT) && $(PYTHON) -m pytest -m smoke -v --no-cov

audit-test: _check-audit-deps
	@echo "==> audit-service: full test suite + coverage"
	cd $(AUDIT) && $(PYTHON) -m pytest

audit-lint: _check-audit-deps
	@echo "==> audit-service: ruff lint"
	cd $(AUDIT) && $(PYTHON) -m ruff check . --exclude tests/

# ---------------------------------------------------------------------------
# linux-stack
# ---------------------------------------------------------------------------

_check-linux-deps:
	@cd $(LINUX) && $(PYTHON) -m pytest --version >/dev/null 2>&1 || \
		(echo "Installing linux-stack deps..." && pip install -q -r requirements.txt)

linux-smoke: _check-linux-deps
	@echo "==> linux-stack: smoke tests"
	cd $(LINUX) && $(PYTHON) -m pytest -m smoke -v --no-cov

linux-test: _check-linux-deps
	@echo "==> linux-stack: full test suite + coverage"
	cd $(LINUX) && $(PYTHON) -m pytest

linux-lint: _check-linux-deps
	@echo "==> linux-stack: ruff lint"
	cd $(LINUX) && $(PYTHON) -m ruff check ipc/ --exclude tests/

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | \
		awk -F: '{printf "  make %-20s\n", $$1}'
