import math
import networkx as nx
import numpy as np
from specklepy.objects.geometry import Point, Line, Polycurve
from matplotlib.path import Path
from collections import defaultdict
from rtree import index
from tqdm import tqdm


# -----------------------------------------------------------------------------
# Room-Separator Workaround
# -----------------------------------------------------------------------------
# Speckle Revit exports often include Room outlines but may omit Room Separator
# elements (OST_RoomSeparationLines). In your grid logic, nodes are only connected
# within the same room_id, which turns Room Separator boundaries into *virtual
# walls*.
#
# The helper below clusters rooms that share a boundary segment *without* a wall
# baseline on that segment, and lets the grid connect across those boundaries
# while keeping per-node original room_id / room_name.

from collections import defaultdict


def compute_open_room_clusters(rooms, walls, tol=0.05, min_shared_len=0.20, wall_tol=0.20, angle_tol_deg=10.0):
    """
    Build room clusters that should behave as a single open area for pathfinding.

    A and B are put in the same cluster when:
      1) Their outlines share a boundary segment (within `tol`), AND
      2) There is *no* wall baseline overlapping that shared segment (within `wall_tol`).

    This approximates Revit Room Separator lines (non-physical boundaries) when
    those separators are not present in the exported Speckle model.

    Returns: dict room_id(str) -> cluster_id(str)
    """

    import math

    def to_meters(val):
        return val / 1000.0 if abs(val) > 100 else val

    def room_id_of(r):
        rid = getattr(r, 'id', None) or getattr(r, 'elementId', None)
        return str(rid) if rid is not None else None

    # --- Union-Find ---------------------------------------------------------
    parent = {}

    def find(x):
        # path compression
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # init
    room_ids = []
    for r in rooms or []:
        rid = room_id_of(r)
        if rid:
            room_ids.append(rid)
            parent.setdefault(rid, rid)

    if not room_ids:
        return {}

    # --- Wall baselines index ----------------------------------------------
    wall_lines = []  # (x1,y1,x2,y2) in meters
    for w in walls or []:
        bl = getattr(w, 'baseLine', None)
        if bl and hasattr(bl, 'start') and hasattr(bl, 'end'):
            try:
                x1, y1 = to_meters(bl.start.x), to_meters(bl.start.y)
                x2, y2 = to_meters(bl.end.x),   to_meters(bl.end.y)
                wall_lines.append((x1, y1, x2, y2))
            except Exception:
                pass

    wall_idx = index.Index()
    for i, (x1, y1, x2, y2) in enumerate(wall_lines):
        wall_idx.insert(i, (min(x1, x2) - wall_tol, min(y1, y2) - wall_tol, max(x1, x2) + wall_tol, max(y1, y2) + wall_tol))

    def dist_point_to_segment(px, py, ax, ay, bx, by):
        # standard projection
        vx, vy = bx - ax, by - ay
        wx, wy = px - ax, py - ay
        c1 = vx * wx + vy * wy
        if c1 <= 0:
            return math.hypot(px - ax, py - ay)
        c2 = vx * vx + vy * vy
        if c2 <= c1:
            return math.hypot(px - bx, py - by)
        t = c1 / c2
        projx, projy = ax + t * vx, ay + t * vy
        return math.hypot(px - projx, py - projy)

    cos_ang = math.cos(math.radians(angle_tol_deg))

    def wall_overlaps_segment(xa, ya, xb, yb):
        if not wall_lines:
            return False

        # segment bbox
        bb = (min(xa, xb) - wall_tol, min(ya, yb) - wall_tol, max(xa, xb) + wall_tol, max(ya, yb) + wall_tol)
        mx, my = (xa + xb) / 2.0, (ya + yb) / 2.0

        # seg dir
        sx, sy = xb - xa, yb - ya
        sl = math.hypot(sx, sy)
        if sl == 0:
            return False
        sx, sy = sx / sl, sy / sl

        for wi in wall_idx.intersection(bb):
            x1, y1, x2, y2 = wall_lines[wi]
            wx, wy = x2 - x1, y2 - y1
            wl = math.hypot(wx, wy)
            if wl == 0:
                continue
            wxu, wyu = wx / wl, wy / wl

            # nearly parallel?
            if abs(sx * wxu + sy * wyu) < cos_ang:
                continue

            # close enough? (midpoint distance to wall segment)
            if dist_point_to_segment(mx, my, x1, y1, x2, y2) > wall_tol:
                continue

            # overlap check via projection onto wall axis
            # project segment endpoints onto wall axis from wall start
            tA = (xa - x1) * wxu + (ya - y1) * wyu
            tB = (xb - x1) * wxu + (yb - y1) * wyu
            seg_min, seg_max = (tA, tB) if tA <= tB else (tB, tA)
            ov = min(seg_max, wl) - max(seg_min, 0.0)
            if ov >= min_shared_len * 0.80:
                return True

        return False

    # --- Shared boundary segments ------------------------------------------
    # map quantized (p,q) -> list[(room_id, (xa,ya,xb,yb), length)]
    seg_map = defaultdict(list)

    def q(v):
        return round(v / tol)  # integer bucket

    def sig(xa, ya, xb, yb):
        a = (q(xa), q(ya))
        b = (q(xb), q(yb))
        return (a, b) if a <= b else (b, a)

    def seg_endpoints(seg):
        s = getattr(seg, 'start', None) or getattr(seg, 'startPoint', None)
        e = getattr(seg, 'end', None)   or getattr(seg, 'endPoint', None)
        if s is not None and e is not None:
            return s, e
        return None

    for r in rooms or []:
        rid = room_id_of(r)
        if not rid:
            continue
        outline = getattr(r, 'outline', None)
        if not outline or not hasattr(outline, 'segments'):
            continue
        for seg in outline.segments:
            pts = seg_endpoints(seg)
            if not pts:
                continue
            s, e = pts
            try:
                xa, ya = to_meters(s.x), to_meters(s.y)
                xb, yb = to_meters(e.x), to_meters(e.y)
            except Exception:
                continue
            L = math.hypot(xb - xa, yb - ya)
            if L < min_shared_len:
                continue
            seg_map[sig(xa, ya, xb, yb)].append((rid, (xa, ya, xb, yb), L))

    merges = 0
    # iterate shared segments and union rooms if not wall-backed
    for _, items in seg_map.items():
        room_set = sorted({it[0] for it in items})
        if len(room_set) < 2:
            continue

        # pick a representative segment (longest) for the wall test
        rep = max(items, key=lambda t: t[2])[1]
        xa, ya, xb, yb = rep

        if wall_overlaps_segment(xa, ya, xb, yb):
            continue  # a physical wall exists → DO NOT merge

        # merge all rooms that share this open boundary
        root = room_set[0]
        for other in room_set[1:]:
            before = (find(root), find(other))
            union(root, other)
            after = (find(root), find(other))
            if before != after:
                merges += 1

    # compress
    clusters = {rid: find(rid) for rid in parent.keys()}

    if merges:
        # Optional console visibility for debugging
        print(f"🧩 Room-separator merge: {merges} union(s) → {len(set(clusters.values()))} cluster(s)")

    return clusters

def _param_value(defn, key: str):
    """Read Revit parameter 'key' from door.definition.parameters, tolerant of shapes."""
    if not defn:
        return None
    params = getattr(defn, "parameters", None) or getattr(defn, "__dict__", {}).get("parameters") or {}
    if isinstance(params, dict):
        pv = params.get(key)
        if isinstance(pv, dict):
            return pv.get("value")
        return pv if isinstance(pv, str) else None
    # Base-like
    pv = getattr(params, key, None)
    if isinstance(pv, dict):
        return pv.get("value")
    if hasattr(pv, "value"):
        return getattr(pv, "value")
    return pv if isinstance(pv, str) else None


