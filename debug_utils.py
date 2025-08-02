from specklepy.objects.geometry import Point
import math
import networkx as nx
from collections import defaultdict
from specklepy.objects.base import Base


def point_to_tuple(p: Point, reference_z: float = None):
    z = reference_z if reference_z is not None else p.z
    return (round(p.x, 4), round(p.y, 4), round(z, 4))


def euclidean_distance_3d(p1, p2):
    return math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(3)))


def inspect_graph_z_levels(G):
    z_vals = {round(n[2], 4) for n in G.nodes}
    print(f"🧭 Unique Z levels in graph: {sorted(z_vals)}")


def debug_door_bounds_vs_grid(doors, grid_nodes, reference_z, level_name=""):
    from specklepy.objects.geometry import Point

    def to_meters(val):
        return val / 1000.0 if abs(val) > 100 else val

    def point_to_tuple(p: Point):
        return (round(to_meters(p.x), 4), round(to_meters(p.y), 4), round(reference_z, 4))

    def extract_door_center(door):
        m = getattr(door, "transform", None)
        if hasattr(m, "matrix") and isinstance(m.matrix, list) and len(m.matrix) >= 16:
            return Point(
                x=to_meters(float(m.matrix[3])),
                y=to_meters(float(m.matrix[7])),
                z=reference_z
            )
        return None

    xs = [to_meters(pt[0]) for pt in grid_nodes]
    ys = [to_meters(pt[1]) for pt in grid_nodes]

    print(f"\n📦 Grid bounds (Level {level_name or '?'}):")
    if not xs or not ys:
        print("⚠️ No grid nodes provided.")
        return

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    print(f"   X: {min_x:.1f} to {max_x:.1f}")
    print(f"   Y: {min_y:.1f} to {max_y:.1f}")

    outside_doors = []
    for door in doors:
        center = extract_door_center(door)
        if not center:
            continue
        x, y = center.x, center.y
        if not (min_x <= x <= max_x and min_y <= y <= max_y):
            outside_doors.append((getattr(door, "id", "?"), x, y))

    if outside_doors:
        print(f"\n🚪 Doors outside grid bounds ({len(outside_doors)} total):")
        for door_id, x, y in outside_doors:
            print(f"   - Door {door_id} at ({x:.1f}, {y:.1f})")
    else:
        print("✅ All doors are within the grid bounds.")


def report_unreachable_start_nodes(G, paths):
    start_nodes = set(G.graph.get("start_nodes", []))
    reached_nodes = set()

    for path in paths:
        if path:
            reached_nodes.add(path[0])

    unreachable = sorted(start_nodes - reached_nodes)

    print(f"\n📊 Pathfinding Summary:")
    print(f"   ✅ Reachable start nodes: {len(reached_nodes)}")
    print(f"   ❌ Unreachable start nodes: {len(unreachable)}")

    if unreachable:
        print("   🔍 Unreachable node coordinates:")
        for node in unreachable:
            print(f"     - {node}")

    return unreachable


def inspect_exit_node_connectivity(G):
    exits = G.graph.get("exit_nodes", [])
    print(f"\n🧵 Inspecting {len(exits)} exit nodes for connectivity...")
    for node in exits:
        if node not in G:
            print(f"❌ Exit node {node} is missing from graph.")
            continue
        degree = G.degree(node)
        print(f"   ↪ Exit node {node} has {degree} edges")
        if degree == 0:
            print(f"   🚨 Exit node {node} is isolated — not connected to graph.")


