import networkx as nx
from specklepy.objects.base import Base
from specklepy.objects.geometry import Line, Point
from specklepy.api import operations
from specklepy.transports.server import ServerTransport
from pathfinding_algorithms import a_star, theta_star  
from helpers import euclidean_distance

def euclidean_distance(p1, p2):
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 + (p1[2] - p2[2]) ** 2) ** 0.5

def closest_node(pt: Point, G):
    def dist(a, b):
        return (a.x - b[0])**2 + (a.y - b[1])**2 + (a.z - b[2])**2
    return min(G.nodes, key=lambda n: dist(pt, n), default=None)

def map_doors_to_graph_nodes(G, doors, rooms, exit_door_ids=None, room_outlines=None):
    start_nodes = []
    exit_nodes = []
    room_node_mapping = {}
    room_start_nodes = {}

    for door in doors or []:
        center = None
        if hasattr(door, "transform") and hasattr(door.transform, "matrix"):
            matrix = door.transform.matrix
            if isinstance(matrix, list) and len(matrix) >= 16:
                cx, cy, cz = float(matrix[3]), float(matrix[7]), float(matrix[11])
                center = Point(x=cx, y=cy, z=cz)

        if center is None and hasattr(door, "definition") and isinstance(door.definition, Base):
            elements = getattr(door.definition, "elements", [])
            if elements and hasattr(elements[0], "baseLine"):
                baseline = elements[0].baseLine
                if isinstance(baseline, Line):
                    center = Point(
                        x=(baseline.start.x + baseline.end.x) / 2,
                        y=(baseline.start.y + baseline.end.y) / 2,
                        z=(baseline.start.z + baseline.end.z) / 2,
                    )

        if center is None:
            continue

        node = closest_node(center, G)
        if not node:
            continue

        door_id = getattr(door, "id", None)
        print(f"🧭 Door {door_id} center → node: {node}")

        if exit_door_ids and door_id in exit_door_ids:
            print(f"🚪 Exit door matched: {door_id}")
            exit_nodes.append(node)
            G.nodes[node]["type"] = "exit"
            G.nodes[node]["source_id"] = door_id
        else:
            room_id = getattr(door, "roomId", None)
            if not room_id and callable(room_outlines):
                room_id = room_outlines(center)
            if room_id:
                start_nodes.append(node)
                room_start_nodes[room_id] = node
                G.nodes[node]["type"] = "door"
                G.nodes[node]["source_id"] = door_id
                G.nodes[node]["room_id"] = room_id

    for room in rooms or []:
        room_id = getattr(room, "elementId", None)
        if room_id and str(room_id) in room_start_nodes:
            room_node_mapping[room_id] = room

    G.graph["start_nodes"] = start_nodes
    G.graph["exit_nodes"] = exit_nodes
    G.graph["room_start_nodes"] = room_start_nodes
    G.graph["room_node_mapping"] = room_node_mapping

    print(f"✅ Mapped {len(start_nodes)} start nodes and {len(exit_nodes)} exits")


# def map_room_center_to_start_nodes(G):
#     """
#     Map each unique real room to a single start node using the centroid of its tagged grid nodes.
#     Excludes fake room_ids such as 'door_XXXX'.
#     """
#     from collections import defaultdict
#     import numpy as np

#     room_nodes = defaultdict(list)

#     for node, data in G.nodes(data=True):
#         room_id_raw = data.get("room_id")
#         room_id = str(room_id_raw).strip() if room_id_raw else None

#         # Only accept real room IDs (not door tags or empty)
#         if room_id and not room_id.lower().startswith("door_") and room_id.lower() != "none":
#             room_nodes[room_id].append(node)

#     room_start_nodes = {}
#     for room_id, nodes in room_nodes.items():
#         coords = np.array(nodes)
#         centroid = coords.mean(axis=0)
#         closest_node = min(nodes, key=lambda pt: np.linalg.norm(np.array(pt) - centroid))
#         room_start_nodes[room_id] = closest_node
#         print(f"📌 Room ID: {room_id} → Start node: {closest_node}")

#     G.graph["start_nodes"] = list(room_start_nodes.values())
#     G.graph["room_start_nodes"] = room_start_nodes