def group_rooms_by_level(rooms, level_alias_map=None):

    grouped = defaultdict(list)
    missing_level = []

    for room in rooms:
        # primary: room.level.name
        level_obj = getattr(room, "level", None)
        level_name = getattr(level_obj, "name", None) if level_obj else None

        # fallback: parameters.LEVEL_NAME.value (Speckle Base)
        if not (isinstance(level_name, str) and level_name.strip()):
            params = getattr(room, "parameters", None)
            level_param = getattr(params, "LEVEL_NAME", None)
            level_name = getattr(level_param, "value", None) or getattr(level_param, "name", None)

        # last tiny fallback (optional): your existing helper for "Level"
        if not (isinstance(level_name, str) and level_name.strip()):
            level_name = _param_value(getattr(room, "parameters", None), "Level")

        if isinstance(level_name, str) and level_name.strip():
            level_key = level_name.strip()
            if level_alias_map and level_key in level_alias_map:
                level_key = level_alias_map[level_key]
            grouped[level_key].append(room)
        else:
            missing_level.append(room)
            grouped["❌ MISSING"].append(room)

    print("\n📋 Grouped room counts by level.name:")
    for lvl, group in sorted(grouped.items()):
        print(f"  • {lvl}: {len(group)} room(s)")

    if missing_level:
        print(f"\n⚠️ Rooms missing level.name: {len(missing_level)}")
        for r in missing_level:
            print(f"  - ID: {getattr(r, 'elementId', '?')} | Name: {getattr(r, 'name', '?')}")
            # print("\n".join([f"    • {k}: {_param_value(getattr(r,'parameters',None), k)}" for k in (getattr(getattr(r,'parameters',None),'get_dynamic_member_names', lambda: list(getattr(getattr(r,'parameters',None),'__dict__',{}).keys()))())]))


    return grouped

def group_walls_by_level(walls, level_alias_map=None):

    grouped = defaultdict(list)
    missing_level = []

    for wall in walls:
        level_obj = getattr(wall, "level", None)
        level_name = getattr(level_obj, "name", None) if level_obj else None

        if level_name and isinstance(level_name, str) and level_name.strip():
            level_key = level_name.strip()
            if level_alias_map and level_key in level_alias_map:
                level_key = level_alias_map[level_key]
            grouped[level_key].append(wall)
        else:
            missing_level.append(wall)
            grouped["❌ MISSING"].append(wall)

    print("\n🧱 Grouped wall counts by level.name:")
    for lvl, group in sorted(grouped.items()):
        print(f"  • {lvl}: {len(group)} wall(s)")

    if missing_level:
        print(f"\n⚠️ Walls missing level.name: {len(missing_level)}")
        for w in missing_level:
            print(f"  - ID: {getattr(w, 'elementId', '?')} | Name: {getattr(w, 'name', '?')}")

    return grouped

def group_doors_by_level(doors, level_alias_map=None):

    grouped = defaultdict(list)
    missing_level = []

    for door in doors:
        # primary: door.level.name
        level_obj = getattr(door, "level", None)
        level_name = getattr(level_obj, "name", None) if level_obj else None

        # fallback: parameters.LEVEL_NAME.value (Speckle Base)
        if not (isinstance(level_name, str) and level_name.strip()):
            params = getattr(door, "parameters", None)
            level_param = getattr(params, "SCHEDULE_LEVEL_PARAM", None)
            level_name = getattr(level_param, "value", None) or getattr(level_param, "name", None)

        # last tiny fallback (optional): your existing helper for "Level"
        if not (isinstance(level_name, str) and level_name.strip()):
            level_name = _param_value(getattr(door, "parameters", None), "Level")

        if isinstance(level_name, str) and level_name.strip():
            level_key = level_name.strip()
            if level_alias_map and level_key in level_alias_map:
                level_key = level_alias_map[level_key]
            grouped[level_key].append(door)
        else:
            missing_level.append(door)
            grouped["❌ MISSING"].append(door)

    print("\n🚪 Grouped door counts by level.name:")
    for lvl, group in sorted(grouped.items()):
        print(f"  • {lvl}: {len(group)} door(s)")

    if missing_level:
        print(f"\n⚠️ Doors missing level.name: {len(missing_level)}")
        for d in missing_level:
            print(f"  - ID: {getattr(d, 'elementId', '?')} | Name: {getattr(d, 'name', '?')}")
            # print("\n".join([f"    • {k}: {_param_value(getattr(d,'parameters',None), k)}" for k in (getattr(getattr(d,'parameters',None),'get_dynamic_member_names', lambda: list(getattr(getattr(d,'parameters',None),'__dict__',{}).keys()))())]))

    return grouped

def group_stairs_by_level(stairs, level_alias_map=None):

    grouped = defaultdict(list)
    missing_level = []

    for stair in stairs:
        level_obj = getattr(stair, "level", None)
        level_name = getattr(level_obj, "name", None) if level_obj else None

        if level_name and isinstance(level_name, str) and level_name.strip():
            level_key = level_name.strip()
            if level_alias_map and level_key in level_alias_map:
                level_key = level_alias_map[level_key]
            grouped[level_key].append(stair)
        else:
            missing_level.append(stair)
            grouped["❌ MISSING"].append(stair)

    print("\n🧗 Grouped stair counts by level.name:")
    for lvl, group in sorted(grouped.items()):
        print(f"  • {lvl}: {len(group)} stair(s)")

    if missing_level:
        print(f"\n⚠️ Stairs missing level.name: {len(missing_level)}")
        for s in missing_level:
            print(f"  - ID: {getattr(s, 'elementId', '?')} | Type: {getattr(s, 'type', '?')}")

    return grouped

def group_columns_by_level(columns, level_alias_map=None):
    from collections import defaultdict
    grouped = defaultdict(list)
    missing = []
    for col in columns or []:
        lvl = getattr(col, "level", None)
        name = getattr(lvl, "name", None) if lvl else None
        if isinstance(name, str) and name.strip():
            key = name.strip()
            if level_alias_map and key in level_alias_map:
                key = level_alias_map[key]
            grouped[key].append(col)
        else:
            missing.append(col)
            grouped["❌ MISSING"].append(col)

    print("\n🗼 Grouped column counts by level.name:")
    for lvl, arr in sorted(grouped.items()):
        print(f"  • {lvl}: {len(arr)} column(s)")

    if missing:
        print(f"\n⚠️ Columns missing level.name: {len(missing)}")
    return grouped


def group_railings_by_level(railings, level_alias_map=None):
    from collections import defaultdict
    grouped = defaultdict(list)
    missing = []
    for rl in railings or []:
        lvl = getattr(rl, "level", None)
        name = getattr(lvl, "name", None) if lvl else None
        if isinstance(name, str) and name.strip():
            key = name.strip()
            if level_alias_map and key in level_alias_map:
                key = level_alias_map[key]
            grouped[key].append(rl)
        else:
            missing.append(rl)
            grouped["❌ MISSING"].append(rl)

    print("\n🛡️ Grouped railing counts by level.name:")
    for lvl, arr in sorted(grouped.items()):
        print(f"  • {lvl}: {len(arr)} railing(s)")

    if missing:
        print(f"\n⚠️ Railings missing level.name: {len(missing)}")
    return grouped


