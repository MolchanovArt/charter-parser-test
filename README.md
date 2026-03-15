# Charter Parser — Codex Core

Минимальное, но рабочее ядро репозитория для поэтапной реализации generalized charter-party clause parser.

Цель репозитория:
- сохранить текущий baseline;
- быстро поднять **eval-driven** контур;
- начать с **geometry-first** пайплайна;
- использовать LLM только для **структурной разметки неоднозначных мест**;
- оставить VLM только как **селективный fallback**.

## Что уже есть

- `legacy/` — текущая baseline-реализация в двух скриптах.
- `artifacts/golden/clauses_merged.json` — seed reference set для regression checks.
- `src/charter_parser/` — минимальный unified scaffold.
- `schemas/` — схемы для output, page IR, layout profile, line selection, reports.
- `docs/` — краткие и жесткие repo-level правила для Codex.

## Главный принцип

Финальный `clause.text` всегда должен собираться **из исходных spans/lines**, а не генерироваться моделью с нуля.

Модель разрешена для:
- выбора line/block boundaries;
- title/body separation;
- ambiguous attach / split / merge decisions;
- selective page repair;
- visual fallback только на flagged pages.

## Быстрый старт

### 1. Скачай PDF

```bash
bash scripts/download_source_pdf.sh
```

### 2. Установи зависимости

```bash
uv sync
cp .env.example .env
```

или

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

### 3. Прогони baseline

```bash
make baseline
make validate
make eval
```

### 4. Сними геометрическую разведку

```bash
make probe
```

Это создаст:
- `artifacts/runs/latest/page_ir.jsonl`
- `artifacts/runs/latest/layout_profile.json`
- `artifacts/runs/latest/run_report.json`

### 5. Запусти Codex в корне репозитория

```bash
codex -m gpt-5.4
```

Первый запрос для Codex:

```text
Read README.md, AGENTS.md, TASK.md, EVAL.md, docs/architecture.md, docs/problem_cases.md and PLANS.md.
Then run make baseline, make validate, make eval, and make probe.
Summarize the current system, its metrics, and propose the smallest Milestone 1 implementation plan.
```

## Обязательные outputs каждого нормального run

Минимум:
- `artifacts/runs/latest/clauses.json`
- `artifacts/runs/latest/run_report.json`
- `artifacts/runs/latest/eval_report.json`
- `artifacts/runs/latest/eval_report.md`

При запуске геометрической разведки:
- `artifacts/runs/latest/page_ir.jsonl`
- `artifacts/runs/latest/layout_profile.json`

## Минимальный маршрут к финальному решению

1. Зафиксировать baseline и eval.
2. Собрать unified `PageIR` на PyMuPDF.
3. Добавить automatic geometric reconnaissance.
4. Собрать deterministic candidate generation.
5. Подключить `gpt-5.4` только для line/block adjudication.
6. Добавить selective visual fallback.
7. Перейти к active loop по grader failures.

Подробности в `PLANS.md`.