#     print(f"🏠 Mapped {len(room_start_nodes)} room centers to start nodes.")

def map_room_center_to_start_nodes(G):
    """
    Map each room to a representative start node:
    - If the room has ≥2 door nodes: pick the midpoint (2 doors) or most central node (3+).
    - Else: use centroid of all tagged room nodes.
    """
    from collections import defaultdict
    import numpy as np

    room_nodes = defaultdict(list)
    door_nodes_by_room = defaultdict(list)

    # Step 1: Collect nodes per room
    for node, data in G.nodes(data=True):
        room_id = str(data.get("room_id", "")).strip()
        if not room_id or room_id.startswith("door_") or room_id == "none":
            continue
        room_nodes[room_id].append(node)
        if data.get("type") == "door":
            door_nodes_by_room[room_id].append(node)

    room_start_nodes = {}

    # Step 2: Assign best start node
    for room_id, nodes in room_nodes.items():
        doors = door_nodes_by_room.get(room_id, [])
        chosen = None

        if len(doors) == 2:
            # Midpoint of the two door nodes
            p1 = np.array(doors[0])
            p2 = np.array(doors[1])
            mid = (p1 + p2) / 2
            chosen = min(nodes, key=lambda pt: np.linalg.norm(np.array(pt) - mid))
            print(f"📍 Room {room_id} → midpoint between 2 doors → node {chosen}")

        elif len(doors) >= 3:
            # Node with minimum average distance to all other door nodes
            def avg_dist_to_all(pt):
                return np.mean([np.linalg.norm(np.array(pt) - np.array(d)) for d in doors])
            chosen = min(nodes, key=avg_dist_to_all)
            print(f"📍 Room {room_id} → central node among {len(doors)} doors → node {chosen}")

        else:
            # Use geometric center of room nodes
            coords = np.array(nodes)
            centroid = coords.mean(axis=0)
            chosen = min(nodes, key=lambda pt: np.linalg.norm(np.array(pt) - centroid))
            print(f"📍 Room {room_id} → centroid fallback → node {chosen}")

        room_start_nodes[room_id] = chosen

    G.graph["start_nodes"] = list(room_start_nodes.values())
    G.graph["room_start_nodes"] = room_start_nodes
    print(f"✅ Assigned start nodes for {len(room_start_nodes)} rooms.")


# def map_farthest_point_from_door(G):
#     """
#     Map each room to a representative start node:
#     - If the room has ≥2 door nodes: pick the midpoint (2 doors) or most central node (3+).
#     - Else: use the farthest corner node (max average distance to other room nodes).
#     """
#     from collections import defaultdict
#     import numpy as np

#     room_nodes = defaultdict(list)
#     door_nodes_by_room = defaultdict(list)

#     # Step 1: Collect nodes per room
#     for node, data in G.nodes(data=True):
#         room_id = str(data.get("room_id", "")).strip()
#         if not room_id or room_id.startswith("door_") or room_id == "none":
#             continue
#         room_nodes[room_id].append(node)
#         if data.get("type") == "door":
#             door_nodes_by_room[room_id].append(node)

#     room_start_nodes = {}

#     # Step 2: Assign best start node
#     for room_id, nodes in room_nodes.items():
#         doors = door_nodes_by_room.get(room_id, [])
#         chosen = None

#         if len(doors) == 2:
#             # Midpoint of the two door nodes
#             p1 = np.array(doors[0])
#             p2 = np.array(doors[1])
#             mid = (p1 + p2) / 2
#             chosen = min(nodes, key=lambda pt: np.linalg.norm(np.array(pt) - mid))
#             print(f"📍 Room {room_id} → midpoint between 2 doors → node {chosen}")

#         elif len(doors) >= 3:
#             # Node with minimum average distance to all other door nodes
#             def avg_dist_to_all(pt):
#                 return np.mean([np.linalg.norm(np.array(pt) - np.array(d)) for d in doors])
#             chosen = min(nodes, key=avg_dist_to_all)
#             print(f"📍 Room {room_id} → central node among {len(doors)} doors → node {chosen}")