def assign_room_metadata_to_nodes(G_floor, rooms_on_level):
    """
    Assign a room_id and room_name to every node:
    1. Direct match using room bounding box
    2. If no match, assign to the closest room center (Euclidean distance)
    """

    # Precompute bounding boxes and centers for rooms
    room_info = []
    for room in rooms_on_level:
        outline = getattr(room, "outline", None)
        if not outline or not hasattr(outline, "vertices"):
            continue

        xs = [v.x for v in outline.vertices]
        ys = [v.y for v in outline.vertices]
        if not xs or not ys:
            continue

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2

        room_info.append({
            "room": room,
            "bbox": (min_x, max_x, min_y, max_y),
            "center": (center_x, center_y),
        })

    # Step 1: Bounding box assignment
    for node, data in G_floor.nodes(data=True):
        if data.get("room_id") and data.get("room_name"):
            continue

        node_x, node_y = node[0], node[1]

        assigned = False
        for info in room_info:
            min_x, max_x, min_y, max_y = info["bbox"]
            if min_x <= node_x <= max_x and min_y <= node_y <= max_y:
                room = info["room"]
                data["room_id"] = getattr(room, "id", None)
                name = getattr(room, "name", None)
                data["room_name"] = name if name not in (None, "?") else "UNNAMED_ROOM"
                assigned = True
                break

        if not assigned:
            data["room_id"] = None
            data["room_name"] = None

    # Step 2: Closest room fallback
    for node, data in G_floor.nodes(data=True):
        if data.get("room_id") and data.get("room_name"):
            continue

        node_x, node_y = node[0], node[1]

        # Find closest room center
        min_dist = float("inf")
        closest_room = None
        for info in room_info:
            cx, cy = info["center"]
            dist = math.dist([node_x, node_y], [cx, cy])
            if dist < min_dist:
                min_dist = dist
                closest_room = info["room"]

        if closest_room:
            data["room_id"] = getattr(closest_room, "id", None)
            name = getattr(closest_room, "name", None)
            data["room_name"] = name if name not in (None, "?") else "UNNAMED_ROOM"


def compute_global_bounds(rooms, walls, doors):

    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")
    all_z = []

    def update_bounds(x, y):
        nonlocal min_x, min_y, max_x, max_y
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x), max(max_y, y)

    def to_m(val):
        return val / 1000.0 if abs(val) > 100 else val

    def _seg_endpoints(seg):
        """Return iterable of endpoints for a segment (Line or Arc-like)."""
        # Lines have .start/.end; Arcs may expose .startPoint/.endPoint
        s = getattr(seg, "start", None) or getattr(seg, "startPoint", None)
        e = getattr(seg, "end",   None) or getattr(seg, "endPoint",   None)
        if s is not None and e is not None:
            return (s, e)
        # Fallback: nothing usable
        return ()

    # Rooms
    for r in rooms:
        outline = getattr(r, "outline", None)
        if outline and hasattr(outline, "segments"):
            for seg in outline.segments:
                for pt in _seg_endpoints(seg):
                    try:
                        update_bounds(to_m(pt.x), to_m(pt.y))
                        all_z.append(to_m(pt.z))
                    except Exception:
                        # Skip any malformed point without changing behavior elsewhere
                        continue

    # Walls
    for w in walls:
        if hasattr(w, "baseLine"):
            try:
                for pt in [w.baseLine.start, w.baseLine.end]:
                    update_bounds(to_m(pt.x), to_m(pt.y))
                    all_z.append(to_m(pt.z))
            except Exception:
                # Skip malformed wall baselines
                pass

    # Doors (use transform center)
    for d in doors:
        m = getattr(d, "transform", None)
        if m and hasattr(m, "matrix") and len(m.matrix) >= 16:
            try:
                update_bounds(to_m(m.matrix[3]), to_m(m.matrix[7]))
            except Exception:
                pass

    return {
        "min_x": min_x, "max_x": max_x,
        "min_y": min_y, "max_y": max_y,
        "avg_z": sum(all_z) / len(all_z) if all_z else 0.0
    }


def generate_gridlines_per_room(rooms, walls, doors=None, spacing=3.0, gap_offset=0.5, level_name=None):

    def to_m(val): return val / 1000.0

    print(f"📐 Generating per-room grid for level: {level_name or '[unspecified]'}")

    global_min_x, global_min_y = float("inf"), float("inf")
    global_max_x, global_max_y = float("-inf"), float("-inf")
    all_z = []
    room_polygons = []

    def update_bounds(x, y):
        nonlocal global_min_x, global_min_y, global_max_x, global_max_y
        global_min_x = min(global_min_x, x)
        global_min_y = min(global_min_y, y)
        global_max_x = max(global_max_x, x)
        global_max_y = max(global_max_y, y)

    # Step 1: Room polygons
    for room in rooms:
        outline = getattr(room, "outline", None)
        if not isinstance(outline, Polycurve):
            continue

        polygon = []
        for seg in outline.segments:
            if hasattr(seg, "start") and hasattr(seg, "end"):
                x1, y1 = to_m(seg.start.x), to_m(seg.start.y)
                x2, y2 = to_m(seg.end.x), to_m(seg.end.y)
                polygon.append((x1, y1))
                update_bounds(x1, y1)
                update_bounds(x2, y2)
                all_z.extend([to_m(seg.start.z), to_m(seg.end.z)])

        if polygon:
            room_polygons.append((getattr(room, "elementId", None), polygon))
        else:
            print(f"⚠️ Room {getattr(room, 'elementId', '?')} has no usable outline.")

    # Step 2: Expand bounds with walls
    for wall in walls or []:
        if hasattr(wall, "baseLine") and isinstance(wall.baseLine, Line):
            sx, sy = to_m(wall.baseLine.start.x), to_m(wall.baseLine.start.y)
            ex, ey = to_m(wall.baseLine.end.x), to_m(wall.baseLine.end.y)
            update_bounds(sx, sy)
            update_bounds(ex, ey)
            all_z.extend([to_m(wall.baseLine.start.z), to_m(wall.baseLine.end.z)])

    # Step 3: Expand bounds with doors
    for door in doors or []:
        if hasattr(door, "transform") and isinstance(door.transform, object):
            m = getattr(door.transform, "matrix", None)
            if isinstance(m, list) and len(m) >= 16:
                dx, dy = to_m(m[3]), to_m(m[7])
                update_bounds(dx, dy)

    # Step 4: Safety check
    if global_min_x == float("inf") or not all_z:
        print("⚠️ No valid geometry found to build grid.")
        return [], None, {}

    avg_level_z = sum(all_z) / len(all_z)

    # Step 5: Adjust spacing
    max_points = 15000
    width = global_max_x - global_min_x
    height = global_max_y - global_min_y
    grid_x_count = int(width / spacing)
    grid_y_count = int(height / spacing)

    if grid_x_count * grid_y_count > max_points:
        scale = ((grid_x_count * grid_y_count) / max_points) ** 0.5
        spacing *= scale
        print(f"⚠️ Grid too dense. Increased spacing to {spacing:.2f}m")

    # Step 6: Generate global grid
    x_vals = np.arange(global_min_x - gap_offset, global_max_x + spacing + gap_offset, spacing)
    y_vals = np.arange(global_min_y - gap_offset, global_max_y + spacing + gap_offset, spacing)

    print(f"🧱 Grid extent: X={len(x_vals)}, Y={len(y_vals)} → Total points: {len(x_vals) * len(y_vals)}")

    all_trimmed_lines = []
    combined_grid_dict = {}

    for room_id, polygon in room_polygons:
        path = Path(polygon)
        grid_dict = {}

        for ix, x in enumerate(x_vals):
            for iy, y in enumerate(y_vals):
                if path.contains_point((x, y)):
                    grid_dict[(ix, iy)] = Point(x=float(x), y=float(y), z=avg_level_z, units="m")

        grid_points = list(grid_dict.items())
        local_edges = []
        for (ix, iy), pt in grid_dict.items():
            for dx, dy in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
                neighbor = (ix + dx, iy + dy)
                if neighbor in grid_dict:
                    local_edges.append((grid_dict[(ix, iy)], grid_dict[neighbor], None))

        print(f"✅ Room {room_id} → {len(local_edges)} edges, {len(grid_points)} nodes")
        all_trimmed_lines.extend(local_edges)

    return all_trimmed_lines, avg_level_z, combined_grid_dict

