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

def compute_exit_paths_for_room(
    G, room_id, start_node, fallback_exits, outside_exits_by_room,
    selected_door_ids, selected_stair_ids,
    furniture_list=None, algorithm="a_star", max_jump_distance=2.0,
    node_to_component=None,
    columns=None, railings=None, other_obstacles=None
):
    """
    'room_id' is the unified id we use everywhere.
    Graph nodes may still carry alternate ids; we accept all aliases recorded in G.graph["id_aliases"].
    """
    from pathfinding_algorithms import a_star, theta_star
    from helpers import euclidean_distance

    door_width_lookup = G.graph.get("door_width_lookup", {}) or {}
    all_exit_paths = []

    # ----- acceptable ids for this room (unified id + aliases recorded earlier) -----
    aliases = G.graph.get("id_aliases", {}) or {}
    acceptable_ids = set([room_id])
    acceptable_ids.update(aliases.get(room_id, []))

    # ---------- helpers: AABB obstacles (meters) ----------
    def _mm_to_m(v): return float(v) / 1000.0

    def _bbox_to_rect(bbox):
        # Speckle bbox variant with min/max vectors (mm)
        if hasattr(bbox, "min") and hasattr(bbox, "max"):
            try:
                xmin = _mm_to_m(float(bbox.min.x)); ymin = _mm_to_m(float(bbox.min.y))
                xmax = _mm_to_m(float(bbox.max.x)); ymax = _mm_to_m(float(bbox.max.y))
                return (xmin, ymin, xmax, ymax)
            except Exception:
                return None
        # Speckle bbox variant with origin + sizes (mm)
        try:
            xmin = _mm_to_m(getattr(bbox, "x", 0.0))
            ymin = _mm_to_m(getattr(bbox, "y", 0.0))
            w    = _mm_to_m(getattr(bbox, "xSize", 0.0))
            h    = _mm_to_m(getattr(bbox, "ySize", 0.0))
            return (xmin, ymin, xmin + max(0.0, w), ymin + max(0.0, h))
        except Exception:
            return None

    def _element_to_rect(elem):
        bbox = getattr(elem, "bbox", None)
        if bbox:
            return _bbox_to_rect(bbox)
        if isinstance(elem, dict) and "bbox" in elem and isinstance(elem["bbox"], dict):
            try:
                b = elem["bbox"]
                xmin = float(b["min"]["x"]); ymin = float(b["min"]["y"])
                xmax = float(b["max"]["x"]); ymax = float(b["max"]["y"])
                return (xmin, ymin, xmax, ymax)
            except Exception:
                return None
        return None

    def _collect_additional_obstacles():
        rects = []
        src_cols  = columns if columns is not None else G.graph.get("Columns") or G.graph.get("columns")
        src_rails = railings if railings is not None else G.graph.get("Railings") or G.graph.get("railings")
        src_other = other_obstacles if other_obstacles is not None else G.graph.get("Other") or G.graph.get("other")

        for coll in (src_cols or []):
            r = _element_to_rect(coll);  rects.extend([r] if r else [])
        for rlg in (src_rails or []):
            r = _element_to_rect(rlg);   rects.extend([r] if r else [])
        for oth in (src_other or []):
            r = _element_to_rect(oth);   rects.extend([r] if r else [])
        return rects

    # ----- select nodes belonging to this room (accepting alias ids) -----
    room_nodes = [n for n, data in G.nodes(data=True) if data.get("room_id") in acceptable_ids]

    # Optional: adjust start inside the room
    if not furniture_list and room_nodes:
        door_nodes = [
            n for n in G.nodes
            if G.nodes[n].get("type") == "door" and G.nodes[n].get("room_id") in acceptable_ids
        ]
        if door_nodes:
            door_center = door_nodes[0] if len(door_nodes) == 1 else tuple(
                sum(coord) / len(door_nodes) for coord in zip(*door_nodes)
            )
            def dist_to_door(n): return euclidean_distance(n, door_center)
            longest_in_room_node = max(room_nodes, key=dist_to_door)
            print(f"🧭 Room {room_id} → using longest in-room node: {longest_in_room_node}")
            start_node = longest_in_room_node

    # Collect exits (already normalized to unified ids by caller)
    exit_sets = []
    room_exit_nodes = outside_exits_by_room.get(room_id, [])
    if room_exit_nodes:
        exit_sets.append(("outside_exit", room_exit_nodes))
    exit_sets.append(("default_exit", list(G.graph.get("exit_nodes") or [])))

    base_furniture = furniture_list or []
    extra_obstacles = _collect_additional_obstacles()
    furniture_for_theta = list(base_furniture) + extra_obstacles
    if extra_obstacles:
        print(f"🧱 Added {len(extra_obstacles)} column/railing/other obstacle(s) to Theta*.")

    # Connected component constraint
    def _same_component(a, b):
        if not node_to_component:
            return True
        return node_to_component.get(a) == node_to_component.get(b)

    best_paths = []

    for exit_label, exit_nodes in exit_sets:
        # Only exits reachable within same connected component
        candidate_exits = [e for e in exit_nodes if _same_component(start_node, e)]
        if not candidate_exits:
            print(f"❌ Skipping room {room_id} for {exit_label}: no reachable exits in same component")
            continue

        best_path = None
        best_dist = float("inf")
        best_exit_node = None

        for exit_node in candidate_exits:
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
                        furniture=furniture_for_theta,
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

            except Exception:
                continue

        if best_path:
            exit_node_data = G.nodes[best_exit_node]
            exit_source_id = exit_node_data.get("source_id")
            exit_door_width = door_width_lookup.get(str(exit_source_id).strip())

            print(f"📏 Room {room_id} → {exit_label} → Exit ID: {exit_source_id} → Width: {exit_door_width}")

            best_paths.append({
                "room_id": room_id,                 # unified id
                "start_node": start_node,
                "exit_node": best_exit_node,
                "exit_source_id": exit_source_id,
                "exit_type": exit_label,
                "exit_door_width": exit_door_width,
                "path": best_path,
                "distance_m": best_dist
            })
        else:
            print(f"❌ No valid path found for room {room_id} via {exit_label}")

    return best_paths