#         else:
#             # NEW: Use farthest corner node (max average distance to all other room nodes)
#             def avg_dist_to_room(pt):
#                 return np.mean([np.linalg.norm(np.array(pt) - np.array(other)) for other in nodes])
#             chosen = max(nodes, key=avg_dist_to_room)
#             print(f"📍 Room {room_id} → farthest corner fallback → node {chosen}")

#         room_start_nodes[room_id] = chosen

#     G.graph["start_nodes"] = list(room_start_nodes.values())
#     G.graph["room_start_nodes"] = room_start_nodes
#     print(f"✅ Assigned start nodes for {len(room_start_nodes)} rooms.")

def map_farthest_point_from_door(G):
    """
    Map each room to a representative start node:
    - If the room has 2 door nodes: choose the midpoint of the 2 doors.
    - If the room has >= 3 doors: choose the node closest to the geometric center of the doors.
    - If the room has 1 door: choose the node farthest from that door.
    - If the room has 0 doors: choose the node closest to the room centroid.
    """
    from collections import defaultdict
    import numpy as np

    room_nodes = defaultdict(list)
    door_nodes_by_room = defaultdict(list)

    # Step 1: Collect nodes per room
    for node, data in G.nodes(data=True):
        room_id = str(data.get("room_id", "")).strip()
        if not room_id or room_id.startswith("door_") or room_id == "none":
            continue

        # Add node to this room
        room_nodes[room_id].append(node)

        # If this node is a door, register it as a door for this room
        if data.get("type") == "door":
            door_nodes_by_room[room_id].append(node)

    room_start_nodes = {}

    # Step 2: Choose representative node per room
    for room_id, nodes in room_nodes.items():
        doors = door_nodes_by_room.get(room_id, [])
        chosen = None

        if len(doors) == 2:
            # Midpoint of the two doors
            p1, p2 = np.array(doors[0]), np.array(doors[1])
            mid = (p1 + p2) / 2
            chosen = min(nodes, key=lambda pt: np.linalg.norm(np.array(pt) - mid))
            print(f"📍 Room {room_id} → midpoint between 2 doors → node {chosen}")

        elif len(doors) >= 3:
            # Node with minimum average distance to all doors (central among doors)
            def avg_dist_to_all(pt):
                return np.mean([np.linalg.norm(np.array(pt) - np.array(d)) for d in doors])
            chosen = min(nodes, key=avg_dist_to_all)
            print(f"📍 Room {room_id} → central node among {len(doors)} doors → node {chosen}")

        elif len(doors) == 1:
            # Node farthest from the single door
            door = np.array(doors[0])
            chosen = max(nodes, key=lambda pt: np.linalg.norm(np.array(pt) - door))
            print(f"📍 Room {room_id} → farthest from 1 door → node {chosen}")

        else:  # len(doors) == 0
            # No doors: fallback to geometric centroid
            centroid = np.mean([np.array(pt) for pt in nodes], axis=0)
            chosen = min(nodes, key=lambda pt: np.linalg.norm(np.array(pt) - centroid))
            print(f"📍 Room {room_id} → no doors → centroid node {chosen}")

        room_start_nodes[room_id] = chosen

    # Save to graph attributes
    G.graph["start_nodes"] = list(room_start_nodes.values())
    G.graph["room_start_nodes"] = room_start_nodes
    print(f"✅ Assigned start nodes for {len(room_start_nodes)} rooms.")



def stitch_subgraphs(G, max_distance=0.6):
    """
    Connects disconnected components of G by linking the closest pair of nodes
    between each component within a max_distance threshold.
    """
    import itertools
    import math

    def euclidean_3d(a, b):
        return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))

    components = list(nx.connected_components(G))
    if len(components) <= 1:
        print("✅ Graph already fully connected.")
        return

    print(f"🧵 Stitching {len(components)} disconnected components...")

    # Connect each isolated component to the largest one (usually component 0)
    main_component = max(components, key=len)
    rest_components = [c for c in components if c != main_component]

    for comp in rest_components:
        min_dist = float("inf")
        closest_pair = (None, None)

        for a, b in itertools.product(main_component, comp):
            dist = euclidean_3d(a, b)
            if dist < min_dist and dist <= max_distance:
                min_dist = dist
                closest_pair = (a, b)

        if closest_pair[0] and closest_pair[1]:
            G.add_edge(closest_pair[0], closest_pair[1], weight=min_dist)
            print(f"🔗 Connected {closest_pair[0]} ↔ {closest_pair[1]} (dist: {min_dist:.2f})")
        else:
            print(f"⚠️ Could not connect a component (no nodes within {max_distance}m).")

    print("✅ Stitching complete.")


