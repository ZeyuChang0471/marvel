# Releasing

## One-time setup

1. Create the PyPI project `marvel-astock` and configure a trusted publisher:
   - owner: `ZeyuChang0471`
   - repository: `marvel`
   - workflow: `release.yml`
2. Set the repository variable `PUBLISH_TO_PYPI=true` when PyPI publishing is ready.
   Without it, the workflow still builds the distributions and creates the GitHub Release.

## Release checklist

1. Update `version` in `pyproject.toml`.
2. Move the `Unreleased` section in `CHANGELOG.md` to `## [<version>] — YYYY-MM-DD`.
3. Add `docs/releases/v<version>.md`.
4. Run:

   ```bash
   pytest tests/ -q
   ruff check .
   ```

5. Commit, tag and push:

   ```bash
   git tag -a v<version> -m "MARVEL v<version>"
   git push origin main
   git push origin v<version>
   ```

6. The `Release` workflow checks the tag against `pyproject.toml`, builds sdist/wheel,
   runs `twine check`, creates the GitHub Release, and publishes to PyPI when enabled.

## Local build

```bash
python -m pip install build twine
python -m build
twine check dist/*
```
