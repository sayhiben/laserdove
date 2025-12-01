# Repository Guidelines

## Project Structure & Module Organization
- Source code lives under `src/laserdove/`; run with `python -m laserdove.main`.
- Runtime code must not import from `reference/`; treat that directory as read-only background.
- Keep optional UI deps (Tk) and hardware deps lazy-imported within simulation/hardware paths so headless/dummy runs keep working.
- CLI entrypoint `cli.py` (python -m laserdove.cli or -m laserdove.main) wires config, validation, planning, and hardware backends.
- Config parsing and CLI overrides are in `config.py`; reference config lives in `example-config.toml`. Per-setup config should be `config.toml` (git-ignored).
- Core math in `geometry.py`; plans and command sequencing in `planner.py`; shared dataclasses in `model.py`.
- Hardware abstractions live under `src/laserdove/hardware/`: `base.py` (interfaces/dummy/executor), `sim.py` (Tk viewer backends), `ruida_*` + `rd_builder.py` (UDP transport + RD job builder), and `rotary.py` (logging/GPIO rotary drivers). `simulation_viewer.py` powers the Tk view; logging helpers stay in `logging_utils.py`.
- RD opcode table is centralized in `src/laserdove/hardware/rd_commands.py` (shared by runtime and parser); avoid defining command labels elsewhere.
- Tests live under `tests/` (currently `tests/test_geometry.py`); add new suites alongside the module under test.

## Build, Test, and Development Commands
- Use Python 3.11+; create a venv (`python3 -m venv .venv && source .venv/bin/activate`) and install dev deps (`pip install pytest`; `tomli` for <3.11).
- Run `python -m pytest tests` (and `make test`/`make lint` where available) before publishing changes; do not skip failing checks.
- Copy the sample config when starting: `cp example-config.toml config.toml`, then adjust to your jig and machine; CLI flags override TOML values.
- Dry-run the planner to inspect generated commands without touching hardware:  
  `python3 -m laserdove.main --config example-config.toml --mode both --dry-run`
- Tk simulation (visual, real-time pacing):  
  `python3 -m laserdove.main --config example-config.toml --simulate`
- Save swizzled RD jobs for inspection: add `--save-rd-dir rd_out/` (works with dry-run or live Ruida).
- Reset-only to park the machine with the beam off: `python3 -m laserdove.main --reset`
- Run the full test suite: `python -m pytest tests`; target a single check with `python -m pytest tests/test_geometry.py::test_tail_layout_basic`.

## Coding Style & Naming Conventions
- Keep `geometry.py` pure and deterministic; avoid side effects so it stays easy to test.
- Leave hardware defaults on dummy backends unless you are actively integrating a controller; guard any real I/O behind flags/config.
- Ruida specifics: RD jobs now anchor to the machine’s current origin (captured at run start) and still emit absolute coordinates; movement-only/reset strips cut commands (zero-power cuts become travel). Air assist is configurable and defaults on. Status readiness falls back to “activity then stable” using position/status polling when busy bits never assert.
- Status probe helper: `tools/ruida_status_probe.py` defaults to dual-socket polling (action vs status sockets) to avoid reply mix-ups; use it to map status bits without panel interaction.
- Prefer type hints and dataclasses for shared params; log hardware actions rather than printing.
- Follow existing Python style: 4-space indents, snake_case functions/variables, CamelCase classes/dataclasses, and concise docstrings that explain “why”.
- Planner/geometry outputs must remain deterministic; if randomness is introduced, seed explicitly and test it.
- When adding config or CLI flags, update argparse help, defaults in `config.py`, validation coverage, and docs (README/index/AGENTS).
- Guard GPIO/Tk/UDP imports so non-hardware environments remain usable; provide clear fallbacks/logs on import failure.

## Testing Guidelines
- Add pytest cases for new geometry, planning branches, and validation edge cases; cover both happy path and common misconfigurations.
- When adding calculations, assert numeric tolerances (e.g., `abs(value) < 1e-9`) to match existing patterns.
- For changes that alter command sequencing, add or update deterministic/snapshot-like assertions to prevent silent regressions.
- Exercise both dummy/simulated paths and Ruida path toggles in tests when feasible (movement_only vs powered).