def get_outside_doors_by_room(G, limit_debug_prints=10):
    """
    Return a dictionary mapping actual room_id → list of door node coordinates.
    A door is considered to lead outside if all its non-door neighbors belong to the same room.
    The room is inferred from the door’s non-door neighbors.
    """
    from collections import defaultdict

    outside_doors_by_room = defaultdict(list)
    debug_count = 0

    for node, data in G.nodes(data=True):
        if data.get("type") != "door":
            continue

        neighbors = list(G.neighbors(node))
        neighbor_room_ids = {
            G.nodes[n].get("room_id") for n in neighbors if G.nodes[n].get("type") != "door"
        }

        neighbor_room_ids = {rid for rid in neighbor_room_ids if rid is not None}

        # Outside door if all neighbors belong to one room
        if len(neighbor_room_ids) == 1:
            room_id = next(iter(neighbor_room_ids))
            outside_doors_by_room[room_id].append(node)

            if debug_count < limit_debug_prints:
                print(f"\n🚪 Outside Door Node: {node} | inferred room_id: {room_id}")
                for n in neighbors:
                    n_data = G.nodes[n]
                    print(f"   ↪ Neighbor: {n} – type: {n_data.get('type')}, room_id: {n_data.get('room_id')}")
                debug_count += 1

    print(f"\n🚪 Rooms with outside doors (out of {len([n for n, d in G.nodes(data=True) if d.get('type') == 'door'])} total door nodes):")
    for room_id, doors in outside_doors_by_room.items():
        print(f"   - Room {room_id} → {len(doors)} outside door(s):")
        for door_node in doors:
            print(f"       • Door node: {door_node}")

    return dict(outside_doors_by_room)

# def prompt_emergency_exit_selection(G):
#     import tkinter as tk
#     from tkinter import simpledialog, messagebox

#     door_nodes = [
#         (node, data.get("source_id"))
#         for node, data in G.nodes(data=True)
#         if data.get("type") == "door"
#     ]
#     stair_nodes = [
#         (node, data.get("source_id"))
#         for node, data in G.nodes(data=True)
#         if data.get("type") == "stair"
#     ]

#     door_ids = [sid for _, sid in door_nodes if sid]
#     stair_ids = [sid for _, sid in stair_nodes if sid]

#     root = tk.Tk()
#     root.withdraw()

#     messagebox.showinfo("Emergency Exit Selection",
#         f"Available emergency exits on this floor:\n\n"
#         f"Doors: {len(door_ids)}\n"
#         f"Stairs: {len(stair_ids)}\n\n"
#         f"You will now be prompted to enter a comma-separated list of selected IDs."
#     )

#     doors_input = simpledialog.askstring("Select Emergency Doors", "Enter selected Door IDs (comma-separated):")
#     stairs_input = simpledialog.askstring("Select Emergency Stairs", "Enter selected Stair IDs (comma-separated):")

#     root.destroy()

#     selected_doors = set((doors_input or "").replace(" ", "").split(",")) if doors_input else set()
#     selected_stairs = set((stairs_input or "").replace(" ", "").split(",")) if stairs_input else set()

#     return selected_doors, selected_stairs

def prompt_emergency_exit_selection(G):
    import os
    import json
    import pickle

    # 🔍 Try to infer level_name from graphs/ folder by comparing object hashes
    level_name = None
    graphs_dir = os.path.join(os.path.dirname(__file__), "graphs")
    for filename in os.listdir(graphs_dir):
        if filename.startswith("G_") and filename.endswith(".pkl"):
            path = os.path.join(graphs_dir, filename)
            try:
                with open(path, "rb") as f:
                    G_candidate = pickle.load(f)
                if G_candidate.number_of_nodes() == G.number_of_nodes() and set(G_candidate.nodes) == set(G.nodes):
                    level_name = filename.replace(".pkl", "").split("_")[-1]
                    break
            except Exception as e:
                continue

    if not level_name:
        print("⚠️ Could not determine level name by inspecting graph folder.")
        return set(), set()

    # 🧾 Load flat list of user input IDs
    try:
        with open("user_inputs.json", "r", encoding="utf-8") as f:
            all_inputs = json.load(f)
        floor_input = all_inputs.get(level_name, [])
    except Exception as e:
        print(f"❌ Failed to load user input for floor {level_name}: {e}")
        return set(), set()

    if isinstance(floor_input, list):
        print(f"✅ Floor {level_name} → Emergency exit IDs loaded: {floor_input}")
        return set(floor_input), set()
    else:
        print(f"❌ Invalid format in user_inputs.json for {level_name}")
        return set(), set()


