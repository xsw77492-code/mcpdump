# Contributing to mcpdump

Thanks for your interest. This document describes the rules that keep the project
small, correct and easy to review. They are not optional decoration — several of
them are enforced by the test suite.

## The project in one paragraph

`mcpdump` is a terminal toolkit for MCP servers. It speaks JSON-RPC 2.0 over stdio
and Streamable HTTP, and has **zero MCP SDK dependency**: the wire format, the
transport and the session handling are implemented in this repository. The layout
is enforced by an AST check (`tests/test_architecture.py`) rather than by a
convention that only lives in prose:

```
core (jsonrpc / transport / session / proxy)
  → ui
    → services (checks/ runs in the order of its numeric filename prefixes)
      → commands
        → cli
```

Seven files sit at the top level: `cli`, `runtime`, `exits`, `i18n`, `demo`,
`__init__` and `__main__`.

## Setting up

```bash
pip install -e ".[dev]"
```

The runtime dependencies are `typer` and `rich` only. `src/mcpdump/demo.py` is
pure standard library and imports nothing from the package — a test enforces this,
because it is the known-good server the conformance suite runs against.

## The quality gates

Three checks must pass before a change is worth proposing. CI runs the same three.

```bash
ruff check src tests      # E/F/I/UP/B, line length 100
mypy                      # --strict, python_version = "3.10"
pytest                    # the whole suite
```

Two of them are stricter than they look:

- `mypy` checks `tests/` too, under the **lowest** supported Python version
  (`3.10`), not your local one. Claiming `requires-python = ">=3.10"` and then
  checking against 3.13 would make the claim meaningless.
- On this project, pytest must run with an explicit basetemp on some machines
  (see the note below), otherwise the suite can exit non-zero with no failures.

## The rules that are tested, not just written

Every rule below has a matching test. A rule that exists only in this document
will be broken — that is the whole point.

- **Every behaviour is a test.** If you add or change behaviour, add or update a
  test in the same change.
- **The layer boundaries are checked by AST**, not by import review. Do not add an
  import that crosses a forbidden edge (`ui` must never import `services`, etc.).
- **The bundled example server lives inside the package.** There is no `examples/`
  directory. Tests launch it the same way the README tells users to launch it
  (`python -m mcpdump.demo`), with the launch form defined in one place
  (`tests/conftest.py`, `DEMO_SERVER_ARGS`).
- **User-facing text goes through `src/mcpdump/i18n.py`.** The interface is
  English by default and switches to Chinese via `MCPDUMP_LANG=zh`. A hardcoded
  string that bypasses the table will show English users Chinese text.
- **Exit codes are a contract**: `0` success, `1` usage, `2` server error
  (`check` and `diff`), `3` timeout, `4` environment.
- **`stdout-purity` is the last check** (its filename prefix ends the sequence).
  Do not reorder the checks by renaming files casually.

## Commit messages

Commits follow `feat: <package> v<version> — <topic>` with a structured Chinese
body (the changelog and commit history are written in Chinese, while code and
tests are English). See the existing history for the exact shape.

## Language

- Code and tests: English.
- `README.md` (primary) and `README.zh-CN.md` are kept in sync.
- `CHANGELOG.md` and commit bodies: Chinese.

## A note on running the tests locally

On some sandboxed/Windows environments, pytest's own cleanup of its temporary
directory is blocked and the run exits `1` with **zero failed tests** — a false
alarm. Pass an explicit basetemp inside the repository:

```bash
pytest --basetemp=.pytest_basetemp/run
```

Use a fresh directory name each run; a basetemp that already holds many files can
trigger `ERROR at setup` on every `tmp_path` test instead.

## Reporting issues

Use the issue templates. Include the `mcpdump` version, your OS and Python version,
and — when relevant — a `record`ing of the session (`mcpdump record --out
session.jsonl '<server command>'`), which is the most useful artifact you can
attach: replaying it reproduces exactly what you saw.