## Commit & Pull Request Guidelines
- Always review `AGENTS.md`, `README.md`, and `reference/index.html` for project and reference context before answering prompts.
- Match the history’s style: short, imperative, capitalized subjects (e.g., “Enhance configuration and backend handling”).
- In PRs, state scope, configs used, and test commands run; call out hardware-impacting changes and whether `backend.use_dummy` was true.
- Update `README.md` or `example-config.toml` when behavior or defaults change; include dry-run output excerpts only when they clarify behavior.
- Any new file added under `reference/` must be accompanied by an entry in `reference/index.html`, keeping the table’s current columns intact.
- PDF->Markdown conversion tool lives at `tools/pdf_to_md.py` (uses PyMuPDF). Run `python tools/pdf_to_md.py --root reference` to regenerate .md and images; filters header/footer and tiny images by default.
- Always review `AGENTS.md`, `README.md`, and `reference/index.html` for project and reference context before answering prompts.
- Note hardware-impacting commits explicitly (e.g., Ruida transport, rotary GPIO) and include whether tests were run against dummy/sim or real devices.
- If touching RD opcode handling (`rd_commands.py`), update dependent tools (parsers/visualizers) and document any new labels and defaults.

## Safety & Configuration Tips
- Never ship real machine credentials or IPs; keep Ruida host/port placeholders. Test dangerous changes with `--dry-run` first.
- Do not send jobs to real hardware until a dry-run, simulation, and at least one movement-only pass have completed cleanly with the intended config.
- Use `--movement-only`/`--reset` to force travel-only RD jobs with power 0; `--save-rd-dir` is helpful for inspecting what would be sent.
- Validate inputs before executing plans (`validation.py` covers core checks); extend it when adding new parameters or motion types.
- RD File Inspection
  - You can design in LightBurn, export the generated `.rd` file, and decode it locally (unswizzle with magic 0x88) to inspect layer settings and embedded commands (e.g., Z offsets via 0x80 0x03).
  - When validating RD generation, a quick path is to `--save-rd-dir`, then decode with `tools/rd_parser.py` (Z offsets, bbox, speeds) or visualize with `tools/rd_visualize.py` to compare the emitted Ruida commands against the planned moves.
- When modifying rotary/pin mappings, default to safe (logging/dummy) drivers if pins are unspecified or imports fail; log loudly.
- Never ship real machine credentials or IPs; keep Ruida host/port placeholders. Test dangerous changes with `--dry-run` first.