# def compute_exit_paths_for_room(
#     G, room_id, start_node, fallback_exits, outside_exits_by_room,
#     selected_door_ids, selected_stair_ids,
#     furniture_list=None, algorithm="a_star", max_jump_distance=2.0,
#     node_to_component=None
# ):
#     from pathfinding_algorithms import a_star, theta_star
#     from helpers import euclidean_distance  # Adjust import path if needed

#     door_width_lookup = G.graph.get("door_width_lookup", {})
#     all_exit_paths = []

#     # Get exits
#     exit_sets = []
#     room_exit_nodes = outside_exits_by_room.get(room_id, [])
#     if room_exit_nodes:
#         exit_sets.append(("outside_exit", room_exit_nodes))
#     exit_sets.append(("default_exit", fallback_exits))

#     for exit_label, exit_nodes in exit_sets:
#         # Component check
#         if node_to_component:
#             start_comp = node_to_component.get(start_node)
#             if start_comp is None or not any(node_to_component.get(e) == start_comp for e in exit_nodes):
#                 print(f"❌ Skipping room {room_id} for {exit_label}: no reachable exits")
#                 continue

#         best_path = None
#         best_dist = float("inf")
#         best_exit_node = None
#         best_exit_type = None

#         for exit_node in exit_nodes:
#             try:
#                 if algorithm == "a_star":
#                     path = a_star(G, start_node, exit_node)
#                 elif algorithm == "theta_star":
#                     wall_segments = G.graph.get("wall_segments", [])
#                     room_boundaries = G.graph.get("room_boundaries", [])
#                     blockers = wall_segments + room_boundaries
#                     path = theta_star(
#                         G,
#                         start_node,
#                         exit_node,
#                         blockers=blockers,
#                         furniture=furniture_list or [],
#                         max_jump_distance=max_jump_distance
#                     )
#                 else:
#                     raise ValueError(f"Unsupported algorithm: {algorithm}")

#                 if path and len(path) >= 2:
#                     dist = sum(euclidean_distance(u, v) for u, v in zip(path[:-1], path[1:]))
#                     if dist < best_dist:
#                         best_dist = dist
#                         best_path = path
#                         best_exit_node = exit_node
#                         sid = G.nodes[exit_node].get("source_id", "")
#                         best_exit_type = exit_label

#             except Exception:
#                 continue

#         if best_path:
#             exit_node_data = G.nodes[best_exit_node]
#             exit_source_id = exit_node_data.get("source_id")
#             exit_door_width = door_width_lookup.get(exit_source_id)

#             print(f"📏 Room {room_id} → {exit_label} → Exit ID: {exit_source_id} → Width: {exit_door_width}")

#             all_exit_paths.append({
#                 "room_id": room_id,
#                 "start_node": start_node,
#                 "exit_node": best_exit_node,
#                 "exit_source_id": exit_source_id,
#                 "exit_type": best_exit_type,
#                 "exit_door_width": exit_door_width,
#                 "path": best_path,
#                 "distance_m": best_dist
#             })
#         else:
#             print(f"❌ No valid path found for room {room_id} via {exit_label}")

#     return all_exit_paths