def generate_extended_gridlines_per_floor(
    rooms, walls, doors=None, spacing=1.0, max_points=15000, level_name=None, global_bounds=None,
    allow_room_separator_merge=True, room_sep_tol=0.05, room_sep_min_len=0.20, room_sep_wall_tol=0.20
):

    def to_meters(val):
        return val / 1000.0 if abs(val) > 100 else val

    print(f"📐 Generating grid for level: {level_name or '[unspecified]'}")

    if not global_bounds:
        raise ValueError("Global bounds required for grid generation")

    min_x = global_bounds["min_x"]
    max_x = global_bounds["max_x"]
    min_y = global_bounds["min_y"]
    max_y = global_bounds["max_y"]
    avg_z = global_bounds["avg_z"]

    x_vals = np.arange(min_x, max_x + spacing, spacing)
    y_vals = np.arange(min_y, max_y + spacing, spacing)

    print(f"🧱 Grid dimensions: {len(x_vals)} cols × {len(y_vals)} rows = {len(x_vals) * len(y_vals)} points")

    grid_dict = {}
    for iy, y in enumerate(y_vals):
        for ix, x in enumerate(x_vals):
            pt = Point(x=round(x, 4), y=round(y, 4), z=avg_z, units="m")
            grid_dict[(ix, iy)] = pt

    # Precompute room polygons
    room_polygons = []
    for room in rooms:
        outline = getattr(room, "outline", None)
        if outline and hasattr(outline, "segments"):

            def _seg_endpoints(seg):
                s = getattr(seg, "start", None) or getattr(seg, "startPoint", None)
                e = getattr(seg, "end",   None) or getattr(seg, "endPoint",   None)
                if s is not None and e is not None:
                    return (s, e)
                return ()

            points = []
            for seg in outline.segments:
                for pt in _seg_endpoints(seg):
                    try:
                        points.append((to_meters(pt.x), to_meters(pt.y)))
                    except Exception:
                        # Skip malformed points without changing overall logic
                        continue

            if len(points) >= 3:
                poly = Path(points)
                room_id = getattr(room, "id", None) or getattr(room, "elementId", None)
                room_id = str(room_id) if room_id is not None else None
                room_polygons.append((room_id, poly))


    # Optional: merge rooms separated only by Room Separator lines (no wall baseline)
    room_cluster_map = {}
    if allow_room_separator_merge:
        try:
            room_cluster_map = compute_open_room_clusters(
                rooms, walls,
                tol=room_sep_tol,
                min_shared_len=room_sep_min_len,
                wall_tol=room_sep_wall_tol,
            )
        except Exception as e:
            print(f"⚠️ Room-separator merge failed (ignored): {e}")

    # Step 1: tag grid nodes with room_id (inside room only)
    grid_node_room_ids = {}
    for (ix, iy), pt in grid_dict.items():
        for rid, poly in room_polygons:
            if poly.contains_point((pt.x, pt.y)):
                grid_node_room_ids[(ix, iy)] = rid
                break

    # Step 2: prune grid_dict to only include nodes inside a room
    grid_dict = {
        (ix, iy): pt
        for (ix, iy), pt in grid_dict.items()
        if (ix, iy) in grid_node_room_ids
    }    # Step 3: connect nodes within the same room *or* within the same "open cluster"
    # (rooms divided only by Room Separator lines).
    edges = []

    def _cluster_of(rid: str):
        if not rid:
            return None
        rid = str(rid)
        return room_cluster_map.get(rid, rid) if room_cluster_map else rid

    for iy in range(len(y_vals)):
        for ix in range(len(x_vals)):
            if (ix, iy) not in grid_dict:
                continue

            pt = grid_dict[(ix, iy)]
            rid_a = grid_node_room_ids.get((ix, iy))
            ca = _cluster_of(rid_a)

            for dx, dy in [(1, 0), (0, 1)]:
                neighbor_key = (ix + dx, iy + dy)
                if neighbor_key not in grid_dict:
                    continue

                neighbor = grid_dict[neighbor_key]
                rid_b = grid_node_room_ids.get(neighbor_key)
                cb = _cluster_of(rid_b)

                # allow cross-room edges if rooms are in the same open cluster
                if rid_a and rid_b and ca and cb and ca == cb:
                    edges.append((pt, neighbor, {"a": str(rid_a), "b": str(rid_b)}))

    print(f"🔗 Total grid edges (after trimming): {len(edges)}")
    print(f"📉 Retained {len(grid_dict)} grid nodes inside rooms")

    return edges, avg_z, grid_dict