## Reference Materials (do not import)
- `reference/` holds vendor and research docs only; never import them into code, use for background.
- Quick index:
  - `nova_plus_series_unified_user's_manual.pdf` – Thunder Nova Plus series user manual (workflow, panel, setup).
  - `RDC6442GU-DFM-RD-Control-System-V1.3-Manual.pdf` and `RDV6442G-Control-System-manual-V1.3.pdf` – Ruida 6442G/6442GU controller manuals (wiring, pinouts, UI).
  - `Diskussion_Nova 35 – FabLab Region Nürnberg.html` – community discussion of Nova 35 (setup/usage anecdotes).
  - `Ruida - EduTech Wiki.html` – consolidated Ruida protocol notes: controller models and card “swizzle” keys; UDP 50200 (RD payloads with 16-bit checksum + ACK), UDP 50207 (unswizzled keypad commands); USB over FT245R at 19200 8N1 with hardware flow control; swizzling/unswizzling algorithm (MAGIC=0x88 for 644xG, 0x11 for 634XG, 0x38 for RDL9635); RD file structure (header/layer headers/body) and command table (move/cut, power, delays, jog key codes).
  - `Ruida RDC6442S_G _ Best Co2 Laser Controller.html` – review/feature overview of 6442S/G controllers.
  - `Network aware laser cutter security – Roger Clark.html` – network/attack surface notes for networked lasers.
  - `Reverse Engineering of Laser Cutter Controller RDLxxx and RDCAM Software.html` (+ `-messages.html`, `-linux.html`) – reverse-engineered Ruida/RDCAM protocol: USB via FTDI/D2XX, scrambling/descrambling (MAGIC 0x88 for 6442G), message framing (MSB-set lead byte, 0x80-0xFF then 0x00-0x7F payload), RD file decoding hints.
  - `cl57t` manual (in reference) – stepper driver documentation for CL57T (wiring, dip switch settings, current/microstep tables); use for rotary wiring, not for direct code imports.
    - CL57T status notes: has ALM+/ALM- alarm output (fault), no documented in-position/busy line; ENA input to disable; min pulse width ~2.5µs; microstep and current via DIP.
  - `23HS45` datasheet (in reference) – NEMA23 stepper specs: phase current, holding torque, wiring pinout, electrical characteristics; use for selecting current/microstep settings.
  - Python tooling snapshots (reference only, do not import):  
    - `ruida.py` – builds `.rd` files: layer model, bounding boxes, power/speed encoding, scramble/unscramble, relative/absolute move encoding.  
    - `ruidaparser.py` – decodes `.rd`: swizzle decode (0x88), parses coords/power/layer metadata, can export to SVG.  
    - `dummylaser.py` – UDP dummy/echo server for Ruida: checksum check, descramble, ACK/NACK behavior.  
    - `udpsendruida.py` – sender for RD over UDP 50200 with checksum/ACK retry and MTU chunking.  
    - `RuidaProxy.py` – UDP proxy/forwarder with ACK/NACK handling, stream timeout.  
    - Other helpers: `protocol.md` (UDP checksum and command crib), `rdcam.py`/`ruidaparser.py`/`ruida.py` variations, `rd2svg.py`/`hexparser.py` converters, `device(1).py` (Meerk40t Ruida device shim), service scripts (`novaprox-start.sh`, `novaprox.service`), and Java/MD notes. Use only as reference patterns; not production-ready or imported.
    - Meerk40t Ruida stack (`reference/meerk40t/`, reference-only): driver/controller/RDJob logic plus transports. Key pieces:  
      - `driver.py`/`controller.py`/`rdjob.py` – builds/sends RD command buffers, handles machine status polling, state, and cut planning.  
      - Transports: `udp_transport.py`/`udp_connection.py`, `tcp_connection.py`, `usb_transport.py`, `serial_connection.py`, `ruidatransport.py`, `mock_connection.py` – various comms layers with swizzle/timeout/ack handling.  
      - Session/emulation: `ruidasession.py`, `emulator.py`, `loader.py`, `plugin.py`, `device.py`, `control.py` – integration glue, device setup, and job execution; `rdjob.py` defines swizzle LUTs, command parsing, magic keys.  
      - Utilities: `exceptions.py`, `controller.py` polling, `udp_transport`/`tcp_connection` ports (50200/40200) and checksum logic; `usb_transport` notes FTDI 0x0403:0x6001 8N1 RTS/CTS/DSR/DTR.  
    - Scripts: `proxy23.py` (UDP/TCP capture/write .rd), `hexparser.py` (unscramble/checksum decoder), `device(1).py` (meerk40t device service stub).
    - Java reference: `Ruida.java` (LibLaserCut driver) – implements Ruida cutter over IP/USB/serial; supports RD export/upload, power/speed settings, bounding box calc, vector/raster property classes, and FTP/serial comms scaffolding. Use as conceptual reference only.

## Project Understanding (Codex)
- Purpose: plan dovetail joints and drive a laser/rotary jig; supports dry-runs, Tk simulation, RD save/inspect, and Ruida UDP control with rotary-aware Z offsets.
- Flow: CLI (`cli.py`/`main.py`) loads config (`config.py` → `RunConfig`), computes layout (`geometry.py`), validates (`validation.py`), plans tails/pins (`planner.py`), then executes via chosen backends (dummy/sim/Ruida + rotary).
- Data/models: `model.py` holds params/layout/commands; geometry stays pure; planners emit `Command` list (MOVE/CUT_LINE/SET_LASER_POWER/ROTATE).
- Hardware layer: `hardware/` supplies dummy/sim backends, GPIO/logging rotary, Ruida UDP (`ruida_laser.py` + `ruida_transport.py`/`ruida_common.py`), RD builder (`rd_builder.py`, opcodes in `rd_commands.py`), panel helper (`ruida_panel.py`), and Tk viewer (`simulation_viewer.py`).
- Safety defaults: dummy/movement-only friendly; capture current machine origin before RD, poll ready, park axes/rotary; air assist configurable (default on); validation gates runs.
- Config/run: copy `example-config.toml` → `config.toml`; common commands: `python -m laserdove.main --mode both --dry-run`, `--simulate`, `--save-rd-dir rd_out --movement-only`, `--reset`. CLI flags override TOML for geometry, backends, speeds/power, air/Z direction.
- Tooling/tests: inspection via `tools/rd_parser.py`/`rd_visualize.py`/`ruida_status_probe.py`; pytest in `tests/` (geometry); lint/test via `make` or `pytest`.
