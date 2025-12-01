<!-- README.md -->
# laserdove

Experimental Python tooling to drive a **Thunder Nova 24 Plus** CO₂ laser and a custom **rotary-esque edge jig** to cut dovetail joints in ~¼″ stock.

This repo focuses on:

- **Geometry and math**: clean, testable geometry for pins/tails.
- **Planning**: a simple planner that emits abstract motion commands.
- **Hardware abstraction**: Dummy/simulated backends, a UDP Ruida transport + RD job builder, and a real rotary driver scaffold.
- **Quality of life**: dry runs, Tk visualization, RD export, logging, and tests.

v1 defaults to dummy hardware; enable Ruida/real rotary via config or CLI when you are on hardware.

- **Job origin awareness**: when running on Ruida, we capture the controller’s current X/Y at job start and treat that as the job origin. All generated moves are absolute and offset from that captured origin so the head stays where you set it on the panel. Movement-only/reset modes also strip cut commands (zero-power cuts become travel) to avoid unintended firing.

---

## Repository layout

```text
laserdove/
  src/laserdove/
    cli.py                 # CLI entrypoint (python -m laserdove.cli / -m laserdove.main)
    main.py                # Thin wrapper around cli.main (preferred)
    config.py              # TOML + CLI config
    model.py               # Dataclasses (params, layouts, commands)
    geometry.py            # Pure math; tails, pins, Z offsets, kerf
    planner.py             # Convert geometry -> Command list
    simulation_viewer.py   # Tkinter viewer for sim mode
    hardware/
      base.py              # Interfaces, DummyLaser/DummyRotary, executor
      sim.py               # Tk-simulated laser/rotary backends
      rd_builder.py        # Minimal RD job builder
      ruida_common.py      # Swizzle/encode helpers
      ruida_laser.py       # UDP Ruida transport + RD upload/run
      rotary.py            # RealRotary wrapper with logging/GPIO drivers
    validation.py          # Geometry/machine/jig validation
    logging_utils.py       # Logging setup
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
air_assist          = true
z_positive_moves_bed_up = true  # set false if your controller treats Z+ as lowering the bed

[backend]
use_dummy  = true                  # legacy toggle: dummy for both unless overridden below
laser_backend = "dummy"            # "dummy" or "ruida"
rotary_backend = "dummy"           # "dummy" or "real"
movement_only = false              # keep beam off while still moving when using Ruida
ruida_host = "192.168.1.100"
ruida_port = 50200
ruida_timeout_s = 3.0
ruida_source_port = 40200
# rotary_* pins may use BOARD or BCM numbering (rotary_pin_numbering)
```
If you do **not** pass `--config`, these same values are used as the built‑in defaults. When you run without `--config`, `laserdove.main` will also look for a local `config.toml` and use it automatically if present.

For a typical workflow:

```bash
cp example-config.toml config.toml
```

Then edit `config.toml` to match your machine, jig, and joint preferences, and run with:

```bash
python3 -m laserdove.main --config config.toml
```

### Reset-only mode

To zero the rotary and park the head at pin Z0 with the laser off (no planning/cutting):

```bash
python3 -m laserdove.main --reset
```

### CLI

#### Basic usage (dry run)

```bash
python3 -m laserdove.main --config example-config.toml --mode both --dry-run
```

#### Simulation mode (visual)

Runs against the simulated backend and opens a Tkinter view of moves/cuts. Motion is paced using commanded feed/rotation rates (real-time); close the window to exit:

```bash
python3 -m laserdove.main --config example-config.toml --mode both --simulate
```

#### RD export and inspection

