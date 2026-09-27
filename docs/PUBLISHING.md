# Publishing mcpdump to PyPI

`mcpdump` is published on PyPI under the name `mcpdump`. This document is the
checklist for a release. It is written so a first-time publisher can follow it
without guessing.

## One-time setup

1. Register an account at <https://pypi.org/account/register/> and verify the email.
2. Create an API token: **Account Settings → API tokens → Add API token**.
   - Name: anything you like, e.g. `mcpdump-publish`.
   - Scope: **Entire account** (the project does not exist on PyPI yet, so it
     cannot be selected from the project list on the first release).
3. Copy the token (`pypi-AgEIcH...`). **It is shown once** — store it in a
   password manager. Never commit it, and never paste it into a chat or a
   screenshot.

## Every release

1. Make sure the working tree is clean and the quality gates pass:

   ```bash
   ruff check src tests
   mypy
   pytest --basetemp=.pytest_basetemp/run
   ```

2. Bump `version` in `pyproject.toml` (PyPI is append-only: a version, once
   uploaded, can never be re-uploaded or deleted).
3. Update `CHANGELOG.md`, commit and tag:

   ```bash
   git add -A && git commit -m "release: mcpdump v<version>"
   git tag "v<version>"
   ```

4. Build the distributions:

   ```bash
   python -m build
   ```

   This produces `dist/mcpdump-<version>.tar.gz` and
   `dist/mcpdump-<version>-py3-none-any.whl`.

5. Upload:

   ```bash
   python -m twine upload dist/*
   ```

   When prompted, use username `__token__` (literally, including the two
   underscores) and paste the `pypi-AgEIcH...` token as the password.

6. Verify:

   ```bash
   pip install mcpdump
   mcpdump demo
   ```

## Pitfalls

- **Same version twice**: PyPI refuses a version that already exists. Always bump
  `version` before uploading.
- **Missing `build`/`twine`**: `pip install build twine`.
- **Local PyPI mirror**: this machine cannot reach `pypi.org` directly, so
  installs here use `-i https://pypi.tuna.tsinghua.edu.cn/simple`. That only
  affects *installing* packages; `twine upload` targets PyPI directly and is not
  affected.
- **Narrow-scope token after the first release**: once `mcpdump` exists on PyPI,
  create a second token scoped to the `mcpdump` project only and use that for
  future uploads. It is safer than an entire-account token.