def create_graph(walls=None, rooms=None, doors=None, gridlines=None, room_boundaries=None, level_name=None, columns=None, railings=None):
    import math
    import networkx as nx
    from specklepy.objects.geometry import Point, Line

    def to_m(val):
        return val / 1000.0 if abs(val) > 100 else val

    G = nx.Graph()
    wall_lines_2d = []
    wall_metas = []   # <— NEW: we’ll return these via /graph/exits

    # --- STEP 0: Build room_id → room_name mapping
    room_id_to_name = {
        str(getattr(r, "id", getattr(r, "elementId", None))): (getattr(r, "name", None) or "?")
        for r in rooms or []
        if (getattr(r, "id", None) or getattr(r, "elementId", None)) is not None
    }

    # --- helpers
    def point_to_tuple(p: Point):
        return (round(p.x, 4), round(p.y, 4), round(p.z, 4))

    def euclidean_distance_2d(a, b):
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)

    # Safety helper: ensure object has x, y, z (i.e., a proper Speckle Point-like)
    def _is_point3d(o) -> bool:
        return hasattr(o, "x") and hasattr(o, "y") and hasattr(o, "z")    # --- STEP 3: Add grid nodes and edges (skip any malformed tuple/list endpoints)
    # Supports:
    #   - legacy meta = room_id
    #   - new meta = {"a": room_id_for_start, "b": room_id_for_end}
    #   - door bridges meta = "door_*" (ignored for room tagging)
    skipped = 0

    def _maybe_set_room(node_key, rid):
        if not rid:
            return
        rid_s = str(rid)
        # do not overwrite an existing assignment
        if "room_id" not in G.nodes[node_key]:
            G.nodes[node_key]["room_id"] = rid_s
            G.nodes[node_key]["room_name"] = room_id_to_name.get(rid_s, "?")

    for idx, item in enumerate(gridlines or []):
        try:
            start, end, meta = item
        except Exception:
            skipped += 1
            continue

        if not (_is_point3d(start) and _is_point3d(end)):
            # Safety net: ignore lines whose endpoints are not Point-like
            skipped += 1
            continue

        p1 = point_to_tuple(start)
        p2 = point_to_tuple(end)
        dist = euclidean_distance_2d(start, end)

        # Determine per-endpoint room ids (if any)
        rid1 = rid2 = None
        if isinstance(meta, dict):
            rid1 = meta.get("a") or meta.get("room_a")
            rid2 = meta.get("b") or meta.get("room_b")
        else:
            # legacy: treat as room id only if it looks like one (string/int)
            # We avoid tagging for door_* bridges etc.
            if meta is not None and not (isinstance(meta, str) and str(meta).lower().startswith("door_")):
                rid1 = rid2 = meta

        # add nodes (no overwrite)
        G.add_node(p1)
        G.add_node(p2)
        _maybe_set_room(p1, rid1)
        _maybe_set_room(p2, rid2)

        G.add_edge(p1, p2, weight=dist)

    if skipped:
        print(f"⚠️ Skipped {skipped} gridline(s) with non-Point endpoints (e.g., tuples).")

    # --- STEP 3.5: Tag door nodes with neighboring room_ids (unchanged)
    for node, data in G.nodes(data=True):
        if data.get("type") == "door":
            connected_room_ids = []
            connected_room_names = []
            for neighbor in G.neighbors(node):
                nd = G.nodes[neighbor]
                rid, rname = nd.get("room_id"), nd.get("room_name")
                if rid and rid not in connected_room_ids:
                    connected_room_ids.append(rid)
                if rname and rname not in connected_room_names:
                    connected_room_names.append(rname)
            if connected_room_ids:
                data["room_id"] = connected_room_ids[0]
                data["room_name"] = connected_room_names[0] if connected_room_names else "?"
                data["connected_rooms"] = connected_room_ids
                data["connected_room_names"] = connected_room_names

    # --- STEP 4: Add wall visualization + wall metadata
    for wall in walls or []:
        # 4a) 2D line for your preview overlay (unchanged)
        if hasattr(wall, "baseLine") and isinstance(wall.baseLine, Line):
            line = wall.baseLine
            wall_lines_2d.append(Line(
                start=Point(x=to_m(line.start.x), y=to_m(line.start.y), units="m"),
                end=Point(x=to_m(line.end.x), y=to_m(line.end.y), units="m"),
                units="m",
                category="wall_segment"
            ))

        # 4b) metadata for the viewer (prefer Speckle object id)
        wid = (
            getattr(wall, "id", None)
            or getattr(wall, "speckle_id", None)
            or getattr(wall, "object_id", None)
            or getattr(wall, "applicationId", None)
            or getattr(wall, "elementId", None)
        )
        if not wid:
            continue

        family = getattr(wall, "revitFamily", None) or _param_value(getattr(wall, "definition", None), "Family")
        wtype  = (getattr(wall, "revitType", None)
                  or _param_value(getattr(wall, "definition", None), "Type Name")
                  or _param_value(getattr(wall, "definition", None), "Type")
                  or _param_value(getattr(wall, "definition", None), "Family and Type"))

        wall_metas.append({
            "id": str(wid),
            "family": family,
            "type": wtype,
        })

    # stash wall metas in the graph so /graph/exits can read them later
    G.graph["walls"] = wall_metas

    # --- STEP 4.1: Treat Structural Columns as short blockers (convert to lines) ---
    column_metas = []
    for col in columns or []:
        bbox = getattr(col, "bbox", None)
        if bbox:
            try:
                x0 = float(getattr(bbox, "x", 0)) / 1000.0
                y0 = float(getattr(bbox, "y", 0)) / 1000.0
                z0 = float(getattr(bbox, "z", 0)) / 1000.0
                w  = float(getattr(bbox, "xSize", 0)) / 1000.0
                h  = float(getattr(bbox, "ySize", 0)) / 1000.0
                p1 = Point(x=x0,      y=y0,      z=z0)
                p2 = Point(x=x0 + w,  y=y0 + h,  z=z0)
                wall_lines_2d.append(Line(start=p1, end=p2, units="m", category="column_diag"))
            except Exception:
                pass

        cid = getattr(col, "id", None) or getattr(col, "applicationId", None) or getattr(col, "elementId", None)
        family = getattr(col, "revitFamily", None) or _param_value(getattr(col, "definition", None), "Family")
        ctype  = getattr(col, "revitType", None)   or _param_value(getattr(col, "definition", None), "Type Name") \
                 or _param_value(getattr(col, "definition", None), "Type")
        if cid:
            column_metas.append({"id": str(cid), "family": family, "type": ctype})

    # --- STEP 4.2: Treat Railings “like walls”: try a base curve/line/path ---
    railing_metas = []
    for rl in railings or []:
        line_like = None
        for attr in ("baseLine", "baseCurve", "centerLine"):
            cand = getattr(rl, attr, None)
            if isinstance(cand, Line):
                line_like = cand
                break

        if line_like:
            wall_lines_2d.append(Line(
                start=Point(x=to_m(line_like.start.x), y=to_m(line_like.start.y), units="m"),
                end=Point(x=to_m(line_like.end.x),   y=to_m(line_like.end.y),   units="m"),
                units="m",
                category="railing_segment"
            ))
        else:
            path = getattr(rl, "path", None) or getattr(rl, "curve", None)
            if path and hasattr(path, "segments"):
                for seg in path.segments:
                    if hasattr(seg, "start") and hasattr(seg, "end"):
                        wall_lines_2d.append(Line(
                            start=Point(x=to_m(seg.start.x), y=to_m(seg.start.y), units="m"),
                            end=Point(x=to_m(seg.end.x),   y=to_m(seg.end.y),   units="m"),
                            units="m",
                            category="railing_segment"
                        ))

        rid = getattr(rl, "id", None) or getattr(rl, "applicationId", None) or getattr(rl, "elementId", None)
        family = getattr(rl, "revitFamily", None) or _param_value(getattr(rl, "definition", None), "Family")
        rtype  = getattr(rl, "revitType", None)   or _param_value(getattr(rl, "definition", None), "Type Name") \
                 or _param_value(getattr(rl, "definition", None), "Type")
        if rid:
            railing_metas.append({"id": str(rid), "family": family, "type": rtype})

    if column_metas:
        G.graph["columns"] = column_metas
    if railing_metas:
        G.graph["railings"] = railing_metas

    print(f"✅ Graph built from elements (level: {level_name}): {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")
    print(f"🧱 Total walls visualized: {len(wall_lines_2d)}")

    return G, wall_lines_2d



