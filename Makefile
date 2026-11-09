.PHONY: install test lint run docker clean

install:
	pip install -e ".[dev]"

test:
	python -m pytest

lint:
	python -m pyflakes src tests
	python -m compileall -q src

run:
	python -m polarion_mcp_2023 --transport stdio

http:
	python -m polarion_mcp_2023 --transport streamable-http --host 0.0.0.0 --port 8000

docker:
	docker build -t polarion-mcp-2023 .

clean:
	rm -rf .pytest_cache build dist *.egg-info src/*.egg-info polarion_attachments
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
