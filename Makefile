.PHONY: install test lint typecheck format clean docker-build

install:
	pip install -r requirements.txt
	pip install pytest ruff mypy

test:
	python -m pytest tests/ -v

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy km140f/km140f.py

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

docker-build:
	docker build -f km140f/Dockerfile -t ha-addon-km140f km140f/

all: lint typecheck test