def trim_gridlines(
    gridlines,
    wall_lines_2d,
    doors,
    grid_dict,
    spacing=0.5,
    gap_offset=0.3,
    columns_2d=None,    # may be raw objects
    railings_2d=None,   # may be raw objects
):
    """
    Trims gridlines that intersect buffered wall polygons,
    skips trimming those crossing door openings,
    and injects missing door-to-door connections.
    """

    # --- helpers -------------------------------------------------------------
    def to_m(val):
        try:
            v = float(val)
            return v / 1000.0 if abs(v) > 100 else v
        except Exception:
            return 0.0

    def coerce_to_line(obj):
        """
        Try to view 'obj' as a Line:
        1) Already a Line with .start/.end
        2) line-like attrs: baseLine/baseCurve/centerLine
        3) poly/path first straight segment
        4) bbox diagonal (good for columns)
        """
        # 1) Already a line
        if hasattr(obj, "start") and hasattr(obj, "end"):
            return obj

        # 2) Common line-like attributes
        for attr in ("baseLine", "baseCurve", "centerLine"):
            cand = getattr(obj, attr, None)
            if cand is not None and hasattr(cand, "start") and hasattr(cand, "end"):
                try:
                    s, e = cand.start, cand.end
                    return Line(
                        start=Point(x=to_m(s.x), y=to_m(s.y), z=getattr(s, "z", 0.0), units="m"),
                        end=Point(x=to_m(e.x), y=to_m(e.y), z=getattr(e, "z", 0.0), units="m"),
                        units="m",
                    )
                except Exception:
                    pass

        # 3) Poly/path fallback
        for attr in ("path", "curve"):
            pc = getattr(obj, attr, None)
            if pc is not None and hasattr(pc, "segments"):
                for seg in getattr(pc, "segments", []) or []:
                    if hasattr(seg, "start") and hasattr(seg, "end"):
                        try:
                            s, e = seg.start, seg.end
                            return Line(
                                start=Point(x=to_m(s.x), y=to_m(s.y), z=getattr(s, "z", 0.0), units="m"),
                                end=Point(x=to_m(e.x), y=to_m(e.y), z=getattr(e, "z", 0.0), units="m"),
                                units="m",
                            )
                        except Exception:
                            continue

        # 4) BBox diagonal
        bbox = getattr(obj, "bbox", None)
        if bbox is not None:
            try:
                x0 = to_m(getattr(bbox, "x", 0.0))
                y0 = to_m(getattr(bbox, "y", 0.0))
                z0 = to_m(getattr(bbox, "z", 0.0))
                w  = to_m(getattr(bbox, "xSize", 0.0))
                h  = to_m(getattr(bbox, "ySize", 0.0))
                p1 = Point(x=x0,     y=y0,     z=z0, units="m")
                p2 = Point(x=x0 + w, y=y0 + h, z=z0, units="m")
                return Line(start=p1, end=p2, units="m")
            except Exception:
                pass

        return None

    def make_polygon_around_line(line: Line, offset: float):
        x0, y0 = line.start.x, line.start.y
        x1, y1 = line.end.x, line.end.y
        dx, dy = x1 - x0, y1 - y0
        length = math.sqrt(dx**2 + dy**2)
        if length == 0:
            return None
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        half = offset / 2
        return [
            Point(x=x0 + px * half, y=y0 + py * half, z=line.start.z),
            Point(x=x1 + px * half, y=y1 + py * half, z=line.end.z),
            Point(x=x1 - px * half, y=y1 - py * half, z=line.end.z),
            Point(x=x0 - px * half, y=y0 - py * half, z=line.start.z)
        ]

    def create_door_polygon(door, door_width=1.2, door_depth=0.2):
        m = getattr(door, "transform", None)
        if not m or not hasattr(m, "matrix") or len(m.matrix) < 16:
            return None

        cx = float(m.matrix[3]) / 1000.0
        cy = float(m.matrix[7]) / 1000.0
        cz = 0.0
        ux, uy = float(m.matrix[0]), float(m.matrix[4])  # width direction
        vx, vy = float(m.matrix[1]), float(m.matrix[5])  # opening direction

        v_len = math.sqrt(vx ** 2 + vy ** 2)
        u_len = math.sqrt(ux ** 2 + uy ** 2)
        if v_len == 0 or u_len == 0:
            return None
        vx, vy = vx / v_len, vy / v_len
        ux, uy = ux / u_len, uy / u_len

        w = door_width / 2
        d = door_depth / 2

        corners = []
        for dx in [-w, w]:
            for dy in [-d, d]:
                px = cx + ux * dx + vx * dy
                py = cy + uy * dx + vy * dy
                corners.append(Point(x=px, y=py, z=cz))

        return corners

    def create_door_bridge(door, grid_dict, spacing=0.5, max_projection_factor=2.0):
        m = getattr(door, "transform", None)
        if not m or not hasattr(m, "matrix") or len(m.matrix) < 16:
            return None

        def to_m_bridge(val): return val / 1000.0 if abs(val) > 100 else val
        def euclidean(p1, p2): return math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2)
        def find_nearest_node(pt, radius=0.75):
            best = None
            best_dist = float("inf")
            for node in grid_dict.values():
                dist = euclidean(pt, node)
                if dist < best_dist and dist < radius:
                    best = node
                    best_dist = dist
            return best

        cx = to_m_bridge(m.matrix[3])
        cy = to_m_bridge(m.matrix[7])
        cz = to_m_bridge(m.matrix[11])
        vx, vy = float(m.matrix[1]), float(m.matrix[5])  # opening direction

        v_len = math.sqrt(vx ** 2 + vy ** 2)
        if v_len == 0:
            return None
        vx /= v_len
        vy /= v_len

        c = Point(x=cx, y=cy, z=cz)
        d = (vx, vy)
        door_id = getattr(door, "elementId", getattr(door, "id", "unknown"))

        n1, n2 = None, None
        for factor in [1.0, 1.5, 2.0, 2.5]:
            offset = spacing * factor
            p1 = Point(x=c.x + d[0] * offset, y=c.y + d[1] * offset, z=c.z)
            p2 = Point(x=c.x - d[0] * offset, y=c.y - d[1] * offset, z=c.z)

            if not n1:
                n1 = find_nearest_node(p1, radius=spacing * 2.5)
            if not n2:
                n2 = find_nearest_node(p2, radius=spacing * 2.5)

            if n1 and n2:
                break

        if not n1 and not n2:
            print(f"⚠️ Skipped door {door_id}: Could not connect both sides (n1: False, n2: False)")
            print(f"🚫 Failed door {door_id}: center=({c.x:.2f}, {c.y:.2f}), dir=({vx:.2f}, {vy:.2f})")
            return None

        if n1 and n2:
            return [(n1, n2, f"door_{door_id}"), (n2, n1, f"door_{door_id}")]
        elif n1:
            return [(n1, (c.x, c.y, c.z), f"door_{door_id}")]
        elif n2:
            return [(n2, (c.x, c.y, c.z), f"door_{door_id}")]

    def line_intersects_polygon(p1, p2, polygon_pts):
        def ccw(a, b, c):
            return (c.y - a.y) * (b.x - a.x) > (b.y - a.y) * (c.x - a.x)
        def intersect(a, b, c, d):
            return ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d)
        for i in range(len(polygon_pts)):
            q1 = polygon_pts[i]
            q2 = polygon_pts[(i + 1) % len(polygon_pts)]
            if intersect(p1, p2, q1, q2):
                return True
        return False

    def point_inside_polygon(p, polygon_pts):
        poly = Path([(pt.x, pt.y) for pt in polygon_pts])
        return poly.contains_point((p.x, p.y))

    # --- normalize blockers (walls + optional columns/railings) --------------
    blockers = []
    for src in (wall_lines_2d, columns_2d, railings_2d):
        if not src:
            continue
        for obj in src:
            ln = coerce_to_line(obj)
            if ln is not None:
                blockers.append(ln)

    # --- Build blocker polygon index -----------------------------------------
    wall_idx = index.Index()
    wall_data = {}
    for i, line_like in enumerate(blockers):
        try:
            poly = make_polygon_around_line(line_like, gap_offset)
        except Exception:
            poly = None
        if poly:
            xs = [p.x for p in poly]
            ys = [p.y for p in poly]
            wall_idx.insert(i, (min(xs), min(ys), max(xs), max(ys)))
            wall_data[i] = poly

    print(f"📦 Blocker index initialized with {len(wall_data)} polygons")

    # --- Door polygons --------------------------------------------------------
    door_polygons = []
    for door in doors or []:
        poly = create_door_polygon(door)
        if poly:
            door_polygons.append(poly)
    print(f"🚪 Door openings prepared: {len(door_polygons)}")

    # --- Trim gridlines -------------------------------------------------------
    kept = []
    for line in tqdm(gridlines, desc="📏 Trimming gridlines (door-aware)"):
        start, end = line[0], line[1]
        meta = line[2] if len(line) > 2 else None
        mid = Point(x=(start.x + end.x) / 2, y=(start.y + end.y) / 2, z=start.z)

        # keep segments that cross a door opening
        if any(line_intersects_polygon(start, end, poly) or point_inside_polygon(mid, poly) for poly in door_polygons):
            kept.append((start, end, meta))
            continue

        bbox = (min(start.x, end.x), min(start.y, end.y), max(start.x, end.x), max(start.y, end.y))
        intersects_wall = False
        for i in wall_idx.intersection(bbox):
            if line_intersects_polygon(start, end, wall_data[i]) or point_inside_polygon(mid, wall_data[i]):
                intersects_wall = True
                break

        if not intersects_wall:
            kept.append((start, end, meta))

    # --- Add bridge edges across door openings --------------------------------
    bridge_edges = []
    for door in doors or []:
        bridge = create_door_bridge(door, grid_dict, spacing=spacing)
        if bridge:
            bridge_edges.extend(bridge)

    print(f"🔗 Injected {len(bridge_edges)} door bridge edges")
    return kept + bridge_edges

