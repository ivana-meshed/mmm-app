.PHONY: format lint check test

# Format code with Black and isort
format:
	black --line-length 80 .
	isort --profile black --line-length 80 .

# Run linting
lint:
	pylint app/ scripts/ --max-line-length 80
	flake8 app/ scripts/ --max-line-length 80

# Type checking
typecheck:
	mypy app/ scripts/

# Run all checks
check: lint typecheck

# Format and check
fix: format check

# Run tests
test:
	pytest tests/ -v --cov=app --cov-report=html

# Clean build artifacts
clean:
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete
	rm -rf .mypy_cache .pytest_cache htmlcov