def find_shortest_paths(G, doors=None, rooms=None, algorithm="a_star", blockers=None, max_jump_distance=2.0):
    import os
    import pickle
    import networkx as nx

    # local imports
    from pathfinding_algorithms import euclidean_distance
    from path_of_travel import get_outside_doors_by_room

    # ----------------------- helpers -----------------------
    def _truthy(v) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in {"yes", "true", "1", "y"}

    def _s(v):
        return "" if v is None else str(v).strip()

    # Build indices for provided rooms (Speckle Base objects)
    rooms = list(rooms or [])
    idx_by_id = {}
    idx_by_element = {}
    idx_by_app = {}
    idx_by_hash = {}  # allowed only for resolution, never primary

    for r in rooms:
        rid = _s(getattr(r, "id", None))
        if rid:
            idx_by_id[rid] = r
        el = _s(getattr(r, "elementId", None))
        if el:
            idx_by_element[el] = r
        app = _s(getattr(r, "applicationId", None))
        if app:
            idx_by_app[app] = r
        # hashes just for resolving odd keys coming from legacy graphs
        for k in ("hash", "__room_hash__", "graph_room_id", "room_id"):
            hv = _s(getattr(r, k, None))
            if hv:
                idx_by_hash[hv] = r

    # If present, this often maps elementId -> list(node ids)
    room_node_mapping = G.graph.get("room_node_mapping", {}) or {}

    def _resolve_room_object(key):
        """Return a room object for a given key (whatever the graph used)."""
        k = _s(key)
        if not k:
            return None
        return (
            idx_by_element.get(k)
            or idx_by_app.get(k)
            or idx_by_id.get(k)
            or idx_by_hash.get(k)
            or (idx_by_element.get(k) if k in room_node_mapping else None)
        )

    def _primary_id_for_room(room_obj, fallback):
        """
        Fix A: primary id priority is elementId → applicationId → speckle id.
        Never choose a hash as primary.
        """
        el  = _s(getattr(room_obj, "elementId", None))
        app = _s(getattr(room_obj, "applicationId", None))
        sid = _s(getattr(room_obj, "id", None))  # Speckle object id
        if el:
            return el
        if app:
            return app
        if sid:
            return sid
        return _s(fallback)

    def _collect_aliases_for_room(room_obj, start_key):
        """All acceptable ids that might appear on graph nodes for this room (aliases only)."""
        aliases = set()
        for k in ("id", "elementId", "applicationId", "hash", "__room_hash__", "graph_room_id", "room_id", "sourceId", "source_id"):
            v = _s(getattr(room_obj, k, None))
            if v:
                aliases.add(v)
        sk = _s(start_key)
        if sk:
            aliases.add(sk)
        return aliases

    # ---------------- auto-detect EXIT doors from node attrs ------------------
    selected_door_ids = set()
    for n, data in G.nodes(data=True):
        if not data.get("is_door") and data.get("type") != "door":
            continue
        is_exit = data.get("is_emergency_exit") or _truthy(data.get("Fire_Exit_Door"))
        if not is_exit:
            continue
        sid = (
            _s(data.get("source_id"))
            or (_s(data["doors"][0].get("id")) if isinstance(data.get("doors"), list) and data["doors"] else "")
            or (_s(data.get("door_ids", [None])[0]) if isinstance(data.get("door_ids"), list) and data["door_ids"] else "")
        )
        if sid:
            selected_door_ids.add(sid)

    selected_stair_ids = set()

    if selected_door_ids:
        print(f"✅ Auto-detected {len(selected_door_ids)} emergency exit door(s).")
    else:
        print("⚠️ No auto-detected emergency exit doors found on this floor.")

    # ---------------- load door/obstacle metadata for widths & blockers -------
    door_width_lookup = {}

    def _resolve_metadata_path():
        root = os.environ.get("VERIFIRE_ROOT", r"C:\ProgramData\VeriFire")
        proj = _s(os.environ.get("PROJECT_ID", ""))
        model = _s(os.environ.get("MODEL_ID", "")).split("@", 1)[0]

        candidates = [
            os.path.join("speckle_elements", "speckle_metadata.pkl"),
            os.path.join(os.getcwd(), "speckle_elements", "speckle_metadata.pkl"),
        ]

        graphs_dir = (G.graph.get("graphs_dir") or G.graph.get("file_dir") or G.graph.get("root_dir"))
        if graphs_dir:
            candidates.append(os.path.join(os.path.abspath(graphs_dir), "..", "speckle_elements", "speckle_metadata.pkl"))

        if proj and model:
            candidates.append(os.path.join(root, f"{proj}_{model}", "speckle_elements", "speckle_metadata.pkl"))

        candidates.append(os.path.join(root, "speckle_elements", "speckle_metadata.pkl"))

        for p in candidates:
            p = os.path.abspath(p)
            if os.path.exists(p):
                return p
        return os.path.abspath(candidates[-1])

    metadata_path = _resolve_metadata_path()

    furniture_list, columns_list, railings_list, other_obstacles = [], [], [], []
    if os.path.exists(metadata_path):
        print(f"🗂️  Using speckle metadata at: {metadata_path}")
        with open(metadata_path, "rb") as f:
            speckle_data = pickle.load(f)

        # door widths
        for door in speckle_data.get("Doors", []) or []:
            door_id = getattr(door, "id", None)
            width = None
            params = getattr(door, "parameters", None)
            if params:
                for key in ("Width", "width", "Panel Width", "PanelWidth", "Frame Width"):
                    if hasattr(params, key):
                        param_obj = getattr(params, key)
                        width = getattr(param_obj, "value", param_obj)
                        break
            if door_id and width is not None:
                door_width_lookup[_s(door_id)] = width

        furniture_list  = speckle_data.get("Other", []) or []
        columns_list    = speckle_data.get("Columns", []) or []
        railings_list   = speckle_data.get("Railings", []) or []
        other_obstacles = speckle_data.get("OtherObstacles", []) or []

        G.graph["Columns"] = columns_list
        G.graph["Railings"] = railings_list
        G.graph["Other"] = other_obstacles
    else:
        print(f"⚠️ Could not find speckle_metadata.pkl at {metadata_path}")

    G.graph["door_width_lookup"] = door_width_lookup

    # ---------------- preconditions ------------------------------------------
    if not G.graph.get("room_start_nodes"):
        raise ValueError("Graph missing room_start_nodes. Run map_room_center_to_start_nodes first.")

    # Start with original mapping as produced earlier
    room_start_nodes = dict(G.graph["room_start_nodes"])  # {some_room_key: start_node}

    # ---------------- build alias → primary mapping ---------------------------
    # Seed from graph-level aliases if present
    alias_to_primary = {}
    if isinstance(G.graph.get("room_id_aliases"), dict):
        for primary, aliases in G.graph["room_id_aliases"].items():
            for a in (aliases or []):
                alias_to_primary[_s(a)] = _s(primary)

    primary_id_for = {}
    id_aliases = {}

    # Normalize every start key to a canonical primary id
    for start_key in list(room_start_nodes.keys()):
        robj = _resolve_room_object(start_key)
        if robj:
            pid = _primary_id_for_room(robj, start_key)
            primary_id_for[start_key] = pid
            aliases = _collect_aliases_for_room(robj, start_key)
            id_aliases.setdefault(pid, set()).update(aliases)
            for a in aliases:
                alias_to_primary[_s(a)] = pid
        else:
            pid = _s(start_key)
            primary_id_for[start_key] = pid
            id_aliases.setdefault(pid, set()).add(pid)
            alias_to_primary[_s(start_key)] = pid

    # Persist mappings on the graph for downstream consumers/diagnostics
    G.graph["primary_id_for"] = dict(primary_id_for)
    G.graph["id_aliases"] = {k: sorted(v) for k, v in id_aliases.items()}
    G.graph["alias_to_primary"] = dict(alias_to_primary)

    # Create a normalized copy of room_start_nodes keyed by primary ids
    norm_room_start_nodes = {}
    for k, node in room_start_nodes.items():
        pk = alias_to_primary.get(_s(k), _s(k))
        # if multiple aliases map to the same primary, keep the first node we saw
        norm_room_start_nodes.setdefault(pk, node)
    room_start_nodes = norm_room_start_nodes

    # ---------------- exits per room (normalize to primary ids) --------------
    raw_by_room = get_outside_doors_by_room(G)  # keyed by whatever start_key was
    exits_by_room = {}
    for k, nodes in raw_by_room.items():
        pk = alias_to_primary.get(_s(k), _s(k))
        exits_by_room.setdefault(pk, []).extend(nodes)

    # Optional: also normalize door.connected_rooms on the graph so future tools see canonical ids
    for n, d in G.nodes(data=True):
        if d.get("type") == "door" and isinstance(d.get("connected_rooms"), (list, tuple)):
            d["connected_rooms"] = [alias_to_primary.get(_s(x), _s(x)) for x in d["connected_rooms"]]

    # Fallback global exits (if not already set)
    if not G.graph.get("exit_nodes"):
        fallback_exits = []
        for node, data in G.nodes(data=True):
            sid = _s(data.get("source_id"))
            if sid and (sid in selected_door_ids or sid in selected_stair_ids):
                G.nodes[node]["type"] = "default_exit"
                G.nodes[node]["is_emergency_exit"] = True
                G.nodes[node]["exit_category"] = "door" if sid in selected_door_ids else "stair"
                fallback_exits.append(node)
        G.graph["exit_nodes"] = fallback_exits
        print(f"🚪 Global fallback exits: {len(fallback_exits)}")
    else:
        fallback_exits = list(G.graph["exit_nodes"])

    # Connected component map (avoid cross-component searches)
    components = list(nx.connected_components(G))
    node_to_component = {node: i for i, comp in enumerate(components) for node in comp}

    # ---------------- compute paths per room (using primary ids) -------------
    all_paths = []
    for primary_room_id, start_node in room_start_nodes.items():
        if start_node not in G:
            continue

        try:
            room_paths = compute_exit_paths_for_room(
                G,
                primary_room_id,                   # unified canonical id (Fix A)
                start_node,
                fallback_exits,
                exits_by_room,                     # normalized to primary ids
                selected_door_ids,
                selected_stair_ids,
                furniture_list=furniture_list,
                algorithm=algorithm,
                max_jump_distance=max_jump_distance,
                node_to_component=node_to_component,
                columns=columns_list,
                railings=railings_list,
                other_obstacles=other_obstacles
            )
            # ensure payload carries the unified id
            for p in room_paths:
                p["room_id"] = primary_room_id
            all_paths.extend(room_paths)
        except Exception as e:
            print(f"❌ Failed to compute paths for room {primary_room_id}: {e}")

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

