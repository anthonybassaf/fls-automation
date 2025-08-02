import math
import networkx as nx
import numpy as np
from specklepy.objects.geometry import Point, Line, Polycurve
from matplotlib.path import Path
from collections import defaultdict
from rtree import index
from tqdm import tqdm

def group_rooms_by_level(rooms, level_alias_map=None):

    grouped = defaultdict(list)
    missing_level = []

    for room in rooms:
        level_obj = getattr(room, "level", None)
        level_name = getattr(level_obj, "name", None) if level_obj else None

        if level_name and isinstance(level_name, str) and level_name.strip():
            level_key = level_name.strip()
            # Apply alias if specified
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
        level_obj = getattr(door, "level", None)
        level_name = getattr(level_obj, "name", None) if level_obj else None

        if level_name and isinstance(level_name, str) and level_name.strip():
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
            print(f"  - ID: {getattr(d, 'elementId', '?')} | Type: {getattr(d, 'type', '?')}")

    return grouped

from collections import defaultdict

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

    for r in rooms:
        outline = getattr(r, "outline", None)
        if outline and hasattr(outline, "segments"):
            for seg in outline.segments:
                for pt in [seg.start, seg.end]:
                    update_bounds(to_m(pt.x), to_m(pt.y))
                    all_z.append(to_m(pt.z))

    for w in walls:
        if hasattr(w, "baseLine"):
            for pt in [w.baseLine.start, w.baseLine.end]:
                update_bounds(to_m(pt.x), to_m(pt.y))
                all_z.append(to_m(pt.z))

    for d in doors:
        m = getattr(d, "transform", None)
        if m and hasattr(m, "matrix") and len(m.matrix) >= 16:
            update_bounds(to_m(m.matrix[3]), to_m(m.matrix[7]))

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
    rooms, walls, doors=None, spacing=1.0, max_points=15000, level_name=None, global_bounds=None
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
            points = [(to_meters(seg.start.x), to_meters(seg.start.y)) for seg in outline.segments]
            if len(points) >= 3:
                poly = Path(points)
                room_id = getattr(room, "id", None) or getattr(room, "elementId", None)
                room_polygons.append((room_id, poly))

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
    }

    # Step 3: connect nodes only within same room
    edges = []
    for iy in range(len(y_vals)):
        for ix in range(len(x_vals)):
            if (ix, iy) not in grid_dict:
                continue

            pt = grid_dict[(ix, iy)]
            current_room = grid_node_room_ids.get((ix, iy))

            for dx, dy in [(1, 0), (0, 1)]:
                neighbor_key = (ix + dx, iy + dy)
                if neighbor_key in grid_dict:
                    neighbor = grid_dict[neighbor_key]
                    neighbor_room = grid_node_room_ids.get(neighbor_key)

                    # Only connect if in the same room
                    if current_room and neighbor_room and current_room == neighbor_room:
                        edges.append((pt, neighbor, current_room))

    print(f"🔗 Total grid edges (after trimming): {len(edges)}")
    print(f"📉 Retained {len(grid_dict)} grid nodes inside rooms")

    return edges, avg_z, grid_dict

def create_graph(walls=None, rooms=None, doors=None, gridlines=None, room_boundaries=None, level_name=None):
    import math
    import networkx as nx
    from specklepy.objects.geometry import Point, Line

    def to_m(val):
        return val / 1000.0 if abs(val) > 100 else val

    G = nx.Graph()
    wall_lines_2d = []

    # --- STEP 0: Build room_id → room_name mapping
    room_id_to_name = {
        str(getattr(r, "id", getattr(r, "elementId", None))): getattr(r, "name", None)
        for r in rooms or []
        if getattr(r, "name", None)
    }

    # --- STEP 2: Helpers with Z normalization
    def point_to_tuple(p: Point):
        return (round(p.x, 4), round(p.y, 4), round(p.z, 4))

    def euclidean_distance_2d(a, b):
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)

    # --- STEP 3: Add grid nodes and edges
    for start, end, room_id in gridlines or []:
        p1 = point_to_tuple(start)
        p2 = point_to_tuple(end)
        dist = euclidean_distance_2d(start, end)

        if room_id is not None:
            room_name = room_id_to_name.get(str(room_id), "?")
            G.add_node(p1, room_id=str(room_id), room_name=room_name)
            G.add_node(p2, room_id=str(room_id), room_name=room_name)
        else:
            G.add_node(p1)
            G.add_node(p2)

        G.add_edge(p1, p2, weight=dist)

    # --- STEP 3.5: Tag door nodes with all neighboring room_ids
    for node, data in G.nodes(data=True):
        if data.get("type") == "door":
            connected_room_ids = []
            connected_room_names = []
            for neighbor in G.neighbors(node):
                neighbor_data = G.nodes[neighbor]
                rid = neighbor_data.get("room_id")
                rname = neighbor_data.get("room_name")
                if rid and rid not in connected_room_ids:
                    connected_room_ids.append(rid)
                if rname and rname not in connected_room_names:
                    connected_room_names.append(rname)
            if connected_room_ids:
                data["room_id"] = connected_room_ids[0]  # For backward compatibility
                data["room_name"] = connected_room_names[0]
                data["connected_rooms"] = connected_room_ids
                data["connected_room_names"] = connected_room_names

    tagged_count = sum(1 for _, data in G.nodes(data=True) if data.get("room_id"))
    print(f"🏷️ Grid nodes tagged with room_id: {tagged_count} / {G.number_of_nodes()}")

    # --- STEP 4: Add wall visualization
    for wall in walls or []:
        if not hasattr(wall, "baseLine") or not isinstance(wall.baseLine, Line):
            continue
        line = wall.baseLine
        wall_lines_2d.append(Line(
            start=Point(x=to_m(line.start.x), y=to_m(line.start.y), units="m"),
            end=Point(x=to_m(line.end.x), y=to_m(line.end.y), units="m"),
            units="m",
            category="wall_segment"
        ))

    print(f"✅ Graph built from elements (level: {level_name}): {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")
    print(f"🧱 Total walls visualized: {len(wall_lines_2d)}")

    return G, wall_lines_2d


