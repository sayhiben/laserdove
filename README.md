<!-- README.md -->
# novadovetail

Experimental Python tooling to drive a **Thunder Nova 24 Plus** CO₂ laser and a custom **rotary-esque edge jig** to cut dovetail joints in ~¼″ stock.

This repo focuses on:

- **Geometry and math**: clean, testable geometry for pins/tails.
- **Planning**: a simple planner that emits abstract motion commands.
- **Hardware abstraction**: a minimal layer that can be wired to a Ruida controller and a NEMA‑23 rotary.
- **Quality of life**: support for dry runs, logging, and basic tests.

v1 intentionally uses dummy hardware by default; it does *not* yet talk to a real Ruida unless you wire in the skeleton classes.

---

## Repository layout

```text
novadovetail/
  novadovetail.py          # CLI entrypoint
  config.py                # TOML + CLI config
  model.py                 # Dataclasses (params, layouts, commands)
  geometry.py              # Pure math; tails, pins, Z offsets, kerf
  planner.py               # Convert geometry -> Command list
  hardware.py              # Laser + rotary interfaces (dummy + skeleton real)
  validation.py            # Geometry/machine/jig validation
  logging_utils.py         # Logging setup
  example-config.toml      # Example configuration
  tests/
    test_geometry.py       # Minimal geometry tests
  README.md
```

---

## Coordinate & geometry conventions

- **Joint edge (Y axis)**  
  - Modeled in a 1D range \( Y \in [0, L] \), where \( L = \text{edge\_length\_mm} \).  
  - \( Y = 0 \) is one end of the jointed edge; \( Y = L \) is the other.

- **Tail layout on the tail board**  
  - Pattern: half‑pin, `(tail, full-pin)*`, tail, half‑pin.  
  - User sets `tail_outer_width_mm`.  
  - The planner derives full pin width `pin_outer_width` such that:

    ```text
    L = N * tail_outer_width_mm + N * pin_outer_width
    ```

- **Pin board**  
  - On the pin board, pins live in the gaps between tails (same Y pattern).

- **Rotary axis and origin**  
  - Rotary axis runs along X.  
  - The job origin (mid‑edge) is at distance \( h \) from the axis (config `axis_to_origin_mm`).

- **Z focus**  
  - Tail board: `z_zero_tail_mm` – focus at the top surface.  
  - Pin board: `z_zero_pin_mm` – focus at mid‑thickness.  
  - Per‑pin Z offsets are computed via `z_offset_for_angle(y_b, θ, h)`, where `y_b` is Y relative to the mid‑edge (board‑centered coordinates) and `h` is the axis‑to‑origin radius.

---

## Config and CLI

### Config file

Configuration is TOML‑based; see `example-config.toml`:

```toml
[joint]
thickness_mm        = 6.35
edge_length_mm      = 100.0
dovetail_angle_deg  = 8.0
num_tails           = 3
tail_outer_width_mm = 20.0
tail_depth_mm       = 6.35
socket_depth_mm     = 6.60
clearance_mm        = 0.05
kerf_tail_mm        = 0.15
kerf_pin_mm         = 0.15

[jig]
axis_to_origin_mm   = 30.0
rotation_zero_deg   = 0.0
rotation_speed_dps  = 30.0

[machine]
cut_speed_tail_mm_s = 10.0
cut_speed_pin_mm_s  = 8.0
rapid_speed_mm_s    = 200.0
z_speed_mm_s        = 5.0
cut_power_tail_pct  = 60.0
cut_power_pin_pct   = 65.0
travel_power_pct    = 0.0
z_zero_tail_mm      = 0.0
z_zero_pin_mm       = 0.0

[backend]
use_dummy  = true
ruida_host = "192.168.1.100"
ruida_port = 50200
```
If you do **not** pass `--config`, these same values are used as the built‑in defaults. When you run without `--config`, `novadovetail` will also look for a local `config.toml` and use it automatically if present.

For a typical workflow:

```bash
cp example-config.toml config.toml
```

Then edit `config.toml` to match your machine, jig, and joint preferences, and run with:

```bash
python3 novadovetail.py --config config.toml
```

### CLI

#### Basic usage (dry run)

```bash
python3 novadovetail.py --config example-config.toml --mode both --dry-run
```

#### Simulation mode (visual)

Runs against the simulated backend and opens a Tkinter view of moves/cuts. Motion is paced using commanded feed/rotation rates (real-time); close the window to exit:

```bash
python3 novadovetail.py --config example-config.toml --mode both --simulate
```

#### Options

