# Repository Guidelines

## Project Structure & Module Organization
- CLI entrypoint `cli.py` (python -m laserdove.cli) wires config, validation, planning, and hardware backends.
- Core math in `geometry.py`; plans and command sequencing in `planner.py`; shared dataclasses in `model.py`.
- Hardware abstractions live in `hardware.py` (`DummyLaser`/`DummyRotary` by default, Ruida/rotary skeletons for real hardware); logging helpers in `logging_utils.py`.
- Config parsing and CLI overrides are in `config.py`; reference config lives in `example-config.toml`. Per-setup config should be `config.toml` (git-ignored).
- Tests live under `tests/` (currently `tests/test_geometry.py`); add new suites alongside the module under test.
- Source code lives under `src/laserdove/`; run with `python -m laserdove.novadovetail`.

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