def add_doors_on_grid(G, doors):
    # --- helpers (local so it's truly drop-in) --------------------------------
    def _norm_key(s: str) -> str:
        return "".join(ch for ch in str(s).lower() if ch.isalnum())

    def truthy_flag(v) -> bool:
        if isinstance(v, bool):
            return v
        try:
            v = getattr(v, "value", v)
        except Exception:
            pass
        return str(v).strip().lower() in {"yes", "true", "1", "y"}

    def find_param_value_by_name(container, target_name: str, default=None):
        if not container:
            return default
        tgt = _norm_key(target_name)

        direct = getattr(container, target_name, None)
        if direct is not None:
            return getattr(direct, "value", default)

        def _yield_children(obj):
            if hasattr(obj, "get_dynamic_member_names"):
                for k in obj.get_dynamic_member_names():
                    yield getattr(obj, k, None)
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield v
            elif isinstance(obj, (list, tuple, set)):
                for v in obj:
                    yield v

        for item in _yield_children(container):
            if item is None:
                continue
            name = getattr(item, "name", None) or getattr(item, "applicationInternalName", None)
            if name and _norm_key(name) == tgt:
                return getattr(item, "value", default)
            for sub in _yield_children(item):
                if sub is None:
                    continue
                sname = getattr(sub, "name", None) or getattr(sub, "applicationInternalName", None)
                if sname and _norm_key(sname) == tgt:
                    return getattr(sub, "value", default)
        return default
    # --------------------------------------------------------------------------

    def to_meters(val):
        return val / 1000.0 if abs(val) > 100 else val

    from specklepy.objects.geometry import Point
    import math

    def euclidean_distance_2d(p1_xy, p2_xy):
        return math.sqrt((p1_xy[0] - p2_xy[0]) ** 2 + (p1_xy[1] - p2_xy[1]) ** 2)

    def closest_node(pt: Point, z_tol_first=2.5, z_tol_second=5.0, max_snap_xy=2.0):
        # project door point
        px, py, pz = round(pt.x, 4), round(pt.y, 4), round(pt.z, 4)
        if not G.nodes:
            return None, None  # (node, dist)

        def _best_among(cands):
            if not cands:
                return None, None
            best = min(cands, key=lambda n: euclidean_distance_2d((n[0], n[1]), (px, py)))
            d = euclidean_distance_2d((best[0], best[1]), (px, py))
            return best, d

        # --- pass 1: tighter z tolerance
        same_plane = [n for n in G.nodes if abs(n[2] - pz) <= z_tol_first]
        node, d = _best_among(same_plane if same_plane else list(G.nodes))

        # if too far, retry with wider z tolerance (but still prefer near planes)
        if node is not None and d is not None and d > max_snap_xy:
            wider = [n for n in G.nodes if abs(n[2] - pz) <= z_tol_second]
            node2, d2 = _best_among(wider if wider else list(G.nodes))
            if node2 is not None and (d2 is not None) and d2 < d:
                node, d = node2, d2

        return node, d

    # ---------- robust center extractors for doors without transform ----------
    def _midpoint(a, b):
        return Point(x=(a.x + b.x) / 2.0, y=(a.y + b.y) / 2.0, z=(a.z + b.z) / 2.0, units="m")

    def _point_m(x, y, z=0.0):
        return Point(x=to_meters(float(x)), y=to_meters(float(y)), z=to_meters(float(z)), units="m")

    def _center_from_baseline(line):
        try:
            return _midpoint(_point_m(line.start.x, line.start.y, getattr(line.start, "z", 0)),
                             _point_m(line.end.x,   line.end.y,   getattr(line.end,   "z", 0)))
        except Exception:
            return None

    def _center_from_definition(defn):
        elems = getattr(defn, "elements", None)
        if not elems:
            return None
        for el in elems:
            bl = getattr(el, "baseLine", None) or getattr(el, "baseline", None)
            if bl:
                c = _center_from_baseline(bl)
                if c: return c
        return None

    def _center_from_bbox(bbox):
        try:
            if not bbox:
                return None
            # Preferred: world-space min/max
            if hasattr(bbox, "min") and hasattr(bbox, "max"):
                mn, mx = bbox.min, bbox.max
                return _point_m((float(mn.x) + float(mx.x)) / 2.0,
                                (float(mn.y) + float(mx.y)) / 2.0,
                                (float(mn.z) + float(mx.z)) / 2.0)
            # Fallback: scalar min/max
            mnx = getattr(bbox, "minX", None); mny = getattr(bbox, "minY", None); mnz = getattr(bbox, "minZ", None)
            mxx = getattr(bbox, "maxX", None); mxy = getattr(bbox, "maxY", None); mxz = getattr(bbox, "maxZ", None)
            if None not in (mnx, mny, mnz, mxx, mxy, mxz):
                return _point_m((float(mnx) + float(mxx)) / 2.0,
                                (float(mny) + float(mxy)) / 2.0,
                                (float(mnz) + float(mxz)) / 2.0)
            return None
        except Exception:
            return None

    def _center_from_display_value(dv):
        try:
            items = dv if isinstance(dv, (list, tuple)) else [dv]
            for it in items:
                c = _center_from_bbox(getattr(it, "bbox", None))
                if c:
                    return c
            for it in items:
                verts = getattr(it, "vertices", None)
                if verts and isinstance(verts, list) and len(verts) >= 3:
                    xs = verts[0::3]; ys = verts[1::3]; zs = verts[2::3]
                    cx = sum(xs) / len(xs); cy = sum(ys) / len(ys); cz = sum(zs) / len(zs)
                    return _point_m(cx, cy, cz)
        except Exception:
            pass
        return None

    def get_door_center(door):
        tf = getattr(door, "transform", None)
        if hasattr(tf, "matrix") and isinstance(tf.matrix, list) and len(tf.matrix) >= 16:
            return _point_m(tf.matrix[3], tf.matrix[7], tf.matrix[11])
        c = _center_from_definition(getattr(door, "definition", None))
        if c: return c
        bl = getattr(door, "baseLine", None) or getattr(door, "baseline", None)
        if bl:
            c = _center_from_baseline(bl)
            if c: return c
        loc = getattr(door, "location", None) or getattr(door, "locationPoint", None)
        if loc and hasattr(loc, "x") and hasattr(loc, "y"):
            return _point_m(loc.x, loc.y, getattr(loc, "z", 0))
        c = _center_from_bbox(getattr(door, "bbox", None))
        if c: return c
        c = _center_from_display_value(getattr(door, "displayValue", None))
        return c
    # --------------------------------------------------------------------------

    mapped = 0
    unmapped = []

    for door in doors:
        door_id_raw = getattr(door, "id", "?")
        center = get_door_center(door)
        if not center:
            unmapped.append((door_id_raw, "❌ No valid transform or geometry center"))
            continue

        node, dist = closest_node(center)
        if not node:
            unmapped.append((door_id_raw, center.x, center.y))
            continue

        if dist is not None and dist > 1.5:
            print(f"⚠️ Door {door_id_raw} matched to a node {dist:.2f} m away in XY → likely a false match")

        # ---------------- identifiers ----------------
        speckle_id = getattr(door, "id", None)
        if isinstance(speckle_id, str):
            speckle_id = speckle_id.strip()
            if "-" in speckle_id:
                speckle_id = ""  # looks like a GUID → not a Speckle object id
        else:
            speckle_id = ""

        revit_id = getattr(door, "applicationId", None) or getattr(door, "elementId", None)
        if isinstance(revit_id, str):
            revit_id = revit_id.strip()

        # use find_param_value_by_name instead of undefined _param_value
        door_family = getattr(door, "revitFamily", None) or find_param_value_by_name(getattr(door, "definition", None), "Family")
        door_type   = getattr(door, "revitType",   None) or \
                      find_param_value_by_name(getattr(door, "definition", None), "Type Name") or \
                      find_param_value_by_name(getattr(door, "definition", None), "Family and Type") or \
                      find_param_value_by_name(getattr(door, "definition", None), "Type")

        # ---- mark node as a door
        G.nodes[node]["type"] = "door"
        G.nodes[node]["is_door"] = True

        # estimate local grid step from existing edges (fallback = 1.0 m)
        def _xy(a): return (a[0], a[1])
        adj_d = [euclidean_distance_2d(_xy(node), _xy(n)) for n in G.neighbors(node)]
        grid_step = min(adj_d) if adj_d else 1.0

        # widen search to jump a trimmed wall gap (door width + wall thickness)
        search_r = 2.5 * grid_step  # was 1.05 * grid_step

        # candidate nodes around door on same slab
        nearby = [
            n for n in G.nodes
            if n != node and abs(n[2] - node[2]) <= 0.5
            and euclidean_distance_2d(_xy(node), _xy(n)) <= search_r
        ]

        # 1) prefer distinct rooms if tags exist
        from math import inf
        best_by_room = {}  # room_id -> (node, dist)
        for n in nearby:
            rid = G.nodes[n].get("room_id")
            dloc = euclidean_distance_2d(_xy(node), _xy(n))
            if rid:
                cur = best_by_room.get(rid, (None, inf))
                if dloc < cur[1]:
                    best_by_room[rid] = (n, dloc)

        made_edge = False
        if best_by_room:
            # connect to up to two closest different rooms
            for rid, (n_best, d_best) in sorted(best_by_room.items(), key=lambda kv: kv[1][1])[:2]:
                if not G.has_edge(node, n_best):
                    G.add_edge(node, n_best, weight=d_best)
                    made_edge = True

        # 2) fallback: if no room_id tags nearby, connect to the two closest nodes anyway
        if not made_edge and nearby:
            near_sorted = sorted(nearby, key=lambda n: euclidean_distance_2d(_xy(node), _xy(n)))[:2]
            for n_best in near_sorted:
                d_best = euclidean_distance_2d(_xy(node), _xy(n_best))
                if not G.has_edge(node, n_best):
                    G.add_edge(node, n_best, weight=d_best)


        # Fire_Exit_Door flag
        params = getattr(door, "parameters", None)
        exit_raw = find_param_value_by_name(params, "Fire_Exit_Door")
        is_exit = truthy_flag(exit_raw)
        if exit_raw is not None:
            G.nodes[node]["Fire_Exit_Door"] = exit_raw
        if is_exit:
            G.nodes[node]["is_emergency_exit"] = True

        # ids on node
        if revit_id:
            G.nodes[node].setdefault("door_revit_ids", [])
            if revit_id not in G.nodes[node]["door_revit_ids"]:
                G.nodes[node]["door_revit_ids"].append(revit_id)

        if speckle_id:
            G.nodes[node].setdefault("door_ids", [])
            if speckle_id not in G.nodes[node]["door_ids"]:
                G.nodes[node]["door_ids"].append(speckle_id)

            G.nodes[node].setdefault("doors", [])
            G.nodes[node]["doors"].append({
                "id": speckle_id,
                "family": door_family,
                "type": door_type,
                "is_emergency_exit": bool(is_exit),
                "Fire_Exit_Door": exit_raw,
            })

            G.nodes[node]["source_id"] = speckle_id
            mapped += 1
        else:
            print(f"⚠️ Door has no Speckle object id; skipping primary ids. Revit id: {revit_id}")

        # connected rooms (legacy summary – now will include both sides)
        connected_room_ids = set()
        connected_room_names = set()
        for neighbor in G.neighbors(node):
            neighbor_data = G.nodes[neighbor]
            rid = neighbor_data.get("room_id")
            rname = neighbor_data.get("room_name")
            if rid:
                connected_room_ids.add(rid)
            if rname:
                connected_room_names.add(rname)

        if connected_room_ids:
            G.nodes[node]["connected_rooms"] = list(connected_room_ids)
            G.nodes[node]["connected_room_names"] = list(connected_room_names)
            G.nodes[node]["room_id"] = list(connected_room_ids)[0]
            G.nodes[node]["room_name"] = list(connected_room_names)[0]

    print(f"🚪 Mapped {mapped} doors to the grid.")

    if unmapped:
        print(f"⚠️ {len(unmapped)} door(s) could not be mapped:")
        for item in unmapped:
            print(f"   - {item}")

    return unmapped

