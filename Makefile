PDF ?= data/raw/voyage-charter-example.pdf
OUT ?= artifacts/runs/latest/clauses.json
GOLDEN ?= artifacts/golden/clauses_merged.json

.PHONY: setup download baseline unified probe validate eval test fmt

setup:
	uv sync --extra dev

download:
	bash scripts/download_source_pdf.sh

baseline:
	mkdir -p artifacts/runs/latest
	uv run python -m charter_parser.cli baseline --pdf $(PDF) --out $(OUT)

unified:
	mkdir -p artifacts/runs/latest
	uv run python -m charter_parser.cli unified --pdf $(PDF) --out artifacts/runs/latest/clauses_unified.json

probe:
	mkdir -p artifacts/runs/latest
	uv run python -m charter_parser.cli probe --pdf $(PDF)

validate:
	uv run python -m charter_parser.cli validate --json $(OUT)

eval:
	uv run python scripts/eval_against_reference.py --golden $(GOLDEN) --candidate $(OUT)

test:
	uv run --extra dev pytest -q

fmt:
	uv run python -m compileall src tests scripts
