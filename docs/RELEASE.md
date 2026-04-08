# Releases, GitHub, and PyPI

## Day-to-day GitHub flow

1. Work on a branch (feature or fix).
2. Open a **Pull Request** against `main`.
3. After review, **merge** (squash or merge commit per team preference).
4. Ensure CI or local checks pass: `python -m build`, `mailspray -V`, targeted `-k` probes in your lab if you change protocol code.

## Version bumps

Single source: `mailspray/__init__.py` (`__version__`). Hatch reads it from [pyproject.toml](../pyproject.toml) (`[tool.hatch.version]`).

After bumping, commit with a clear message and tag if you ship a release.

## Git tags and GitHub Releases

```bash
git tag -a v0.5.0 -m "v0.5.0"
git push origin v0.5.0
```

On GitHub: **Releases** → **Draft a new release** → attach the tag, paste changelog.

## PyPI (optional)

Publishing makes `pip install mailspray` and `pipx install mailspray` work without a path to the repo.

Typical flow:

1. `python -m build`
2. Upload with `twine upload dist/*` (PyPI API token configured).
3. Verify on [pypi.org](https://pypi.org).

Until published, install from git:

```bash
pipx install git+https://github.com/OWNER/mailspray.git
# or from a checkout:
pipx install .
```

Replace `OWNER` with your GitHub org or user.
