# Governed Edge AI — project-wide QA targets
#
# Canonical rule: every module under this repo must have a tests/ directory
# and must pass `make qa` before code is committed.
#
# Usage:
#   make smoke            fast sanity pass (all modules)
#   make test             full test + coverage (all modules)
#   make lint             ruff check (all Python, including S/security rules)
#   make typecheck        mypy static type checking (production files only)
#   make security         bandit SAST + pip-audit CVE scan
#   make qa               lint + typecheck + security + test  (CI gate)
#
#   Per-module variants: audit-{test,smoke,lint,typecheck,security}
#                        linux-{test,smoke,lint,typecheck,security}

PYTHON      := python3
AUDIT       := audit-service
LINUX       := linux-stack

.PHONY: smoke test lint typecheck security qa \
        audit-test audit-smoke audit-lint audit-typecheck audit-security \
        linux-test linux-smoke linux-lint linux-typecheck linux-security \
        _check-audit-deps _check-linux-deps _check-sec-deps

# ---------------------------------------------------------------------------
# Top-level targets (extend as modules are added)
# ---------------------------------------------------------------------------

smoke: audit-smoke linux-smoke

test: audit-test linux-test

lint: audit-lint linux-lint

typecheck: audit-typecheck linux-typecheck

security: audit-security linux-security

qa: lint typecheck security test

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
	@echo "==> audit-service: ruff lint (incl. security rules)"
	cd $(AUDIT) && $(PYTHON) -m ruff check .

audit-typecheck: _check-audit-deps
	@echo "==> audit-service: mypy type check"
	cd $(AUDIT) && $(PYTHON) -m mypy logger.py dashboard/app.py dashboard/models.py \
		--ignore-missing-imports --python-version 3.11

audit-security: _check-sec-deps
	@echo "==> audit-service: bandit SAST"
	cd $(AUDIT) && $(PYTHON) -m bandit -r . --exclude ./tests -ll -q
	@echo "==> audit-service: pip-audit CVE scan"
	pip-audit --requirement $(AUDIT)/requirements.txt

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
	@echo "==> linux-stack: ruff lint (incl. security rules)"
	cd $(LINUX) && $(PYTHON) -m ruff check .

linux-typecheck: _check-linux-deps
	@echo "==> linux-stack: mypy type check"
	cd $(LINUX) && $(PYTHON) -m mypy ipc/ perception/ \
		--ignore-missing-imports --python-version 3.11

linux-security: _check-sec-deps
	@echo "==> linux-stack: bandit SAST"
	cd $(LINUX) && $(PYTHON) -m bandit -r ipc/ perception/ -ll -q
	@echo "==> linux-stack: pip-audit CVE scan"
	pip-audit --requirement $(LINUX)/requirements.txt

# ---------------------------------------------------------------------------
# Security tool dep check
# ---------------------------------------------------------------------------

_check-sec-deps:
	@$(PYTHON) -m bandit --version >/dev/null 2>&1 || \
		(echo "Installing security tools..." && pip install -q bandit pip-audit mypy)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | \
		awk -F: '{printf "  make %-25s\n", $$1}'