def check_graph_connectivity(G):
    print("\n🔍 Running graph connectivity check...")

    if nx.is_connected(G):
        print("✅ Graph is fully connected (1 component)")
        return

    components = list(nx.connected_components(G))
    print(f"❗ Graph is NOT connected. Found {len(components)} subgraphs.")
    print(f"   🔸 Largest subgraph size: {max(len(c) for c in components)}")
    print(f"   🔸 Smallest subgraph size: {min(len(c) for c in components)}")

    node_to_component = {}
    for i, comp in enumerate(components):
        for node in comp:
            node_to_component[node] = i

    start_nodes = G.graph.get("start_nodes", [])
    exit_nodes = G.graph.get("exit_nodes", [])

    if not start_nodes:
        print("⚠️ No start nodes in graph.")
    if not exit_nodes:
        print("⚠️ No exit nodes in graph.")

    start_comps = set(node_to_component.get(n) for n in start_nodes if n in node_to_component)
    exit_comps = set(node_to_component.get(n) for n in exit_nodes if n in node_to_component)

    print(f"🔸 Start nodes span {len(start_comps)} component(s): {sorted(start_comps)}")
    print(f"🔸 Exit nodes span {len(exit_comps)} component(s): {sorted(exit_comps)}")

    shared_components = start_comps & exit_comps
    if shared_components:
        print(f"✅ There is at least one component that contains both start and exit nodes: {sorted(shared_components)}")
    else:
        print("🚫 No component contains both start and exit nodes — all paths will fail.")


def clean_speckle_objects(objects):
    cleaned = []
    for obj in objects:
        if isinstance(obj, Base):
            cleaned.append(obj)
        else:
            print(f"⚠️ Skipping non-Base object of type {type(obj)}")
    print(f"✅ Cleaned object list: {len(cleaned)} valid Base objects retained out of {len(objects)} total")
    return cleaned

from collections import defaultdict

def print_rooms_with_outside_doors(G):
    """
    Prints each room ID and the door nodes it has that lead to the outside.
    A door leads outside if it connects to only one unique room (excluding other doors).
    """

    room_outside_doors = defaultdict(list)
    total_doors_checked = 0
    missing_room_id_doors = 0

    for node, data in G.nodes(data=True):
        if data.get("type") != "door":
            continue

        total_doors_checked += 1

        # Get neighboring non-door room_ids
        neighbor_room_ids = {
            G.nodes[n].get("room_id")
            for n in G.neighbors(node)
            if G.nodes[n].get("type") != "door" and G.nodes[n].get("room_id")
        }

        if len(neighbor_room_ids) == 1:
            room_id = next(iter(neighbor_room_ids))
            room_outside_doors[room_id].append(node)

    print(f"\n🚪 Rooms with outside doors (out of {total_doors_checked} total door nodes):")
    for room_id, doors in room_outside_doors.items():
        print(f"   - Room {room_id} → {len(doors)} outside door(s):")
        for door_node in doors:
            print(f"       • Door node: {door_node}")

    if missing_room_id_doors:
        print(f"⚠️ {missing_room_id_doors} door nodes had no room_id assigned.")


def debug_door_connections(G, max_doors=10):
    """
    Inspect a sample of door nodes to determine why some rooms may not be detected
    as having outside doors. This function will print:
    - Door nodes missing 'room_id'
    - Neighbor nodes and their 'room_id'
    - Summary of neighbor uniqueness
    """

    print(f"\n🔍 Inspecting door connections (limit {max_doors} doors):")
    checked = 0
    outside_door_candidates = 0

    for node, data in G.nodes(data=True):
        if data.get("type") != "door":
            continue

        room_id = data.get("room_id")
        print(f"\n🚪 Door Node: {node} | room_id: {room_id}")

        if not room_id:
            print("   ⚠️ Door is missing room_id.")
            continue

        neighbors = list(G.neighbors(node))
        if not neighbors:
            print("   ❌ Door has no neighbors.")
            continue

        neighbor_room_ids = set()
        for neighbor in neighbors:
            neighbor_data = G.nodes[neighbor]
            n_type = neighbor_data.get("type", "grid")
            n_room_id = neighbor_data.get("room_id")
            print(f"   ↪ Neighbor: {neighbor} – type: {n_type}, room_id: {n_room_id}")
            if n_room_id:
                neighbor_room_ids.add(n_room_id)

        # Count unique room ids among neighbors
        unique_room_ids = {rid for rid in neighbor_room_ids if rid != room_id}
        if not unique_room_ids:
            print("   🔁 All neighbors are from the same room.")
        else:
            print(f"   🌐 Neighboring rooms (excluding own): {unique_room_ids}")
            outside_door_candidates += 1

        checked += 1
        if checked >= max_doors:
            break

    print(f"\n✅ Checked {checked} doors. Found {outside_door_candidates} possible outside door candidates.\n")