def compute_exit_paths_for_room(
    G, room_id, start_node, fallback_exits, outside_exits_by_room,
    selected_door_ids, selected_stair_ids,
    furniture_list=None, algorithm="a_star", max_jump_distance=2.0,
    node_to_component=None
):
    from pathfinding_algorithms import a_star, theta_star
    from helpers import euclidean_distance

    door_width_lookup = G.graph.get("door_width_lookup", {})
    all_exit_paths = []

    # Identify nodes in room
    room_nodes = [n for n, data in G.nodes(data=True) if data.get("room_id") == room_id]

    # Extend in-room path if no furniture is present
    if not furniture_list and room_nodes:
        door_nodes = [
            n for n in G.nodes if G.nodes[n].get("type") == "door" and G.nodes[n].get("room_id") == room_id
        ]
        if door_nodes:
            door_center = door_nodes[0] if len(door_nodes) == 1 else tuple(
                sum(coord) / len(door_nodes) for coord in zip(*door_nodes)
            )
            def dist_to_door(n):
                return euclidean_distance(n, door_center)
            longest_in_room_node = max(room_nodes, key=dist_to_door)
            print(f"🧭 Room {room_id} → using longest in-room node: {longest_in_room_node}")
            start_node = longest_in_room_node

    # Collect exits
    exit_sets = []
    room_exit_nodes = outside_exits_by_room.get(room_id, [])
    if room_exit_nodes:
        exit_sets.append(("outside_exit", room_exit_nodes))
    exit_sets.append(("default_exit", fallback_exits))

    for exit_label, exit_nodes in exit_sets:
        if node_to_component:
            start_comp = node_to_component.get(start_node)
            if start_comp is None or not any(node_to_component.get(e) == start_comp for e in exit_nodes):
                print(f"❌ Skipping room {room_id} for {exit_label}: no reachable exits")
                continue

        best_path = None
        best_dist = float("inf")
        best_exit_node = None
        best_exit_type = None

        for exit_node in exit_nodes:
            try:
                if algorithm == "a_star":
                    path = a_star(G, start_node, exit_node)
                elif algorithm == "theta_star":
                    wall_segments = G.graph.get("wall_segments", [])
                    room_boundaries = G.graph.get("room_boundaries", [])
                    blockers = wall_segments + room_boundaries
                    path = theta_star(
                        G,
                        start_node,
                        exit_node,
                        blockers=blockers,
                        furniture=furniture_list or [],
                        max_jump_distance=max_jump_distance
                    )
                else:
                    raise ValueError(f"Unsupported algorithm: {algorithm}")

                if path and len(path) >= 2:
                    dist = sum(euclidean_distance(u, v) for u, v in zip(path[:-1], path[1:]))
                    if dist < best_dist:
                        best_dist = dist
                        best_path = path
                        best_exit_node = exit_node
                        sid = G.nodes[exit_node].get("source_id", "")
                        best_exit_type = exit_label

            except Exception:
                continue

        if best_path:
            exit_node_data = G.nodes[best_exit_node]
            exit_source_id = exit_node_data.get("source_id")
            exit_door_width = door_width_lookup.get(exit_source_id)

            print(f"📏 Room {room_id} → {exit_label} → Exit ID: {exit_source_id} → Width: {exit_door_width}")

            all_exit_paths.append({
                "room_id": room_id,
                "start_node": start_node,
                "exit_node": best_exit_node,
                "exit_source_id": exit_source_id,
                "exit_type": best_exit_type,
                "exit_door_width": exit_door_width,
                "path": best_path,
                "distance_m": best_dist
            })
        else:
            print(f"❌ No valid path found for room {room_id} via {exit_label}")

    return all_exit_paths



