# Repository Guidelines

## Project Structure & Module Organization
- CLI entrypoint `novadovetail.py` wires config, validation, planning, and hardware backends.
- Core math in `geometry.py`; plans and command sequencing in `planner.py`; shared dataclasses in `model.py`.
- Hardware abstractions live in `hardware.py` (`DummyLaser`/`DummyRotary` by default, Ruida/rotary skeletons for real hardware); logging helpers in `logging_utils.py`.
- Config parsing and CLI overrides are in `config.py`; reference config lives in `example-config.toml`. Per-setup config should be `config.toml` (git-ignored).
- Tests live under `tests/` (currently `tests/test_geometry.py`); add new suites alongside the module under test.

## Build, Test, and Development Commands
- Use Python 3.11+; create a venv (`python3 -m venv .venv && source .venv/bin/activate`) and install dev deps (`pip install pytest`; `tomli` for <3.11).
- Dry-run the planner to inspect generated commands without touching hardware:  
  `python3 novadovetail.py --config example-config.toml --mode both --dry-run`
- Copy the sample config when starting: `cp example-config.toml config.toml`, then adjust to your jig and machine; CLI flags override TOML values.
- Run the full test suite: `python -m pytest tests`; target a single check with `python -m pytest tests/test_geometry.py::test_tail_layout_basic`.

## Coding Style & Naming Conventions
- Follow existing Python style: 4-space indents, snake_case functions/variables, CamelCase classes/dataclasses, and concise docstrings that explain “why”.
- Keep `geometry.py` pure and deterministic; avoid side effects so it stays easy to test.
- Prefer type hints and dataclasses for shared params; log hardware actions rather than printing.
- Leave hardware defaults on dummy backends unless you are actively integrating a controller; guard any real I/O behind flags/config.

## Testing Guidelines
- Add pytest cases for new geometry, planning branches, and validation edge cases; cover both happy path and common misconfigurations.
- When adding calculations, assert numeric tolerances (e.g., `abs(value) < 1e-9`) to match existing patterns.

## Commit & Pull Request Guidelines
- Match the history’s style: short, imperative, capitalized subjects (e.g., “Enhance configuration and backend handling”).
- In PRs, state scope, configs used, and test commands run; call out hardware-impacting changes and whether `backend.use_dummy` was true.
- Update `README.md` or `example-config.toml` when behavior or defaults change; include dry-run output excerpts only when they clarify behavior.

## Safety & Configuration Tips
- Never ship real machine credentials or IPs; keep Ruida host/port placeholders. Test dangerous changes with `--dry-run` first.
- Validate inputs before executing plans (`validation.py` covers core checks); extend it when adding new parameters or motion types.