def trim_gridlines(gridlines, wall_lines_2d, doors, grid_dict, spacing=0.5, gap_offset=0.3):
    """
    Trims gridlines that intersect buffered wall polygons,
    skips trimming those crossing door openings,
    and injects missing door-to-door connections.
    """

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

        def to_m(val): return val / 1000.0 if abs(val) > 100 else val
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

        cx = to_m(m.matrix[3])
        cy = to_m(m.matrix[7])
        cz = to_m(m.matrix[11])
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

    # --- Build wall polygon index
    wall_idx = index.Index()
    wall_data = {}
    for i, wall in enumerate(wall_lines_2d):
        poly = make_polygon_around_line(wall, gap_offset)
        if poly:
            xs = [p.x for p in poly]
            ys = [p.y for p in poly]
            wall_idx.insert(i, (min(xs), min(ys), max(xs), max(ys)))
            wall_data[i] = poly

    print(f"📦 Wall index initialized with {len(wall_data)} polygons")

    # --- Door polygons
    door_polygons = []
    for door in doors:
        poly = create_door_polygon(door)
        if poly:
            door_polygons.append(poly)
    print(f"🚪 Door openings prepared: {len(door_polygons)}")

    # --- Trim gridlines
    kept = []
    for line in tqdm(gridlines, desc="📏 Trimming gridlines (door-aware)"):
        start, end = line[0], line[1]
        meta = line[2] if len(line) > 2 else None
        mid = Point(x=(start.x + end.x) / 2, y=(start.y + end.y) / 2, z=start.z)

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

    # --- Add bridge edges across door openings
    bridge_edges = []
    for door in doors:
        bridge = create_door_bridge(door, grid_dict, spacing=spacing)
        if bridge:
            bridge_edges.extend(bridge)

    print(f"🔗 Injected {len(bridge_edges)} door bridge edges")
    return kept + bridge_edges


def add_doors_on_grid(G, doors):

    def to_meters(val):
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

    for door in doors:
        transform = getattr(door, "transform", None)
        door_id = getattr(door, "id", "?")

        if hasattr(transform, "matrix") and isinstance(transform.matrix, list) and len(transform.matrix) >= 16:
            center = Point(
                x=to_meters(float(transform.matrix[3])),
                y=to_meters(float(transform.matrix[7])),
                z=to_meters(float(transform.matrix[11]))
            )
        else:
            unmapped.append((door_id, "❌ No valid transform"))
            continue

        node = closest_node(center)
        if not node:
            unmapped.append((door_id, center.x, center.y))
            continue

        dist = euclidean_distance_3d(point_to_tuple(center), node)
        if dist > 3.0:
            print(f"⚠️ Door {door_id} matched to a node {dist:.2f}m away → likely a false match")

        # Add door metadata
        G.nodes[node]["type"] = "door"
        G.nodes[node]["source_id"] = door_id
        G.nodes[node]["is_door"] = True

        # Detect and assign connected rooms based on neighboring nodes
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

        # Assign to door node
        if connected_room_ids:
            G.nodes[node]["connected_rooms"] = list(connected_room_ids)
            G.nodes[node]["connected_room_names"] = list(connected_room_names)
            # Legacy compatibility
            G.nodes[node]["room_id"] = list(connected_room_ids)[0]
            G.nodes[node]["room_name"] = list(connected_room_names)[0]

        mapped += 1

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
        stair_id = getattr(stair, "id", "?")
        center = None

        # ✅ Try centroid from first displayValue mesh
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
        except Exception as e:
            pass  # fallback to unmapped

        if not center:
            unmapped.append((stair_id, "❌ No valid transform or displayValue mesh"))
            continue

        node = closest_node(center)
        if not node:
            unmapped.append((stair_id, center.x, center.y))
            continue

        dist = euclidean_distance_3d(point_to_tuple(center), node)
        if dist > 3.0:
            print(f"⚠️ Stair {stair_id} matched to a node {dist:.2f}m away → likely a false match")

        G.nodes[node]["type"] = "stair"
        G.nodes[node]["source_id"] = stair_id
        G.nodes[node]["is_stair"] = True
        mapped += 1

    print(f"🧗 Mapped {mapped} stairs to the grid.")
    if unmapped:
        print(f"⚠️ {len(unmapped)} stair(s) could not be mapped:")
        for item in unmapped:
            print(f"   - {item}")

    return unmapped