- `--save-rd-dir /path/to/out` writes swizzled `.rd` jobs to disk for inspection; works with real or dry-run Ruida modes.
- Decode RD files in text (bbox, Z offsets, speeds): `PYTHONPATH=src:. .venv/bin/python tools/rd_parser.py rd_out/job_001.rd`
- Visualize RD files directly in the Tk viewer: `PYTHONPATH=src:. .venv/bin/python tools/rd_visualize.py rd_out/job_001.rd --edge-length-mm 100 --rotation-deg 0 --board tail`
- RD opcode table lives in `src/laserdove/hardware/rd_commands.py` (shared by runtime/parser).
- Calibration RD notes:
  - `CA 41` appears to encode layer mode flags. In the LightBurn calibration RD, `0x00` covers line layers and CROSS1–6 (bidirectional fill at 50.8 LPI), and `0x02` covers fills/images/LPI/gradient/offset/engrave layers. CA 41 is not a pure line/fill bit.
  - `C6 11` (listed as “Time” in EduTech) stays unknown; calibration RD shows raw `00 04 62 2d 00` decoded ≈ 10000.0.
  - `E5 05` decodes to ~0.6419 mm spacing and appears once near the trailer after Finish/Stop/Work_Interval; likely a summary/spacing/DPI-related field rather than per-layer LPI.
  - Layer mapping (color → LB layer/mode/power/speed/CA41 flag):
    - 00 `#00e000` line 200/100 flag 0x00 → TEST1
    - 01 `#d0d000` line 150/100 flag 0x00 → TEST2
    - 02 `#ff8000` line 100/100 flag 0x00 → TEST3
    - 03 `#00e0e0` line 80/100 flag 0x00 → TEST4
    - 04 `#ff00ff` line 60/100 flag 0x00 → TEST5
    - 05 `#b4b4b4` line 30/100 flag 0x00 → TEST6
    - 06 `#0000a0` line 20/100 flag 0x00 → TEST7
    - 07 `#a00000` line 10/100 flag 0x00 → TEST8
    - 08 `#00a000` fill 500/100 flag 0x02 → TEST9
    - 09 `#a0a000` fill 400/100 flag 0x02 → TEST10
    - 10 `#c08000` fill 300/100 flag 0x02 → TEST11
    - 11 `#00a0ff` fill 200/100 flag 0x02 → TEST12
    - 12 `#a000a0` speed 200/12.5 flag 0x00 → CROSS1 (42°) bidirectional fill 50.8 LPI
    - 13 `#808080` speed 200/12.5 flag 0x00 → CROSS2 (25°)
    - 14 `#7d87b9` speed 200/12.5 flag 0x00 → CROSS3 (8°)
    - 15 `#bb7784` speed 200/12.5 flag 0x00 → CROSS4 (-8°)
    - 16 `#4a6fe3` speed 200/12.5 flag 0x00 → CROSS5 (-25°)
    - 17 `#d33f6a` speed 200/12.5 flag 0x00 → CROSS6 (-42°)
    - 18–25 `#8cd78c,#f0b98d,#f6c4e1,#fa9ed4,#500a78,#b45a00,#004754,#86fa88` fill 200/12.5 flag 0x02 → LPI1–LPI8
    - 26 `#ffdb66` line 500/30 flag 0x02 → OFFSET
    - 27 `#000000` line 80/30 flag 0x00 → SCORE
    - 28 `#000000` image/fill 200/12.5 flag 0x02 → GRADIENT
    - 29 `#0000ff` fill 200/12.5 flag 0x02 → ENGRAVE
    - 30 `#ff0000` line 20/45 flag 0x00 → CUT

#### Options