def add_stairs_on_grid(G, stairs):
    from specklepy.objects.geometry import Point, Mesh
    import math

    def to_m(val):
        return val / 1000.0 if abs(val) > 100 else val

    def point_to_tuple(p: Point):
        return (round(p.x, 4), round(p.y, 4), round(p.z, 4))

    def euclidean_distance_3d(p1, p2):
        return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(3)))

    def closest_node(pt: Point):
        projected = point_to_tuple(pt)
        if not G.nodes:
            return None
        return min(G.nodes, key=lambda n: euclidean_distance_3d(n, projected))

    mapped = 0
    unmapped = []

    for stair in stairs:
        stair_id_raw = getattr(stair, "id", "?")
        center = None

        # Try centroid from first displayValue mesh
        try:
            meshes = getattr(stair, "displayValue", [])
            if meshes and hasattr(meshes[0], "vertices") and isinstance(meshes[0].vertices, list):
                verts = meshes[0].vertices
                if len(verts) >= 3:
                    xs = verts[0::3]
                    ys = verts[1::3]
                    zs = verts[2::3]
                    avg_x = to_m(sum(xs) / len(xs))
                    avg_y = to_m(sum(ys) / len(ys))
                    avg_z = to_m(sum(zs) / len(zs))
                    center = Point(x=avg_x, y=avg_y, z=avg_z)
        except Exception:
            pass

        if not center:
            unmapped.append((stair_id_raw, "❌ No valid transform or displayValue mesh"))
            continue

        node = closest_node(center)
        if not node:
            unmapped.append((stair_id_raw, center.x, center.y))
            continue

        dist = euclidean_distance_3d(point_to_tuple(center), node)
        if dist > 3.0:
            print(f"⚠️ Stair {stair_id_raw} matched to a node {dist:.2f}m away → likely a false match")

        # ---------------- identifiers ----------------
        speckle_id = getattr(stair, "id", None)
        if isinstance(speckle_id, str):
            speckle_id = speckle_id.strip()
            if "-" in speckle_id:
                speckle_id = ""
        else:
            speckle_id = ""

        revit_id = getattr(stair, "applicationId", None) or getattr(stair, "elementId", None)
        if isinstance(revit_id, str):
            revit_id = revit_id.strip()

        stair_family = getattr(stair, "revitFamily", None) or _param_value(getattr(stair, "definition", None), "Family")
        stair_type   = getattr(stair, "revitType",   None) or \
                       _param_value(getattr(stair, "definition", None), "Type Name") or \
                       _param_value(getattr(stair, "definition", None), "Type")

        G.nodes[node]["type"] = "stair"
        G.nodes[node]["is_stair"] = True

        # Keep Revit ids only for reference
        if revit_id:
            G.nodes[node].setdefault("stair_revit_ids", [])
            if revit_id not in G.nodes[node]["stair_revit_ids"]:
                G.nodes[node]["stair_revit_ids"].append(revit_id)

        # Only store Speckle object id in primary fields
        if speckle_id:
            G.nodes[node].setdefault("stair_ids", [])
            if speckle_id not in G.nodes[node]["stair_ids"]:
                G.nodes[node]["stair_ids"].append(speckle_id)

            G.nodes[node].setdefault("stairs", [])
            G.nodes[node]["stairs"].append({
                "id": speckle_id,   # ← object id (no dashes)
                "family": stair_family,
                "type": stair_type,
            })

            G.nodes[node]["source_id"] = speckle_id
            mapped += 1
        else:
            print(f"⚠️ Stair has no Speckle object id; skipping primary ids. Revit id: {revit_id}")

    print(f"🧗 Mapped {mapped} stairs to the grid.")
    if unmapped:
        print(f"⚠️ {len(unmapped)} stair(s) could not be mapped:")
        for item in unmapped:
            print(f"   - {item}")

    return unmapped
