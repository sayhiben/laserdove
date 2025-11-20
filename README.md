<!-- README.md -->
# novadovetail

Experimental Python tooling to drive a **Thunder Nova 24 Plus** CO₂ laser and a custom **rotary-esque edge jig** to cut dovetail joints in ~¼″ stock.

This repo focuses on:

- Clean, testable **geometry and math** for pins/tails.
- A simple **planner** that emits abstract motion commands.
- A minimal **hardware abstraction** that can be wired to a Ruida controller and a NEMA-23 rotary later.
- Support for **dry runs**, logging, and simple tests.

v1 intentionally uses a **dummy hardware backend**; it does *not* yet talk to a real Ruida.

---

## Repository layout

```text
novadovetail/
  novadovetail.py          # CLI entrypoint
  config.py                # TOML + CLI config
  model.py                 # Dataclasses (params, layouts, commands)
  geometry.py              # Pure math; tails, pins, Z offsets, kerf
  planner.py               # Convert geometry -> Command list
  hardware.py              # Laser + rotary interfaces (dummy for now)
  logging_utils.py         # Logging setup
  example-config.toml      # Example configuration
  tests/
    test_geometry.py       # Minimal geometry tests
  README.md
```

Coordinate & geometry conventions
	•	Board edges are modeled in a 1D Y range:
	•	Y ∈ [0, L], where L = edge_length_mm.
	•	0 is one end of the jointed edge; L is the other.
	•	Tail layout on the tail board:
	•	Pattern: half-pin, (tail, full-pin)*, tail, half-pin.
	•	User sets tail_outer_width_mm.
	•	Planner derives full pin width Wp such that:

L = N * tail_outer_width_mm + N * pin_outer_width

On the pin board, pins live in these gaps (same Y pattern).
	•	Rotary axis runs along X; the job origin (mid-edge) is at distance h from the axis (config axis_to_origin_mm).
	•	Z focus:
	•	Tail board: z_zero_tail_mm – focus at top surface.
	•	Pin board: z_zero_pin_mm – focus at mid-thickness; per-pin Z offsets are computed via z_offset_for_angle(y_b, θ, h).

⸻

Config and CLI

Config file

Configuration is TOML-based; see example-config.toml:

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

CLI

Basic usage (dry run):

python3 novadovetail.py --config example-config.toml --mode both --dry-run

Options:
	•	--mode {tails,pins,both} – which board to plan.
	•	--config path – which TOML file to load.
	•	--dry-run – do not talk to hardware; print the Command objects.
	•	Common overrides:
	•	--edge-length-mm
	•	--thickness-mm
	•	--num-tails
	•	--dovetail-angle-deg
	•	--tail-width-mm
	•	--clearance-mm
	•	--kerf-tail-mm
	•	--kerf-pin-mm
	•	--axis-offset-mm
	•	Logging:
	•	--log-level DEBUG|INFO|WARNING|ERROR

⸻

High-level behavior

Tail board
	•	Board flat on bed; jig is just a fence.
	•	Planner:
	•	Computes tail/pin layout.
	•	Treats each pin gap as a rectangular pocket 0..tail_depth_mm deep.
	•	Cuts pocket outlines at kerf-offset Y positions.
	•	Z is held at z_zero_tail_mm.

Pin board
	•	Board mounted on rotary; job origin at mid-edge.
	•	For each pin side:
	•	Rotary angle = rotation_zero_deg ± dovetail_angle_deg.
	•	Z offset via z_offset_for_angle with axis_to_origin_mm.
	•	Y cut position via kerf/clearance-aware offset.
	•	L-shaped cut:
	•	short X leg into the board
	•	long Y “ramp” leg at depth
	•	retract X leg
	•	Sides are processed in center-outward order per angle.

⸻

Hardware integration

Right now:
	•	DummyLaser and DummyRotary just log moves.
	•	execute_commands interprets Command objects using a dispatch lookup.
	•	You can integrate with Ruida by implementing:
	•	class RuidaLaser(LaserInterface) that wraps your RuidaProxy / udpsendruida.py.
	•	class RealRotary(RotaryInterface) that drives the NEMA-23 driver.

The executor does not need to change for that.

⸻

Testing

Minimal tests live in tests/test_geometry.py:
	•	Tail layout sanity.
	•	Z offset at 0° is zero.

You can run:

pytest

after you add a pyproject.toml / requirements.txt if you want a full test harness.

⸻

Next steps
	•	Wire in a real RuidaLaser using your existing Ruida tooling.
	•	Add a RealRotary that knows steps per degree, direction, and homing.
	•	Add validation (bounds checking) before planning/execution.
	•	Iterate with physical test joints to tune:
	•	kerf_tail_mm, kerf_pin_mm
	•	clearance_mm
	•	axis_to_origin_mm calibration
	•	dovetail angle and depth.