| **Option**              | **Description**                                                                                 | **Default**                                |
|-------------------------|-------------------------------------------------------------------------------------------------|--------------------------------------------|
| `--mode {tails,pins,both}` | Which board(s) to plan.                                                                      | `both`                                     |
| `--config PATH`        | TOML file to load (`[joint]`, `[jig]`, `[machine]`, `[backend]`). If provided and the file is missing or invalid, the program exits with an error. | `config.toml` if present, otherwise built‑ins shown above |
| `--dry-run`            | Do not talk to hardware; print `Command` objects (dummy/sim). Ruida honors dry-run by logging UDP payloads only. | disabled                                   |
| `--save-rd-dir PATH`   | Write swizzled `.rd` payloads to this directory.                                                | disabled                                   |
| `--simulate`           | Run against the simulated backend and open a Tkinter visualization.                             | disabled                                   |
| `--reset`              | Skip planning; laser off, rotate to zero, move head to origin at pin Z0.                        | disabled                                   |
| `--movement-only`      | Keep laser power at 0 and emit travel-only RD jobs (no cut commands) while still driving motion. | disabled                                   |
| `--air-assist` / `--no-air-assist` | Toggle air assist in generated RD jobs.                                                 | `machine.air_assist` (true)                |
| `--z-positive-bed-up` / `--z-positive-bed-down` | Define Z+ direction: bed up (closer to head) vs bed down.                            | `machine.z_positive_moves_bed_up` (true)   |
| `--laser-backend {dummy,ruida}` | Override laser backend (dummy for logs, ruida for UDP+RD).                              | from config/`use_dummy`                    |
| `--rotary-backend {dummy,real}` | Override rotary backend (dummy for logs, real for stepper GPIO).                         | from config/`use_dummy`                    |
| `--edge-length-mm`     | Override `joint.edge_length_mm`.                                                                | unset (use config/built‑in)                |
| `--thickness-mm`       | Override `joint.thickness_mm` (also sets `tail_depth_mm` to match).                             | unset (use config/built‑in)                |
| `--num-tails`          | Override `joint.num_tails`.                                                                     | unset (use config/built‑in)                |
| `--dovetail-angle-deg` | Override `joint.dovetail_angle_deg`.                                                            | unset (use config/built‑in)                |
| `--tail-width-mm`      | Override `joint.tail_outer_width_mm`.                                                           | unset (use config/built‑in)                |
| `--clearance-mm`       | Override `joint.clearance_mm`.                                                                  | unset (use config/built‑in)                |
| `--kerf-tail-mm`       | Override `joint.kerf_tail_mm`.                                                                  | unset (use config/built‑in)                |
| `--kerf-pin-mm`        | Override `joint.kerf_pin_mm`.                                                                   | unset (use config/built‑in)                |
| `--axis-offset-mm`     | Override `jig.axis_to_origin_mm`.                                                               | unset (use config/built‑in)                |
| `--ruida-timeout-s`    | UDP ACK timeout seconds for Ruida.                                                              | `backend.ruida_timeout_s` (3.0)            |
| `--ruida-source-port`  | Local UDP source port.                                                                          | `backend.ruida_source_port` (40200)        |
| `--rotary-steps-per-rev` | Full steps per revolution for rotary driver.                                                   | `backend.rotary_steps_per_rev` (4000)      |
| `--rotary-microsteps`  | Extra microstep multiplier from driver DIP switch.                                              | `backend.rotary_microsteps` (none)         |
| `--rotary-step-pin`, `--rotary-dir-pin` | BCM pins for STEP-/DIR- when `--rotary-backend real`.                               | unset                                      |
| `--rotary-step-pin-pos`, `--rotary-dir-pin-pos` | Pins for STEP+/DIR+ (pulse these if PUL-/DIR- tied to GND).                      | 11 / 13 (BOARD)                            |
| `--rotary-enable-pin`, `--rotary-alarm-pin` | Optional pins for enable (active low) and alarm input.                           | unset                                      |
| `--rotary-invert-dir`  | Invert DIR output when using the real rotary.                                                    | disabled                                   |
| `--rotary-pin-numbering {bcm,board}` | Pin numbering scheme for rotary GPIO.                                                 | `board`                                    |
| `--rotary-max-step-rate-hz` | Cap rotary step pulse rate (Hz); prevents over-speeding when using high pulses/rev.     | 500.0                                      |
| `--log-level`          | Logging verbosity for `laserdove.main`.                                                        | `INFO`                                     |

### Ruida transport and status notes