def find_shortest_paths(G, doors=None, rooms=None, algorithm="a_star", blockers=None, max_jump_distance=2.0):
    import networkx as nx
    import pickle
    import os

    from pathfinding_algorithms import euclidean_distance
    from path_of_travel import get_outside_doors_by_room, prompt_emergency_exit_selection

    selected_door_ids, selected_stair_ids = prompt_emergency_exit_selection(G)

    # Load door widths
    door_width_lookup = {}
    metadata_path = os.path.join("speckle_elements", "speckle_metadata.pkl")
    furniture_list = []
    if os.path.exists(metadata_path):
        with open(metadata_path, "rb") as f:
            speckle_data = pickle.load(f)
            door_list = speckle_data.get("Doors", [])
            furniture_list = speckle_data.get("Other", [])
            for door in door_list:
                door_id = getattr(door, "id", None)
                width = None
                params = getattr(door, "parameters", None)
                if params:
                    for key in ["Width", "width", "Panel Width", "PanelWidth", "Frame Width"]:
                        if hasattr(params, key):
                            param_obj = getattr(params, key)
                            width = getattr(param_obj, "value", param_obj)
                            break
                if door_id and width is not None:
                    door_width_lookup[door_id] = width
    else:
        print(f"⚠️ Could not find speckle_metadata.pkl at {metadata_path}")

    G.graph["door_width_lookup"] = door_width_lookup

    if not G.graph.get("room_start_nodes"):
        raise ValueError("Graph missing room_start_nodes. Run map_room_center_to_start_nodes first.")

    room_start_nodes = G.graph["room_start_nodes"]
    all_paths = []

    outside_exits_by_room = get_outside_doors_by_room(G)

    if not G.graph.get("exit_nodes"):
        fallback_exits = []
        for node, data in G.nodes(data=True):
            sid = data.get("source_id", "")
            if sid in selected_door_ids or sid in selected_stair_ids:
                G.nodes[node]["type"] = "default_exit"
                G.nodes[node]["is_emergency_exit"] = True
                G.nodes[node]["exit_category"] = "door" if sid in selected_door_ids else "stair"
                fallback_exits.append(node)
        G.graph["exit_nodes"] = fallback_exits
        print(f"🚪 Global fallback exits: {len(fallback_exits)}")
    else:
        fallback_exits = G.graph["exit_nodes"]

    components = list(nx.connected_components(G))
    node_to_component = {node: i for i, comp in enumerate(components) for node in comp}

    for room_id, start_node in room_start_nodes.items():
        if start_node not in G:
            continue
        room_paths = compute_exit_paths_for_room(
            G, room_id, start_node,
            fallback_exits, outside_exits_by_room,
            selected_door_ids, selected_stair_ids,
            furniture_list=furniture_list,
            algorithm=algorithm,
            max_jump_distance=max_jump_distance,
            node_to_component=node_to_component
        )
        all_paths.extend(room_paths)

    print(f"✅ Found {len(all_paths)} paths from room centers to exits.")
    return all_paths



def visualize_shortest_paths(paths, level_name=None):
    red_material = {
        "diffuse": [1.0, 0.0, 0.0],
        "opacity": 1.0
    }

    path_lines = []
    for path_obj in paths:
        path = path_obj.get("path", [])
        for i in range(len(path) - 1):
            start = path[i]
            end = path[i + 1]
            line = Line(
                start=Point(x=start[0], y=start[1], z=start[2]),
                end=Point(x=end[0], y=end[1], z=end[2]),
                units="m"
            )
            line["category"] = "escape_path"
            line["renderMaterial"] = red_material
            if level_name:
                line["floor"] = level_name
            path_lines.append(line)

    return path_lines


def send_paths_results_to_speckle(graph_objects, path_lines, client, stream_id, level_name):
    if not graph_objects and not path_lines:
        print(f"⚠️ No results to commit for floor: {level_name}. Skipping.")
        return

    print(f"📦 Preparing to commit results for {level_name}...")
    print(f"  - Graph objects: {len(graph_objects)}")
    print(f"  - Path lines: {len(path_lines)}")

    output = Base()
    output["graph_edges"] = graph_objects
    output["escape_paths"] = path_lines
    output["name"] = f"Fire Safety Analysis - {level_name}"
    output["floor"] = level_name
    output["units"] = "m"
    output["analysis_type"] = "emergency_egress"

    transport = ServerTransport(client=client, stream_id=stream_id)

    try:
        object_id = operations.send(base=output, transports=[transport])
        print(f"📦 Uploaded compliance results for {level_name}. Object ID: {object_id}")
    except Exception as e:
        print(f"❌ Failed to upload results for {level_name}: {e}")
        return

    try:
        commit_id = client.commit.create(
            stream_id=stream_id,
            object_id=object_id,
            branch_name="main",
            message=f"FLS Emergency Egress Paths – Floor: {level_name}"
        )
        print(f"✅ Committed results for {level_name}. Commit ID: {commit_id}")
    except Exception as e:
        print(f"❌ Failed to commit results for {level_name}: {e}")