| **Option**              | **Description**                                                                                 | **Default**                                |
|-------------------------|-------------------------------------------------------------------------------------------------|--------------------------------------------|
| `--mode {tails,pins,both}` | Which board(s) to plan.                                                                      | `both`                                     |
| `--config PATH`        | TOML file to load (`[joint]`, `[jig]`, `[machine]`, `[backend]`). If provided and the file is missing or invalid, the program exits with an error. | `config.toml` if present, otherwise built‑ins shown above |
| `--dry-run`            | Do not talk to hardware; print `Command` objects instead.                                      | disabled                                   |
| `--edge-length-mm`     | Override `joint.edge_length_mm`.                                                               | unset (use config/built‑in)                |
| `--thickness-mm`       | Override `joint.thickness_mm` (also sets `tail_depth_mm` to match).                           | unset (use config/built‑in)                |
| `--num-tails`          | Override `joint.num_tails`.                                                                    | unset (use config/built‑in)                |
| `--dovetail-angle-deg` | Override `joint.dovetail_angle_deg`.                                                           | unset (use config/built‑in)                |
| `--tail-width-mm`      | Override `joint.tail_outer_width_mm`.                                                          | unset (use config/built‑in)                |
| `--clearance-mm`       | Override `joint.clearance_mm`.                                                                 | unset (use config/built‑in)                |
| `--kerf-tail-mm`       | Override `joint.kerf_tail_mm`.                                                                 | unset (use config/built‑in)                |
| `--kerf-pin-mm`        | Override `joint.kerf_pin_mm`.                                                                  | unset (use config/built‑in)                |
| `--axis-offset-mm`     | Override `jig.axis_to_origin_mm`.                                                              | unset (use config/built‑in)                |
| `--simulate`           | Run against the simulated backend and open a Tkinter visualization.                           | disabled                                   |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | Logging verbosity for `novadovetail`.                                        | `INFO`                                     |

---

## High‑level behavior

### Tail board

- Board flat on the bed; the jig is a fence only.  
- The planner:
  - Computes tail/pin layout.  
  - Treats each pin gap as a rectangular pocket from 0 to `tail_depth_mm` deep.  
  - Cuts pocket outlines at kerf‑offset Y positions.  
  - Holds Z at `z_zero_tail_mm`.

### Pin board

- Board mounted on the rotary; job origin at mid‑edge.  
- For each pin flank:
  - Rotary angle = `rotation_zero_deg ± dovetail_angle_deg`.  
  - Z offset via `z_offset_for_angle` with `axis_to_origin_mm` and board-centered Y.  
  - Y cut position via kerf/clearance-aware offset.  
- Each flank cuts a closed, orthogonal rectangle spanning half the gap to the neighboring pin/half-pin; angular fit comes from the jig rotation, not from shearing the cut path.  
- Flanks are processed in center-outward order per angle.

---

## Backends

`hardware.py` defines four backends:

- `DummyLaser` / `DummyRotary`: fully simulated; only log moves.  
- `RuidaLaser`: skeleton wrapper intended to talk to a Ruida controller (via `RuidaProxy` or UDP).  
- `RealRotary`: skeleton wrapper for the physical rotary stepper on the Pi.

Backend selection is driven by the `[backend]` section in the config:

```toml
[backend]
use_dummy  = true
ruida_host = "192.168.1.100"
ruida_port = 50200
```

- If `use_dummy = true`, `novadovetail` uses `DummyLaser` and `DummyRotary`.  
- If `use_dummy = false`, it uses `RuidaLaser` (UDP to `ruida_host:ruida_port`, default 50200/40200, timeout `backend.ruida_timeout_s`, source port `backend.ruida_source_port`, swizzle `backend.ruida_magic`) and `RealRotary`.

`RuidaLaser` and `RealRotary` currently only log; you must fill in the TODOs with your actual UDP / RD‑job / GPIO / driver calls.

---

## Validation

Before planning any motion, `novadovetail` validates:

- **Joint geometry**: thickness, `edge_length_mm`, `num_tails`, angles, kerf.  
- **Tail layout**: tails do not extend outside the edge length.  
- **Machine parameters**: cut/rapid/Z speeds, power ranges.  
- **Jig parameters**: `axis_to_origin_mm > 0`.

If validation fails, the program prints all errors and exits before talking to hardware.

Validation logic lives in `validation.py`.

---

## Testing

Tests live under `tests/`; run them with coverage enabled by default:

```bash
pytest
```

This emits terminal coverage and writes `coverage.xml` for CI artifacts.