- **Origin anchoring**: The machine’s X/Y at job start become the job origin; all commands remain absolute (Ruida prefers periodic absolutes for precision) but offset from that captured origin so the head does not jump to controller 0,0 after you set origin on the panel.
- **Movement-only/reset safety**: zero-power cuts are omitted from RD files; only travel moves remain to avoid controllers interpreting “unset power” as a default.
- **Air assist**: default on via `machine.air_assist`; toggle with CLI `--air-assist/--no-air-assist`.
- **Status wait heuristic**: when Ruida firmware doesn’t assert busy bits, the transport waits for a deviation then stable status/position before proceeding.
- **Status probe tool**: `python tools/ruida_status_probe.py --host <ip> --baseline-polls 5 --polls-after 20` (dual-socket polling by default) to log status codes while running movement-only actions.

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

`src/laserdove/hardware/` provides:

- `DummyLaser` / `DummyRotary`: log-only backends; safest for dev.  
- `SimulatedLaser` / `SimulatedRotary`: Tkinter visualization; respects feed/rotation rates in real time.  
- `RuidaLaser`: UDP (50200) transport with swizzle/checksum, RD job builder/uploader, optional RD file save, travel-only clamp (`--movement-only`/`--reset` force RD jobs to moves with 0 power).  
- `RealRotary`: stepper wrapper that can emit GPIO DIR/STEP pulses (via `GPIOStepperDriver`) or just log (`LoggingStepperDriver`).

Ruida communication waits for ACKs and polls for job completion; `movement_only=true` sends a single laser-off then suppresses further power changes while still moving.

Backend selection is driven by the `[backend]` section in the config:

```toml
[backend]
use_dummy  = true
laser_backend = "dummy"
rotary_backend = "dummy"
movement_only = false
ruida_host = "192.168.1.100"
ruida_port = 50200
```

- `use_dummy` keeps the legacy "all dummy vs all real" switch; `laser_backend`/`rotary_backend` override each side independently (e.g., real rotary + dummy laser).  
- `movement_only = true` sends a single laser-off to Ruida then suppresses all further power changes while still issuing moves—useful for motion shakedowns on real hardware.  
- `RuidaLaser` uses UDP to `ruida_host:ruida_port` (default 50200/40200, timeout `backend.ruida_timeout_s`, source port `backend.ruida_source_port`, swizzle `backend.ruida_magic`). `RealRotary` drives the stepper (`backend.rotary_steps_per_rev` default 4000 pulses/rev, `backend.rotary_microsteps` multiplier, `backend.rotary_max_step_rate_hz` cap at 500 Hz by default).
- Default rotary pins match the working Pi script (physical BOARD numbering): pulse PUL+/DIR+ (`rotary_step_pin_pos=11`, `rotary_dir_pin_pos=13`) with PUL-/DIR- tied to GND. You can instead drive the negative side (e.g., BCM6/14) by setting `rotary_step_pin`/`rotary_dir_pin` and leaving the opposite side tied high.
- Motion-only presets:
  - Rotary-only checkout: `laser_backend="dummy"`, `rotary_backend="real"`, `movement_only=true`.
  - XY-only checkout: `laser_backend="ruida"`, `rotary_backend="dummy"`, `movement_only=true`.
  - Combined motion without firing: `laser_backend="ruida"`, `rotary_backend="real"`, `movement_only=true`.
- Real rotary GPIO pins: `backend.rotary_pin_numbering` selects BCM vs BOARD numbering (default BOARD/physical). Defaults: `rotary_step_pin_pos=11`, `rotary_dir_pin_pos=13` (pulse + side, PUL-/DIR- to GND). Optional `rotary_step_pin`/`rotary_dir_pin` if you pulse the - side instead, optional `rotary_enable_pin` (active low) and `rotary_alarm_pin` (input); omit optional pins if not wired; `rotary_invert_dir` (boolean).

---

## Validation

Before planning any motion, `laserdove.main` validates:

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
