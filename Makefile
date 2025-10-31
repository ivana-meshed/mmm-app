.PHONY: install-dev format format-check lint typecheck check fix test clean ci

# Config
PY_PATHS = app scripts
LINE_LEN = 80

# Dev env
install-dev:
	pip install --upgrade pip
	# adjust if you use Poetry/UV/etc.
	@if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi
	@if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

# Format (local, mutating)
format:
	black --line-length $(LINE_LEN) .
	isort --profile black --line-length $(LINE_LEN) .

# Format (CI, non-mutating)
format-check:
	black --line-length $(LINE_LEN) --check --diff .
	isort --profile black --line-length $(LINE_LEN) --check-only .

# Linting
lint:
	pylint $(PY_PATHS)/ --max-line-length $(LINE_LEN)
	flake8 $(PY_PATHS)/ --max-line-length $(LINE_LEN)

# Type checking
typecheck:
	mypy $(PY_PATHS)/

# Tests
test:
	pytest tests/ -v --cov=app --cov-report=html

# Aggregate checks
check: format-check lint typecheck
fix: format check

# CI one-liner
ci: install-dev check test

# Clean build artifacts
clean:
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete
	rm -rf .mypy_cache .pytest_cache htmlcov
