# Contributing

Thanks for helping. Issues and pull requests can be written in Chinese or English.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Requires Python 3.10 or newer.

## Tests

The test suite is offline: `tests/conftest.py` blocks outbound sockets.

```bash
pytest tests/ -q
ruff check .
```

With coverage:

```bash
pytest tests/ -q --cov=marvel --cov=web --cov=cli --cov-report=term-missing
```

Manual probes under `scripts/probe_*.py` call live APIs; some of them spend LLM tokens. They are not part of CI. Read the script header before running one.

## Style

- Keep changes small and focused.
- Add or update a regression test for every bug fix.
- Do not commit `.env`, API keys, account data or generated reports.
- Record user-visible behaviour changes in `CHANGELOG.md`.

## Commit messages

Keep them short: `type: 简短说明` (for example `fix: 修复新闻窗口为空`). Put details in the pull request, not in a long commit body.

Do not put private information (names, keys, account data) into commit messages or diffs.

## Pull requests

- Describe the problem and the fix.
- List the commands you ran.
- Update tests and docs when behaviour changes.
- By submitting, you confirm the contribution can be distributed under this repository's licences: Apache-2.0 plus PolyForm-Noncommercial-1.0.0 (see `LICENSING.md`).
