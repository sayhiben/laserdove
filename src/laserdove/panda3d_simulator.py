# panda3d_simulator.py
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from .model import Command, CommandType

log = logging.getLogger(__name__)


@dataclass
class PlaybackSegment:
    """
    One motion/rotation interval with start/end poses in board and world space.
    """

    start_board: tuple[float, float, float]
    end_board: tuple[float, float, float]
    start_world: tuple[float, float, float]
    end_world: tuple[float, float, float]
    start_rotation_deg: float
    end_rotation_deg: float
    board: str
    is_cut: bool
    duration: float
    power_pct: float
    air_assist: bool
    source: str = "plan"


class CutMesh:
    """
    Simple prism mesh with subtractive holes in the X/Y plane, extruded along Z.
    """

    def __init__(
        self,
        parent,
        *,
        geom_factory: dict[str, object],
        color: tuple[float, float, float, float],
        thickness_x: float,
        y_center: float,
        height_z: float,
        z_offset: float,
        rotation_zero_deg: float = 0.0,
    ) -> None:
        self.parent = parent
        self.color = color
        self.thickness_x = thickness_x
        self.y_center = y_center
        self.height_z = height_z
        self.z_offset = z_offset
        self.rotation_zero_deg = rotation_zero_deg
        self.holes: list[tuple[list[tuple[float, float]], float]] = []
        self.node = None
        self.wire_np = None
        self._force_box = False
        self._epsilon = max(1e-4, min(0.005, max(thickness_x, y_center) * 0.0005))
        self._Geom = geom_factory["Geom"]
        self._GeomNode = geom_factory["GeomNode"]
        self._GeomTriangles = geom_factory["GeomTriangles"]
        self._GeomVertexData = geom_factory["GeomVertexData"]
        self._GeomVertexFormat = geom_factory["GeomVertexFormat"]
        self._GeomVertexWriter = geom_factory["GeomVertexWriter"]
        self._TransparencyAttrib = geom_factory.get("TransparencyAttrib")
        if not self._build():
            self._force_box = True
            self._build_box()

    def reset(self) -> None:
        log.debug("Reset cut mesh (clearing %d holes)", len(self.holes))
        self.holes.clear()
        self._build()

    def add_hole(self, poly: list[tuple[float, float]], rotation_deg: float = 0.0) -> None:
        if self._force_box:
            log.warning(
                "Skipping hole; triangulation forced to box fallback (holes=%d)", len(self.holes)
            )
            return
        if len(poly) < 3:
            return
        # Ensure closed loop
        if poly[0] != poly[-1]:
            poly = poly + [poly[0]]
        safe = self._sanitize_hole(poly)
        if len(safe) < 3:
            log.debug("Ignoring degenerate hole: %s", safe)
            return
        rotation_delta = rotation_deg - self.rotation_zero_deg
        rotation_rad = math.radians(rotation_delta)
        # Use the latest rotation to shear the bottom surface relative to the top.
        self._rotation_rad_for_mesh = rotation_rad
        self.holes.append((safe, rotation_rad))
        log.debug(
            "Added hole %d with %d pts (rot=%.3f°, delta=%.3f°)",
            len(self.holes),
            len(safe),
            rotation_deg,
            rotation_delta,
        )
        if not self._build():
            log.warning("Dropping hole %d due to triangulation failure", len(self.holes))
            self.holes.pop()

    def _sanitize_hole(self, poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        Clamp hole vertices slightly inside the stock so the triangulator
        does not reject segments that lie on the outer boundary.
        """
        eps = self._epsilon
        ymin = -self.y_center - eps
        ymax = self.y_center + eps
        xmin = 0.0 - eps
        xmax = self.thickness_x + eps
        clamped = []
        for x, y in poly:
            clamped.append(
                (
                    max(xmin, min(xmax, x)),
                    max(ymin, min(ymax, y)),
                )
            )
        # Remove consecutive duplicates
        deduped = []
        for pt in clamped:
            if not deduped or _distance(pt, deduped[-1]) > 1e-6:
                deduped.append(pt)
        if len(deduped) < 3:
            return []
        xs = [p[0] for p in deduped]
        ys = [p[1] for p in deduped]
        if (max(xs) - min(xs)) < eps or (max(ys) - min(ys)) < eps:
            return []
        area_eps = max(self._epsilon * 0.2, 1e-5)
        if abs(_polygon_area(deduped + [deduped[0]])) < area_eps * area_eps * 10:
            return []
        return deduped

    def _outer_ring(self) -> list[tuple[float, float]]:
        yc = self.y_center
        return [(0.0, -yc), (self.thickness_x, -yc), (self.thickness_x, yc), (0.0, yc)]

    def _build_grid_mesh(self) -> bool:
        x_coords = {0.0, self.thickness_x}
        y_coords = {-self.y_center, self.y_center}
        hole_shear: dict[tuple[int, int], float] = {}
        for hole, rot in self.holes:
            y_shift = -self.height_z * math.sin(rot)
            for x, y in hole:
                x_coords.add(x)
                y_coords.add(y)
                y_coords.add(y + y_shift)
        x_edges = sorted(x_coords)
        y_edges = sorted(y_coords)
        if len(x_edges) < 2 or len(y_edges) < 2:
            log.warning("Grid mesh missing edges; falling back to box")
            return False

        def point_in_poly(pt: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
            # Ray casting
            x, y = pt
            inside = False
            pts = poly if poly[0] == poly[-1] else poly + [poly[0]]
            for i in range(len(pts) - 1):
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                if ((y0 > y) != (y1 > y)) and x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0:
                    inside = not inside
            return inside

        filled: set[tuple[int, int]] = set()
        for ix in range(len(x_edges) - 1):
            for iy in range(len(y_edges) - 1):
                xc = 0.5 * (x_edges[ix] + x_edges[ix + 1])
                yc = 0.5 * (y_edges[iy] + y_edges[iy + 1])
                in_outer = 0.0 <= xc <= self.thickness_x and -self.y_center <= yc <= self.y_center
                in_hole = False
                shear_val = 0.0
                for hole, rot in self.holes:
                    y_shift = -self.height_z * math.sin(rot)
                    if point_in_poly((xc, yc), hole) or point_in_poly(
                        (xc, yc), [(x, y + y_shift) for x, y in hole]
                    ):
                        in_hole = True
                        shear_val = y_shift
                        break
                if in_outer and not in_hole:
                    filled.add((ix, iy))
                if in_outer and in_hole:
                    hole_shear[(ix, iy)] = shear_val

        if not filled:
            log.warning("Grid mesh found no filled cells; falling back to box")
            return False

        format = self._GeomVertexFormat.getV3c4()
        format = self._GeomVertexFormat.getV3n3c4()
        vdata = self._GeomVertexData("gridmesh", format, self._Geom.UHStatic)
        vwriter = self._GeomVertexWriter(vdata, "vertex")
        nwriter = self._GeomVertexWriter(vdata, "normal")
        cwriter = self._GeomVertexWriter(vdata, "color")
        tris = self._GeomTriangles(self._Geom.UHStatic)

        def add_vertex(pt: tuple[float, float, float], normal: tuple[float, float, float]) -> int:
            vwriter.addData3(*pt)
            nwriter.addData3(*normal)
            cwriter.addData4f(*self.color)
            return vwriter.getWriteRow() - 1

        def add_quad(p0, p1, p2, p3):
            ux, uy, uz = (p1[i] - p0[i] for i in range(3))
            vx, vy, vz = (p3[i] - p0[i] for i in range(3))
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            norm_len = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            normal = (nx / norm_len, ny / norm_len, nz / norm_len)

            i0 = add_vertex(p0, normal)
            i1 = add_vertex(p1, normal)
            i2 = add_vertex(p2, normal)
            i3 = add_vertex(p3, normal)
            tris.addVertices(i0, i1, i2)
            tris.addVertices(i0, i2, i3)

        z_top = 0.0
        z_bot = -self.height_z

        max_ix = len(x_edges) - 1
        max_iy = len(y_edges) - 1

        def is_filled(ix, iy):
            return (ix, iy) in filled

        def shear_for_hole(ix: int, iy: int) -> float:
            return hole_shear.get((ix, iy), 0.0)

        for ix, iy in filled:
            x0, x1 = x_edges[ix], x_edges[ix + 1]
            y0, y1 = y_edges[iy], y_edges[iy + 1]
            add_quad(
                (x0, y0, z_top),
                (x1, y0, z_top),
                (x1, y1, z_top),
                (x0, y1, z_top),
            )
            add_quad((x0, y0, z_bot), (x0, y1, z_bot), (x1, y1, z_bot), (x1, y0, z_bot))

            if ix - 1 < 0 or not is_filled(ix - 1, iy):
                shear_bot = shear_for_hole(ix - 1, iy) if ix - 1 >= 0 else 0.0
                add_quad(
                    (x0, y0 + shear_bot, z_bot),
                    (x0, y0, z_top),
                    (x0, y1, z_top),
                    (x0, y1 + shear_bot, z_bot),
                )
            if ix + 1 >= max_ix or not is_filled(ix + 1, iy):
                shear_bot = shear_for_hole(ix + 1, iy) if ix + 1 < max_ix else 0.0
                add_quad(
                    (x1, y0, z_top),
                    (x1, y0 + shear_bot, z_bot),
                    (x1, y1 + shear_bot, z_bot),
                    (x1, y1, z_top),
                )
            if iy - 1 < 0 or not is_filled(ix, iy - 1):
                shear_bot = shear_for_hole(ix, iy - 1) if iy - 1 >= 0 else 0.0
                add_quad(
                    (x0, y0 + shear_bot, z_bot),
                    (x1, y0 + shear_bot, z_bot),
                    (x1, y0, z_top),
                    (x0, y0, z_top),
                )
            if iy + 1 >= max_iy or not is_filled(ix, iy + 1):
                shear_bot = shear_for_hole(ix, iy + 1) if iy + 1 < max_iy else 0.0
                add_quad(
                    (x0, y1, z_top),
                    (x1, y1, z_top),
                    (x1, y1 + shear_bot, z_bot),
                    (x0, y1 + shear_bot, z_bot),
                )

        geom = self._Geom(vdata)
        geom.addPrimitive(tris)
        gnode = self._GeomNode("material")
        gnode.addGeom(geom)
        self.node = self.parent.attachNewNode(gnode)
        self.node.setPos(0, 0, self.z_offset)
        self.node.setTwoSided(True)
        self._apply_wireframe()
        return True

    def _build(self) -> bool:
        if self.node is not None:
            try:
                self.node.removeNode()
            except Exception:
                pass
            self.node = None
        if self.wire_np is not None:
            try:
                self.wire_np.removeNode()
            except Exception:
                pass
            self.wire_np = None

        log.debug(
            "Building cut mesh (holes=%d, thickness=%.3f, height=%.3f)",
            len(self.holes),
            self.thickness_x,
            self.height_z,
        )
        if self._build_grid_mesh():
            log.debug("Cut mesh built (holes=%d)", len(self.holes))
            return True

        log.warning(
            "Grid mesh failed; falling back to solid box (holes=%d, dims=%.3fx%.3f)",
            len(self.holes),
            self.thickness_x,
            self.height_z,
        )
        self._build_box()
        return False

    def _build_box(self) -> None:
        """
        Fallback solid box if triangulation fails (should be rare).
        """
        if self.node is not None:
            try:
                self.node.removeNode()
            except Exception:
                pass
            self.node = None
        log.debug(
            "Building solid box fallback (thickness=%.3f, height=%.3f)",
            self.thickness_x,
            self.height_z,
        )
        format = self._GeomVertexFormat.getV3n3c4()
        vdata = self._GeomVertexData("box", format, self._Geom.UHStatic)
        vwriter = self._GeomVertexWriter(vdata, "vertex")
        nwriter = self._GeomVertexWriter(vdata, "normal")
        cwriter = self._GeomVertexWriter(vdata, "color")
        top_z = 0.0
        bot_z = -self.height_z
        corners = [
            (0.0, -self.y_center, top_z),
            (self.thickness_x, -self.y_center, top_z),
            (self.thickness_x, self.y_center, top_z),
            (0.0, self.y_center, top_z),
            (0.0, -self.y_center, bot_z),
            (self.thickness_x, -self.y_center, bot_z),
            (self.thickness_x, self.y_center, bot_z),
            (0.0, self.y_center, bot_z),
        ]
        tris = self._GeomTriangles(self._Geom.UHStatic)

        def add_face(indices: tuple[int, int, int, int]):
            a, b, c, d = indices
            p0, p1, p3 = corners[a], corners[b], corners[d]
            ux, uy, uz = (p1[i] - p0[i] for i in range(3))
            vx, vy, vz = (p3[i] - p0[i] for i in range(3))
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            norm_len = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            normal = (nx / norm_len, ny / norm_len, nz / norm_len)
            base_idx = vwriter.getWriteRow()
            for idx in indices:
                vwriter.addData3(*corners[idx])
                nwriter.addData3(*normal)
                cwriter.addData4f(*self.color)
            tris.addVertices(base_idx + 0, base_idx + 1, base_idx + 2)
            tris.addVertices(base_idx + 0, base_idx + 2, base_idx + 3)

        faces = (
            (0, 1, 2, 3),  # top
            (4, 5, 6, 7),  # bottom
            (0, 1, 5, 4),  # front
            (1, 2, 6, 5),  # right
            (2, 3, 7, 6),  # back
            (3, 0, 4, 7),  # left
        )
        for face in faces:
            add_face(face)
        geom = self._Geom(vdata)
        geom.addPrimitive(tris)
        gnode = self._GeomNode("material_box")
        gnode.addGeom(geom)
        self.node = self.parent.attachNewNode(gnode)
        self.node.setPos(0, 0, self.z_offset)
        self.node.setTwoSided(True)
        self._apply_wireframe()

    def _apply_wireframe(self) -> None:
        """
        Create a thin wireframe overlay to increase edge definition.
        """
        if self.wire_np is not None:
            try:
                self.wire_np.removeNode()
            except Exception:
                pass
            self.wire_np = None
        if self.node is None:
            return
        self.wire_np = self.node.copyTo(self.node.getParent())
        self.wire_np.setRenderModeWireframe()
        self.wire_np.setRenderModeThickness(1.4)
        self.wire_np.setColor(0.05, 0.05, 0.05, 0.7)
        if self._TransparencyAttrib:
            self.wire_np.setTransparency(self._TransparencyAttrib.MAlpha)
        self.wire_np.setDepthOffset(1)


def invert_projected_y(
    y_machine: float,
    rotation_deg: float,
    *,
    axis_to_origin_mm: float,
    y_center: float,
    rotation_zero_deg: float = 0.0,
) -> float:
    """
    Recover board-space Y from a projected machine-space Y at a given rotation.
    """
    delta = rotation_deg - rotation_zero_deg
    cos_t = math.cos(math.radians(abs(delta)))
    if abs(cos_t) < 1e-6:
        return y_center
    sin_t = math.sin(math.radians(delta))
    return y_center + (y_machine - y_center + axis_to_origin_mm * sin_t) / cos_t


def board_to_world_local(
    x_b: float,
    y_local: float,
    z_local: float,
    rotation_deg: float,
    *,
    axis_to_origin_mm: float,
    y_center: float,
    rotation_zero_deg: float = 0.0,
) -> tuple[float, float, float]:
    """
    Map board-local coordinates (centered at y=0, z=0 at the top surface)
    into world space for a given rotary angle.
    """
    delta = rotation_deg - rotation_zero_deg
    angle_rad = math.radians(abs(delta))
    sin_t = math.sin(math.radians(delta))
    cos_t = math.cos(angle_rad)
    y_rot = y_local * cos_t - (axis_to_origin_mm + z_local) * sin_t
    z_rot = y_local * sin_t + (axis_to_origin_mm + z_local) * cos_t
    return (x_b, y_center + y_rot, z_rot)


def _current_z_reference(board: str, z_zero_tail_mm: float, z_zero_pin_mm: float) -> float:
    return z_zero_tail_mm if board == "tail" else z_zero_pin_mm


def capture_segments_from_commands(
    commands: Iterable[Command],
    *,
    edge_length_mm: float,
    axis_to_origin_mm: float,
    rotation_zero_deg: float,
    z_zero_tail_mm: float,
    z_zero_pin_mm: float,
    movement_only: bool = False,
    air_assist: bool = True,
    start_board: str = "tail",
) -> List[PlaybackSegment]:
    """
    Expand planner Commands into time-annotated playback segments for visualization.
    """
    y_center = edge_length_mm / 2.0
    rotation = rotation_zero_deg
    board = start_board
    z_ref = _current_z_reference(board, z_zero_tail_mm, z_zero_pin_mm)
    x = 0.0
    y = 0.0
    z = z_ref
    power_pct = 0.0
    segments: List[PlaybackSegment] = []

    y_local = y - y_center
    z_local = z - z_ref
    board_local = (x, y_local, z_local)
    world_pos = board_to_world_local(
        x,
        y_local,
        z_local,
        rotation,
        axis_to_origin_mm=axis_to_origin_mm,
        y_center=y_center,
        rotation_zero_deg=rotation_zero_deg,
    )

    for command in commands:
        if command.type == CommandType.SET_LASER_POWER:
            power_pct = 0.0 if movement_only else (command.power_pct or 0.0)
            continue

        if command.type == CommandType.ROTATE:
            target_rotation = rotation if command.angle_deg is None else command.angle_deg
            delta_angle = abs(target_rotation - rotation)
            speed = command.speed_mm_s or 0.0
            duration = delta_angle / speed if speed > 0 else 0.0
            segments.append(
                PlaybackSegment(
                    start_board=board_local,
                    end_board=board_local,
                    start_world=world_pos,
                    end_world=world_pos,
                    start_rotation_deg=rotation,
                    end_rotation_deg=target_rotation,
                    board=board,
                    is_cut=False,
                    duration=duration,
                    power_pct=power_pct,
                    air_assist=air_assist,
                    source="plan",
                )
            )
            rotation = target_rotation
            if board != "pin":
                board = "pin"
            z_ref = _current_z_reference(board, z_zero_tail_mm, z_zero_pin_mm)
            board_y_abs = (
                invert_projected_y(
                    y,
                    rotation,
                    axis_to_origin_mm=axis_to_origin_mm,
                    y_center=y_center,
                    rotation_zero_deg=rotation_zero_deg,
                )
                if board == "pin" and not math.isclose(rotation, rotation_zero_deg, abs_tol=1e-9)
                else y
            )
            board_local = (x, board_y_abs - y_center, z - z_ref)
            world_pos = board_to_world_local(
                board_local[0],
                board_local[1],
                board_local[2],
                rotation,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            continue

        if command.type not in (CommandType.MOVE, CommandType.CUT_LINE):
            log.debug("Skipping unsupported command in simulator: %s", command)
            continue

        target_x = x if command.x is None else command.x
        target_y_machine = y if command.y is None else command.y
        target_z = z if command.z is None else command.z

        y_board_abs = (
            invert_projected_y(
                target_y_machine,
                rotation,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            if board == "pin" and not math.isclose(rotation, rotation_zero_deg, abs_tol=1e-9)
            else target_y_machine
        )
        y_target_local = y_board_abs - y_center
        z_target_local = target_z - z_ref

        end_world = board_to_world_local(
            target_x,
            y_target_local,
            z_target_local,
            rotation,
            axis_to_origin_mm=axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=rotation_zero_deg,
        )

        dx = target_x - x
        dy = target_y_machine - y
        dz = target_z - z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if command.type == CommandType.MOVE and math.isclose(distance, 0.0, abs_tol=1e-9):
            distance = abs(dz)
        speed = command.speed_mm_s or 0.0
        duration = distance / speed if speed > 0 else 0.0

        is_cut = command.type == CommandType.CUT_LINE and (not movement_only) and power_pct > 0.0
        segment = PlaybackSegment(
            start_board=board_local,
            end_board=(target_x, y_target_local, z_target_local),
            start_world=world_pos,
            end_world=end_world,
            start_rotation_deg=rotation,
            end_rotation_deg=rotation,
            board=board,
            is_cut=is_cut,
            duration=duration,
            power_pct=power_pct,
            air_assist=air_assist,
            source="plan",
        )
        segments.append(segment)

        x, y, z = target_x, target_y_machine, target_z
        board_local = (target_x, y_target_local, z_target_local)
        world_pos = end_world

    return segments


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.hypot(dx, dy)


def _polygon_area(poly: list[tuple[float, float]]) -> float:
    if len(poly) < 3:
        return 0.0
    area = 0.0
    for i in range(len(poly) - 1):
        x0, y0 = poly[i]
        x1, y1 = poly[i + 1]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def overlay_segments_from_rd(
    rd_segments: Sequence[dict],
    rotation_deg: float,
    board: str,
    *,
    edge_length_mm: float,
    axis_to_origin_mm: float,
    rotation_zero_deg: float,
    z_zero_tail_mm: float,
    z_zero_pin_mm: float,
) -> List[PlaybackSegment]:
    """
    Convert RD parser segments into board/world coordinates for overlay rendering.
    """
    y_center = edge_length_mm / 2.0
    z_ref = _current_z_reference(board, z_zero_tail_mm, z_zero_pin_mm)
    overlays: List[PlaybackSegment] = []

    for seg in rd_segments:
        x0 = float(seg["x0"])
        y0 = float(seg["y0"])
        x1 = float(seg["x1"])
        y1 = float(seg["y1"])
        z_seg = float(seg.get("z") or seg.get("logical_z") or 0.0)
        y0_board = (
            invert_projected_y(
                y0,
                rotation_deg,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            if board == "pin" and not math.isclose(rotation_deg, rotation_zero_deg, abs_tol=1e-9)
            else y0
        )
        y1_board = (
            invert_projected_y(
                y1,
                rotation_deg,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            if board == "pin" and not math.isclose(rotation_deg, rotation_zero_deg, abs_tol=1e-9)
            else y1
        )
        start_board = (x0, y0_board - y_center, z_seg - z_ref)
        end_board = (x1, y1_board - y_center, z_seg - z_ref)
        overlays.append(
            PlaybackSegment(
                start_board=start_board,
                end_board=end_board,
                start_world=board_to_world_local(
                    start_board[0],
                    start_board[1],
                    start_board[2],
                    rotation_deg,
                    axis_to_origin_mm=axis_to_origin_mm,
                    y_center=y_center,
                    rotation_zero_deg=rotation_zero_deg,
                ),
                end_world=board_to_world_local(
                    end_board[0],
                    end_board[1],
                    end_board[2],
                    rotation_deg,
                    axis_to_origin_mm=axis_to_origin_mm,
                    y_center=y_center,
                    rotation_zero_deg=rotation_zero_deg,
                ),
                start_rotation_deg=rotation_deg,
                end_rotation_deg=rotation_deg,
                board=board,
                is_cut=bool(seg.get("is_cut")),
                duration=0.0,
                power_pct=float(seg.get("power_pct", 0.0)),
                air_assist=bool(seg.get("air_assist", True)),
                source="rd",
            )
        )

    return overlays


def _require_panda3d():
    try:
        from direct.showbase.ShowBase import ShowBase  # type: ignore
        from panda3d.core import (  # type: ignore
            AmbientLight,
            AntialiasAttrib,
            DirectionalLight,
            Geom,
            GeomNode,
            GeomTriangles,
            GeomVertexData,
            GeomVertexFormat,
            GeomVertexWriter,
            LineSegs,
            NodePath,
            OrthographicLens,
            TransparencyAttrib,
            Vec3,
            WindowProperties,
            loadPrcFileData,
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Panda3D is required for the 3D simulator. Install with `pip install panda3d`."
        ) from exc

    return {
        "ShowBase": ShowBase,
        "AmbientLight": AmbientLight,
        "AntialiasAttrib": AntialiasAttrib,
        "DirectionalLight": DirectionalLight,
        "Geom": Geom,
        "GeomNode": GeomNode,
        "GeomTriangles": GeomTriangles,
        "GeomVertexData": GeomVertexData,
        "GeomVertexFormat": GeomVertexFormat,
        "GeomVertexWriter": GeomVertexWriter,
        "LineSegs": LineSegs,
        "NodePath": NodePath,
        "OrthographicLens": OrthographicLens,
        "TransparencyAttrib": TransparencyAttrib,
        "Vec3": Vec3,
        "WindowProperties": WindowProperties,
        "loadPrcFileData": loadPrcFileData,
    }


class Panda3DViewer:
    """
    Lightweight Panda3D scene that replays planner output in 3D.
    """

    def __init__(
        self,
        plan_segments: Sequence[PlaybackSegment],
        overlay_segments: Sequence[PlaybackSegment],
        *,
        axis_to_origin_mm: float,
        edge_length_mm: float,
        board_thickness_mm: float,
        rotation_zero_deg: float = 0.0,
        time_scale: float = 1.0,
        window_size: tuple[int, int] | None = None,
    ) -> None:
        mods = _require_panda3d()
        self._ShowBase = mods["ShowBase"]
        self._LineSegs = mods["LineSegs"]
        self._Vec3 = mods["Vec3"]
        self._AmbientLight = mods["AmbientLight"]
        self._DirectionalLight = mods["DirectionalLight"]
        self._AntialiasAttrib = mods["AntialiasAttrib"]
        self._TransparencyAttrib = mods["TransparencyAttrib"]
        self._OrthographicLens = mods["OrthographicLens"]
        self._Geom = mods["Geom"]
        self._GeomNode = mods["GeomNode"]
        self._GeomTriangles = mods["GeomTriangles"]
        self._GeomVertexData = mods["GeomVertexData"]
        self._GeomVertexFormat = mods["GeomVertexFormat"]
        self._GeomVertexWriter = mods["GeomVertexWriter"]
        self._NodePath = mods["NodePath"]
        self._WindowProperties = mods["WindowProperties"]

        self.axis_to_origin_mm = axis_to_origin_mm
        self.edge_length_mm = edge_length_mm
        self.board_thickness_mm = board_thickness_mm
        self.rotation_zero_deg = rotation_zero_deg
        self.time_scale = time_scale if time_scale > 0 else 1.0
        self.y_center = edge_length_mm / 2.0
        self.plan_segments = list(plan_segments)
        self.overlay_segments = list(overlay_segments)
        self.current_index = 0
        self.elapsed_in_segment = 0.0
        self.beam_padding_up = board_thickness_mm * 0.6 + 10.0
        self.beam_padding_down = board_thickness_mm * 0.5 + 2.0
        self.active_cut_path: list[tuple[float, float]] = []
        self.active_cut_board: str | None = None
        self._close_epsilon = 0.25
        self._logged_segment_index = -1
        self.active_cut_rotation: float | None = None
        self.fly_mode = False
        self._key_state: dict[str, bool] = {}
        self._fly_heading = 0.0
        self._fly_pitch = -20.0
        self._fly_speed = max(40.0, edge_length_mm * 2.0)
        self._fly_turn_rate = 110.0
        self._fly_mouse_sensitivity = 0.15
        self._fly_mouse_center: tuple[int, int] | None = None

        # Enable multisample antialiasing for smoother lines before creating the window.
        win_w, win_h = window_size if window_size else (1600, 1200)
        mods["loadPrcFileData"](
            "",
            f"framebuffer-multisample 1\nmultisamples 4\nwin-size {win_w} {win_h}\n",
        )
        self.base = self._ShowBase()
        self.base.disableMouse()
        self.base.setBackgroundColor(0.05, 0.05, 0.08)
        self.base.render.setAntialias(self._AntialiasAttrib.MAuto)
        self.base.render.setShaderAuto()
        self._views = self._build_view_presets()
        self._setup_camera()
        self._setup_lights()
        self._setup_input()
        self.tail_pivot = None
        self.pin_pivot = None
        self.beam_np = None
        self.beam_marker_np = None
        self.cut_meshes: dict[str, object] = {}
        self.surface_overlay_nodes: dict[str, object] = {}

        log.info(
            "Init Panda3DViewer edge=%.3f thickness=%.3f axis=%.3f segments(plan=%d overlay=%d)",
            edge_length_mm,
            board_thickness_mm,
            axis_to_origin_mm,
            len(self.plan_segments),
            len(self.overlay_segments),
        )

        self._register_hotkeys()

        self._build_boards()
        # Draw overlays (e.g., RD files) up front; plan paths are revealed only as cut occurs.
        if self.overlay_segments:
            self._draw_paths(self.overlay_segments, is_overlay=True)

        if self.plan_segments:
            self.base.taskMgr.add(self._tick, "laserdove_playback")
        else:
            log.info("No plan segments to replay; viewer idle.")

    def _apply_overtravel(self, path: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return path

    # ---------------- Scene setup ----------------
    def _setup_camera(self) -> None:
        self._persp_lens = self.base.camLens
        self._ortho_lens = self._OrthographicLens()
        span = max(self.edge_length_mm, self.axis_to_origin_mm, self.board_thickness_mm) * 1.6
        self._ortho_lens.setFilmSize(span, span)
        self._ortho_lens.setNearFar(0.5, span * 4)
        self._apply_view("0")

    def _setup_lights(self) -> None:
        ambient = self._AmbientLight("ambient")
        ambient.setColor((0.35, 0.35, 0.4, 1))
        ambient_np = self.base.render.attachNewNode(ambient)
        self.base.render.setLight(ambient_np)

        directional = self._DirectionalLight("dir_key")
        directional.setColor((0.65, 0.65, 0.7, 1))
        directional_np = self.base.render.attachNewNode(directional)
        directional_np.setHpr(-35, -45, 0)
        self.base.render.setLight(directional_np)

        fill = self._DirectionalLight("dir_fill")
        fill.setColor((0.35, 0.35, 0.4, 1))
        fill_np = self.base.render.attachNewNode(fill)
        fill_np.setHpr(60, 35, 0)
        self.base.render.setLight(fill_np)

        rim = self._DirectionalLight("dir_rim")
        rim.setColor((0.25, 0.25, 0.3, 1))
        rim_np = self.base.render.attachNewNode(rim)
        rim_np.setHpr(140, -20, 0)
        self.base.render.setLight(rim_np)

        axes = self._LineSegs()
        axes.setThickness(1.6)
        axes.setColor(0.2, 0.2, 0.2, 1)
        grid = 6
        span = self.edge_length_mm * 1.2
        step = span / grid
        for i in range(-grid, grid + 1):
            y = i * step
            axes.moveTo(-span, y, 0)
            axes.drawTo(span, y, 0)
            axes.moveTo(y, -span, 0)
            axes.drawTo(y, span, 0)
        axes_np = self.base.render.attachNewNode(axes.create())
        axes_np.setTransparency(self._TransparencyAttrib.MAlpha)
        axes_np.setColor(0.15, 0.2, 0.25, 0.35)

    def _build_view_presets(
        self,
    ) -> dict[
        str,
        tuple[
            str, tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
        ],
    ]:
        yc = self.y_center
        axis = self.axis_to_origin_mm
        thick = self.board_thickness_mm
        span = max(self.edge_length_mm, thick, axis) * 1.8
        # lens, pos, look_at, up
        return {
            "0": (
                "persp",
                (-self.edge_length_mm * 0.8, -self.edge_length_mm * 1.2, axis * 1.6),
                (0.0, yc, axis * 0.3),
                (0.0, 0.0, 1.0),
            ),
            "1": (
                "ortho",
                (0.0, yc, axis * 3.5 + thick * 2.0),
                (0.0, yc, axis),
                (0.0, 1.0, 0.0),
            ),
            "2": (
                "ortho",
                (0.0, -span, axis),
                (0.0, yc, axis),
                (0.0, 0.0, 1.0),
            ),
            "3": (
                "ortho",
                (span, yc, axis),
                (thick * 0.5, yc, axis),
                (0.0, 0.0, 1.0),
            ),
            "4": (
                "ortho",
                (-span, yc, axis),
                (thick * 0.5, yc, axis),
                (0.0, 0.0, 1.0),
            ),
            "5": (
                "ortho",
                (0.0, yc + span, axis),
                (0.0, yc, axis),
                (0.0, 0.0, 1.0),
            ),
            "6": (
                "ortho",
                (0.0, yc, -axis * 3.5 - thick * 2.0),
                (0.0, yc, axis),
                (0.0, 1.0, 0.0),
            ),
        }

    def _register_hotkeys(self) -> None:
        for key in ("0", "1", "2", "3", "4", "5", "6"):
            self.base.accept(key, self._apply_view, [key])
        self.base.accept("tab", self._toggle_fly_mode)

    def _apply_view(self, key: str) -> None:
        if self.fly_mode:
            return
        preset = self._views.get(key)
        if not preset:
            return
        lens_type, pos, look_at, up_vec = preset
        lens = self._persp_lens if lens_type == "persp" else self._ortho_lens
        self.base.camNode.setLens(lens)
        self.base.camera.setPos(*pos)
        self.base.camera.lookAt(self._Vec3(*look_at), self._Vec3(*up_vec))

    def _setup_input(self) -> None:
        # Track key state for smooth movement.
        for key in (
            "w",
            "a",
            "s",
            "d",
            "q",
            "e",
            "shift",
            "arrow_left",
            "arrow_right",
            "arrow_up",
            "arrow_down",
        ):
            self.base.accept(key, self._set_key, [key, True])
            self.base.accept(f"{key}-up", self._set_key, [key, False])

    def _set_key(self, key: str, pressed: bool) -> None:
        self._key_state[key] = pressed
        if self.fly_mode:
            log.debug("Fly key %s -> %s", key, pressed)

    def _toggle_fly_mode(self) -> None:
        self.fly_mode = not self.fly_mode
        if self.fly_mode:
            self.base.camNode.setLens(self._persp_lens)
            hpr = self.base.camera.getHpr()
            self._fly_heading = hpr.x
            pitch = hpr.y
            # Avoid looking straight up/down; keep an FPS-friendly starting pitch.
            if abs(pitch) > 75.0:
                pitch = math.copysign(45.0, pitch)
            self._fly_pitch = pitch
            self.base.camera.setHpr(self._fly_heading, self._fly_pitch, 0)
            log.info(
                "Fly camera enabled (WASD move, Q/E up/down, mouse look, Tab to exit) "
                "pos=(%.1f, %.1f, %.1f) hpr=(%.1f, %.1f)",
                self.base.camera.getX(),
                self.base.camera.getY(),
                self.base.camera.getZ(),
                self._fly_heading,
                self._fly_pitch,
            )
            self._capture_mouse()
        else:
            log.info("Fly camera disabled; number keys restore preset views.")
            self._release_mouse()

    def _capture_mouse(self) -> None:
        if not self.base.win:
            return
        props = self._WindowProperties()
        props.setCursorHidden(True)
        self.base.win.requestProperties(props)
        self._center_mouse_pointer()

    def _release_mouse(self) -> None:
        if not self.base.win:
            return
        props = self._WindowProperties()
        props.setCursorHidden(False)
        self.base.win.requestProperties(props)
        self._fly_mouse_center = None

    def _center_mouse_pointer(self) -> None:
        if not self.base.win:
            return
        cx = int(self.base.win.getXSize() * 0.5)
        cy = int(self.base.win.getYSize() * 0.5)
        self._fly_mouse_center = (cx, cy)
        self.base.win.movePointer(0, cx, cy)

    def _apply_fly_rotation(self, delta_heading: float, delta_pitch: float) -> None:
        if delta_heading == 0.0 and delta_pitch == 0.0:
            return
        self._fly_heading += delta_heading
        self._fly_pitch = max(-85.0, min(85.0, self._fly_pitch + delta_pitch))
        self.base.camera.setHpr(self._fly_heading, self._fly_pitch, 0)

    def _update_fly_orientation(self, dt: float) -> None:
        if not self.base.win:
            return
        heading_delta = 0.0
        pitch_delta = 0.0

        if self._fly_mouse_center is None:
            self._center_mouse_pointer()
        else:
            pointer = self.base.win.getPointer(0)
            dx = pointer.getX() - self._fly_mouse_center[0]
            dy = pointer.getY() - self._fly_mouse_center[1]
            if dx or dy:
                heading_delta -= dx * self._fly_mouse_sensitivity
                pitch_delta -= dy * self._fly_mouse_sensitivity
                self.base.win.movePointer(0, self._fly_mouse_center[0], self._fly_mouse_center[1])

        turn_rate = self._fly_turn_rate * dt
        if self._key_state.get("arrow_left"):
            heading_delta += turn_rate
        if self._key_state.get("arrow_right"):
            heading_delta -= turn_rate
        if self._key_state.get("arrow_up"):
            pitch_delta += turn_rate
        if self._key_state.get("arrow_down"):
            pitch_delta -= turn_rate

        self._apply_fly_rotation(heading_delta, pitch_delta)

    def _update_fly_camera(self, dt: float) -> None:
        if not self.fly_mode or not self.base.win:
            return

        self._update_fly_orientation(dt)

        move_vec = self._Vec3(0, 0, 0)
        quat = self.base.camera.getQuat()
        forward = quat.xform(self._Vec3(0, 1, 0))
        right = quat.xform(self._Vec3(1, 0, 0))
        up = self._Vec3(0, 0, 1)
        speed = self._fly_speed * (2.0 if self._key_state.get("shift") else 1.0)
        if self._key_state.get("w"):
            move_vec += forward
        if self._key_state.get("s"):
            move_vec -= forward
        if self._key_state.get("a"):
            move_vec -= right
        if self._key_state.get("d"):
            move_vec += right
        if self._key_state.get("q"):
            move_vec -= up
        if self._key_state.get("e"):
            move_vec += up
        if move_vec.length() > 0:
            move_vec.normalize()
            move_vec *= speed * dt
            self.base.camera.setPos(self.base.camera.getPos() + move_vec)
            log.debug(
                "Fly move pos=(%.2f, %.2f, %.2f) hpr=(%.1f, %.1f) vec=(%.2f, %.2f, %.2f)",
                self.base.camera.getX(),
                self.base.camera.getY(),
                self.base.camera.getZ(),
                self.base.camera.getH(),
                self.base.camera.getP(),
                move_vec.x,
                move_vec.y,
                move_vec.z,
            )

    def _finalize_active_cut(self) -> None:
        if self.active_cut_path and self.active_cut_board is not None:
            path = list(self.active_cut_path)
            if _distance(path[0], path[-1]) > self._close_epsilon:
                path.append(path[0])
            path = self._apply_overtravel(path)
            rotation_deg = self.active_cut_rotation if self.active_cut_rotation is not None else 0.0
            self._ensure_cut_mesh(self.active_cut_board).add_hole(path, rotation_deg=rotation_deg)
            self._record_surface_cut(self.active_cut_board, path, rotation_deg=rotation_deg)
            log.debug("Finalized cut loop on %s (%d pts)", self.active_cut_board, len(path))
        self.active_cut_path = []
        self.active_cut_board = None

    def _handle_cut_progress(self, seg: PlaybackSegment) -> None:
        if seg.is_cut:
            if not self.active_cut_path or self.active_cut_board != seg.board:
                self._finalize_active_cut()
                self.active_cut_board = seg.board
                self.active_cut_rotation = seg.start_rotation_deg
                self.active_cut_path = [(seg.start_board[0], seg.start_board[1])]
                log.debug("Start new cut loop on %s at %s", seg.board, seg.start_board)
            self.active_cut_path.append((seg.end_board[0], seg.end_board[1]))
            if (
                len(self.active_cut_path) >= 3
                and _distance(self.active_cut_path[0], self.active_cut_path[-1])
                < self._close_epsilon
            ):
                self._finalize_active_cut()
        else:
            if self.active_cut_path:
                self._finalize_active_cut()
                self.active_cut_rotation = None

    # ---------------- Geometry helpers ----------------
    def _ensure_board_nodes(self, board: str) -> object:
        if board == "tail":
            if self.tail_pivot is None:
                self.tail_pivot = self.base.render.attachNewNode("tail_pivot")
                self.tail_pivot.setPos(0, self.y_center, 0)
            return self.tail_pivot

        if self.pin_pivot is None:
            self.pin_pivot = self.base.render.attachNewNode("pin_pivot")
            self.pin_pivot.setPos(0, self.y_center, 0)
        return self.pin_pivot

    def _ensure_cut_mesh(self, board: str) -> CutMesh:
        if board in self.cut_meshes:
            return self.cut_meshes[board]
        pivot = self._ensure_board_nodes(board)
        height_z = max(self.board_thickness_mm, 1.0)
        color = (0.3, 0.6, 0.9, 0.6) if board == "tail" else (0.9, 0.6, 0.3, 0.6)
        log.debug("Create cut mesh for %s", board)
        mesh = CutMesh(
            pivot,
            geom_factory={
                "Geom": self._Geom,
                "GeomNode": self._GeomNode,
                "GeomTriangles": self._GeomTriangles,
                "GeomVertexData": self._GeomVertexData,
                "GeomVertexFormat": self._GeomVertexFormat,
                "GeomVertexWriter": self._GeomVertexWriter,
                "TransparencyAttrib": self._TransparencyAttrib,
            },
            color=color,
            thickness_x=self.edge_length_mm,
            y_center=self.y_center,
            height_z=height_z,
            z_offset=self.axis_to_origin_mm,
            rotation_zero_deg=self.rotation_zero_deg,
        )
        self.cut_meshes[board] = mesh
        return mesh

    def _ensure_overlay_node(self, board: str) -> object:
        if board in self.surface_overlay_nodes:
            return self.surface_overlay_nodes[board]
        pivot = self._ensure_board_nodes(board)
        node = pivot.attachNewNode(f"{board}_surface_overlays")
        node.setPos(0, 0, self.axis_to_origin_mm)
        self.surface_overlay_nodes[board] = node
        log.debug("Created overlay node for %s", board)
        return node

    def _record_surface_cut(
        self, board: str, path: list[tuple[float, float]], rotation_deg: float = 0.0
    ) -> None:
        """
        Draw a slightly offset overlay of the cut loop on the top and bottom surfaces.
        """
        if len(path) < 2:
            return
        log.debug("Render surface overlay on %s (%d pts)", board, len(path))
        rot_rad = math.radians(rotation_deg - self.rotation_zero_deg)
        y_shift = -self.board_thickness_mm * math.sin(rot_rad)
        overlay_node = self._ensure_overlay_node(board)
        path_closed = path if _distance(path[0], path[-1]) < 1e-6 else (path + [path[0]])
        top_ls = self._LineSegs()
        bot_ls = self._LineSegs()
        top_ls.setThickness(2.6)
        bot_ls.setThickness(2.0)
        top_ls.setColor(0.95, 0.2, 0.2, 0.9)
        bot_ls.setColor(0.25, 0.55, 0.95, 0.7)
        # Keep overlays on the actual local surfaces; nudge a hair inward to avoid z-fighting.
        z_top = -0.01
        z_bot = -self.board_thickness_mm + 0.01
        for idx in range(len(path_closed) - 1):
            x0, y0 = path_closed[idx]
            x1, y1 = path_closed[idx + 1]
            top_ls.moveTo(x0, y0, z_top)
            top_ls.drawTo(x1, y1, z_top)
            bot_ls.moveTo(x0, y0 + y_shift, z_bot)
            bot_ls.drawTo(x1, y1 + y_shift, z_bot)
        top_np = overlay_node.attachNewNode(top_ls.create())
        bot_np = overlay_node.attachNewNode(bot_ls.create())
        for np in (top_np, bot_np):
            np.setTransparency(self._TransparencyAttrib.MAlpha)

    def _build_boards(self) -> None:
        if any(seg.board == "tail" for seg in self.plan_segments + self.overlay_segments):
            self._ensure_cut_mesh("tail")
        if any(seg.board == "pin" for seg in self.plan_segments + self.overlay_segments):
            self._ensure_cut_mesh("pin")

    def _draw_paths(self, segments: Sequence[PlaybackSegment], *, is_overlay: bool) -> None:
        if not segments:
            return
        cut_color = (1.0, 0.2, 0.1, 0.9) if not is_overlay else (1.0, 0.7, 0.2, 0.8)
        travel_color = (0.4, 0.55, 0.7, 0.5) if not is_overlay else (0.6, 0.6, 0.6, 0.45)
        for board in ("tail", "pin"):
            board_segments = [seg for seg in segments if seg.board == board]
            if not board_segments:
                continue
            log.debug(
                "Drawing %d %s segments on %s",
                len(board_segments),
                "overlay" if is_overlay else "plan",
                board,
            )
            ls = self._LineSegs()
            for seg in board_segments:
                color = cut_color if seg.is_cut else travel_color
                ls.setColor(*color)
                ls.setThickness(3.0 if seg.is_cut else 1.6)
                ls.moveTo(*seg.start_board)
                ls.drawTo(*seg.end_board)
            pivot = self._ensure_board_nodes(board)
            geom_np = pivot.attachNewNode(ls.create())
            geom_np.setTransparency(self._TransparencyAttrib.MAlpha)
            geom_np.setPos(0, 0, self.axis_to_origin_mm)

    # ---------------- Playback ----------------
    def _update_beam(self, position: Tuple[float, float, float], is_cut: bool) -> None:
        if self.beam_np is not None:
            self.beam_np.removeNode()
        if self.beam_marker_np is not None:
            self.beam_marker_np.removeNode()
        ls = self._LineSegs()
        ls.setThickness(3.2 if is_cut else 1.8)
        ls.setColor(1.0, 0.1, 0.1, 0.9 if is_cut else 0.4)
        x, y, z = position
        ls.moveTo(x, y, z + self.beam_padding_up)
        ls.drawTo(x, y, z - self.beam_padding_down)
        self.beam_np = self.base.render.attachNewNode(ls.create())
        # Add a small crosshair at the beam location so it remains visible in top-ortho view.
        mark = self._LineSegs()
        mark.setThickness(4.0 if is_cut else 2.5)
        mark.setColor(1.0, 0.3, 0.2, 0.95 if is_cut else 0.6)
        r = 2.0
        z_mark = z + 0.2  # slight lift to avoid z-fighting
        mark.moveTo(x - r, y, z_mark)
        mark.drawTo(x + r, y, z_mark)
        mark.moveTo(x, y - r, z_mark)
        mark.drawTo(x, y + r, z_mark)
        self.beam_marker_np = self.base.render.attachNewNode(mark.create())
        self.beam_marker_np.setTransparency(self._TransparencyAttrib.MAlpha)

    def _tick(self, task):
        if self.current_index >= len(self.plan_segments):
            self._finalize_active_cut()
            return task.done
        dt = self.base.taskMgr.globalClock.getDt() * self.time_scale
        self._update_fly_camera(dt)
        seg = self.plan_segments[self.current_index]
        if self.current_index != self._logged_segment_index:
            log.debug(
                "Begin segment %d type=%s cut=%s start=(%.3f,%.3f,%.3f) end=(%.3f,%.3f,%.3f) rot=%.3f->%.3f",
                self.current_index,
                seg.source,
                seg.is_cut,
                seg.start_board[0],
                seg.start_board[1],
                seg.start_board[2],
                seg.end_board[0],
                seg.end_board[1],
                seg.end_board[2],
                seg.start_rotation_deg,
                seg.end_rotation_deg,
            )
            self._logged_segment_index = self.current_index
        self.elapsed_in_segment += dt
        duration = max(seg.duration, 1e-6)
        progress = min(self.elapsed_in_segment / duration, 1.0)

        interp_rot = (
            seg.start_rotation_deg + (seg.end_rotation_deg - seg.start_rotation_deg) * progress
        )
        if seg.board == "pin" and self.pin_pivot is not None:
            self.pin_pivot.setP(interp_rot - self.rotation_zero_deg)
        if seg.board == "tail" and self.tail_pivot is not None:
            self.tail_pivot.setP(interp_rot - self.rotation_zero_deg)

        start_vec = self._Vec3(*seg.start_world)
        end_vec = self._Vec3(*seg.end_world)
        current_vec = start_vec + (end_vec - start_vec) * progress
        self._update_beam((current_vec.x, current_vec.y, current_vec.z), seg.is_cut)

        if progress >= 0.999:
            self._handle_cut_progress(seg)
            self.current_index += 1
            self.elapsed_in_segment = 0.0
        return task.cont

    def run(self) -> None:
        """Start the Panda3D app loop."""
        self.base.run()
