import os
import pickle
import glob
import math
from specklepy.objects.base import Base
from typing import Iterable, Tuple

def inspect_graph_pkls():
    """Inspect the contents of graph pkl files"""
    graph_files = sorted(glob.glob("graphs/G_*.pkl"))
    
    if not graph_files:
        print("❌ No graph pickle files found in 'graphs/' directory.")
        return
    
    print(f"🔍 Found {len(graph_files)} graph files:")
    
    for graph_path in graph_files:
        level_name = graph_path.split("_")[-1].replace(".pkl", "")
        print(f"\n📁 Inspecting: {graph_path} (Floor: {level_name})")
        
        try:
            with open(graph_path, "rb") as f:
                G = pickle.load(f)

            print(f"  📊 Nodes: {len(G.nodes)} | Edges: {len(G.edges)}")
            print(f"  🚪 Doors: {[n for n in G.nodes if G.nodes[n].get('type') == 'door']}")
            print(f"  🛑 Exits: {[n for n in G.nodes if G.nodes[n].get('type') == 'exit']}")
            print(f"  📎 Graph keys: {list(G.graph.keys())}")
        except Exception as e:
            print(f"❌ Error loading {graph_path}: {e}")

def inspect_full_structure(pkl_path="speckle_elements/speckle_metadata.pkl", max_items=3):
    with open(pkl_path, "rb") as f:
        metadata = pickle.load(f)

    for category, elements in metadata.items():
        print(f"\n🔎 Category: {category} ({len(elements)} items)")
        for idx, elem in enumerate(elements[:max_items]):
            print(f"  📦 Element {idx+1}: Type={getattr(elem, 'speckle_type', 'N/A')}")
            print(f"    • ID: {getattr(elem, 'id', 'N/A')}")
            print(f"    • Dynamic members: {elem.get_dynamic_member_names()}")
            if hasattr(elem, 'parameters'):
                print(f"    • Has parameters: ✅")
            else:
                print(f"    • Has parameters: ❌")
            print("-" * 30)

def inspect_element_parameters(element, show_all=True):
    parameters = getattr(element, "parameters", None)
    if not isinstance(parameters, Base):
        print("❌ No valid parameters found.")
        return

    print(f"📑 Parameters for Element ID: {getattr(element, 'id', 'N/A')}")
    for param_key in parameters.get_dynamic_member_names():
        param_obj = getattr(parameters, param_key, None)
        if isinstance(param_obj, Base):
            name = getattr(param_obj, "name", "")
            value = getattr(param_obj, "value", "")
            unit = getattr(param_obj, "unit", "")
            print(f"   • {name}: {value} {unit}".strip())
        elif show_all:
            print(f"   • {param_key}: {param_obj}")

def inspect_sample_parameters(pkl_path="speckle_elements/speckle_metadata.pkl", category="Doors", index=2):
    with open(pkl_path, "rb") as f:
        metadata = pickle.load(f)

    sample_elem = metadata.get(category, [])[index]
    inspect_element_parameters(sample_elem)

def inspect_start_and_exit_nodes(graph_dir="graphs"):
    for filename in sorted(os.listdir(graph_dir)):
        if not filename.startswith("G_") or not filename.endswith(".pkl"):
            continue

        level = filename.replace("G_", "").replace(".pkl", "")
        graph_path = os.path.join(graph_dir, filename)

        with open(graph_path, "rb") as f:
            G = pickle.load(f)

        start_nodes = G.graph.get("start_nodes", [])
        exit_nodes = G.graph.get("exit_nodes", [])

        print(f"📐 Floor {level}")
        print(f"  🔹 Start nodes: {len(start_nodes)}")
        print(f"  🔸 Exit nodes:  {len(exit_nodes)}")

        # Optionally, print their IDs:
        print(f"    • Start node IDs: {start_nodes}")
        print(f"    • Exit node IDs: {exit_nodes}")
        print("-" * 30)

def inspect_paths(paths_dir="paths"):
    for filename in sorted(os.listdir(paths_dir)):
        if not filename.startswith("paths_") or not filename.endswith(".pkl"):
            continue

        level = filename.replace("paths_", "").replace(".pkl", "")
        path_file = os.path.join(paths_dir, filename)

        with open(path_file, "rb") as f:
            paths = pickle.load(f)

        room_ids = [p.get("room_id") for p in paths if isinstance(p, dict)]
        num_paths = len(room_ids)

        print(f"🚪 Floor {level}")
        print(f"  🔹 Paths found: {num_paths}")
        print(f"  🏠 Rooms with paths: {set(room_ids)}")
        print("-" * 30)

def count_exit_nodes_from_paths(paths_dir="paths"):
    for filename in sorted(os.listdir(paths_dir)):
        if not filename.startswith("paths_") or not filename.endswith(".pkl"):
            continue

        level = filename.replace("paths_", "").replace(".pkl", "")
        path_file = os.path.join(paths_dir, filename)

        with open(path_file, "rb") as f:
            paths = pickle.load(f)

        exit_nodes = set()
        for p in paths:
            if isinstance(p, dict) and "path" in p and p["path"]:
                exit_nodes.add(tuple(p["path"][-1]))  # last node = exit

        print(f"🚪 Floor {level}")
        print(f"  🔸 Unique exit nodes used: {len(exit_nodes)}")
        for node in exit_nodes:
            print(f"     • {node}")
        print("-" * 30)

def count_default_emergency_exits(graph_dir="graphs"):
    for fname in sorted(os.listdir(graph_dir)):
        if not fname.startswith("G_") or not fname.endswith(".pkl"):
            continue

        level = fname.replace("G_", "").replace(".pkl", "")
        graph_path = os.path.join(graph_dir, fname)

        with open(graph_path, "rb") as f:
            G = pickle.load(f)

        fallback_exits = [
            node for node, data in G.nodes(data=True)
            if data.get("exit_category") == "fallback"
        ]

        print(f"🚨 Floor {level}")
        print(f"  🔸 Default (fallback) exits: {len(fallback_exits)}")
        for node in fallback_exits:
            print(f"     • {node} (door_id: {G.nodes[node].get('source_id')})")
        print("-" * 30)

import pickle

def inspect_exit_door_ids_from_pkl(file_path):
    """
    Inspect the paths .pkl file to extract and categorize exit_source_id values
    by their type (default_exit vs exit).
    """
    import pickle
    from collections import defaultdict

    with open(file_path, "rb") as f:
        data = pickle.load(f)

    if isinstance(data, list) and all(isinstance(d, dict) for d in data):
        categorized_exits = defaultdict(set)

        for path_obj in data:
            exit_id = path_obj.get("exit_source_id")
            exit_type = path_obj.get("exit_type", "unknown")
            if exit_id:
                categorized_exits[exit_type].add(exit_id)

        total_ids = sum(len(v) for v in categorized_exits.values())
        print(f"🚪 Found {total_ids} unique exit door IDs categorized by type:")

        for etype in sorted(categorized_exits.keys()):
            ids = categorized_exits[etype]
            print(f"\n🔹 {etype} ({len(ids)}):")
            for eid in sorted(ids):
                print(f"   • {eid}")

        return

    # Fallback if input is a networkx graph
    try:
        import networkx as nx
        if isinstance(data, nx.Graph):
            exit_door_ids = set()
            for node, attrs in data.nodes(data=True):
                if attrs.get("type") in ("exit", "default_exit"):
                    source_id = attrs.get("source_id")
                    if source_id:
                        exit_door_ids.add(source_id)

            if exit_door_ids:
                print(f"✅ Found {len(exit_door_ids)} exit door IDs in graph:")
                for eid in sorted(exit_door_ids):
                    print(f"  - {eid}")
            else:
                print("⚠️ No exit nodes with source_id found in graph.")
    except Exception as e:
        print(f"❌ Error while inspecting: {e}")

def inspect_exit_door_widths_from_pkl(file_path):
    """
    Check all exit_door_widths from path .pkl file.
    """
    import pickle

    with open(file_path, "rb") as f:
        data = pickle.load(f)

    widths = {}
    if isinstance(data, list):
        for path_obj in data:
            eid = path_obj.get("exit_source_id")
            width = path_obj.get("exit_door_width")
            if eid and width:
                widths[eid] = width

    if widths:
        print(f"✅ Found {len(widths)} exit door widths:")
        for eid, width in widths.items():
            print(f"   • {eid}: {width} mm")
    else:
        print("⚠️ No exit_door_width values found in paths file.")


import pickle
import networkx as nx

def inspect_door_widths_in_graph(graph_pkl_path):
    with open(graph_pkl_path, "rb") as f:
        G = pickle.load(f)

    if not isinstance(G, nx.Graph):
        print("❌ File does not contain a valid NetworkX graph.")
        return

    exit_nodes = [n for n, d in G.nodes(data=True) if d.get("type") in ("exit", "default_exit")]
    if not exit_nodes:
        print("⚠️ No exit or default_exit nodes found.")
        return

    found_widths = {}
    missing_widths = []

    for node in exit_nodes:
        data = G.nodes[node]
        door_id = data.get("source_id")
        width = data.get("width")

        if width is not None:
            found_widths[door_id] = width
        else:
            missing_widths.append(door_id)

    print(f"🔍 Exit nodes with width: {len(found_widths)}")
    for k, v in found_widths.items():
        print(f"   • {k} → {v} mm")

    print(f"\n🚫 Exit nodes missing width: {len(missing_widths)}")
    for k in missing_widths:
        print(f"   • {k}")

def inspect_node_room_metadata(graph_dir="graphs"):
    """
    Inspect all graph PKLs to verify tagging of nodes with room_id and room_name.
    """
    import pickle

    graph_files = sorted(f for f in os.listdir(graph_dir) if f.endswith(".pkl") and f.startswith("G_"))

    if not graph_files:
        print("❌ No graph PKL files found.")
        return

    for fname in graph_files:
        path = os.path.join(graph_dir, fname)
        with open(path, "rb") as f:
            G = pickle.load(f)

        total_nodes = len(G.nodes)
        room_tagged = [
            (n, d) for n, d in G.nodes(data=True)
            if "room_id" in d or "room_name" in d
        ]

        print(f"\n📦 {fname}")
        print(f"   • Nodes with room_id or room_name: {len(room_tagged)} / {total_nodes}")

        if room_tagged:
            print("   🔍 Sample tagged nodes:")
            for n, d in room_tagged[:5]:
                print(f"     - {n} | room_id={d.get('room_id')} | room_name={d.get('room_name')}")

def inspect_room_door_counts(graph_dir="graphs"):
    from path_of_travel import get_outside_doors_by_room

    for fname in sorted(os.listdir(graph_dir)):
        if not fname.startswith("G_") or not fname.endswith(".pkl"):
            continue

        level = fname.replace("G_", "").replace(".pkl", "")
        graph_path = os.path.join(graph_dir, fname)

        with open(graph_path, "rb") as f:
            G = pickle.load(f)

        print(f"\n📘 Floor {level}")
        room_to_doors = get_outside_doors_by_room(G, limit_debug_prints=0)
        for room_id, doors in sorted(room_to_doors.items(), key=lambda x: -len(x[1])):
            print(f"  🏠 Room {room_id} has {len(doors)} door(s)")
            for door in doors:
                print(f"     • Door node: {door}")

def inspect_multi_door_room_starts(graph_dir="graphs", min_doors=2, distance_threshold=1.5):
    import numpy as np
    import pickle
    import os

    def distance(a, b):
        return np.linalg.norm(np.array(a) - np.array(b))

    for fname in sorted(os.listdir(graph_dir)):
        if not fname.startswith("G_") or not fname.endswith(".pkl"):
            continue

        with open(os.path.join(graph_dir, fname), "rb") as f:
            G = pickle.load(f)

        room_starts = G.graph.get("room_start_nodes", {})
        door_nodes = G.graph.get("door_nodes_by_room", {})
        level = fname.replace("G_", "").replace(".pkl", "")

        print(f"\n📐 Floor {level} — Multi-Door Room Start Node Check")

        for room_id, door_list in door_nodes.items():
            if len(door_list) < min_doors:
                continue

            if room_id not in room_starts:
                print(f"❌ Room {room_id} has {len(door_list)} doors but no start node.")
                continue

            start_node = room_starts[room_id]

            # Compute door midpoint
            door_coords = [np.array(d) for d in door_list]
            midpoint = np.mean(door_coords, axis=0)

            d = distance(start_node, midpoint)
            status = "✅ OK" if d <= distance_threshold else f"⚠️ {d:.2f} m off"

            print(f"🏠 Room {room_id}: {len(door_list)} doors")
            print(f"   • Start node: {start_node}")
            print(f"   • Door midpoint: {tuple(round(x, 3) for x in midpoint)}")
            print(f"   → Distance to midpoint: {d:.2f} m → {status}")


def inspect_all_doors_by_room(graph_dir="graphs"):
    import os, pickle
    from collections import defaultdict

    for fname in sorted(os.listdir(graph_dir)):
        if not fname.endswith(".pkl") or not fname.startswith("G_"):
            continue

        floor = fname.replace("G_", "").replace(".pkl", "")
        with open(os.path.join(graph_dir, fname), "rb") as f:
            G = pickle.load(f)

        door_data = defaultdict(list)

        for node, data in G.nodes(data=True):
            if data.get("is_door") and data.get("room_id"):
                room_id = data["room_id"]
                door_data[room_id].append(node)

        print(f"\n📘 Floor {floor} — Doors per Room:")
        if not door_data:
            print("⚠️ No door nodes found with room_id tags.")
        else:
            for room_id, door_nodes in door_data.items():
                print(f"🏠 Room {room_id} → {len(door_nodes)} door(s)")
                for dn in door_nodes:
                    print(f"   • Door node: {dn}")

def inspect_unique_room_names(graph_dir="graphs"):
    import pickle, os
    from collections import Counter

    graph_files = sorted(f for f in os.listdir(graph_dir) if f.endswith(".pkl") and f.startswith("G_"))

    for fname in graph_files:
        path = os.path.join(graph_dir, fname)
        with open(path, "rb") as f:
            G = pickle.load(f)

        names = [d.get("room_name") for _, d in G.nodes(data=True) if "room_name" in d]
        counts = Counter(names)
        print(f"\n📦 {fname}")
        print(f"   • Unique room names: {len(counts)}")
        print(f"   • '?' room nodes: {counts.get('?', 0)}")
        print("   • Top 10 names:", counts.most_common(10))

def _is_finite3(t) -> bool:
    if (not isinstance(t, (tuple, list))) or len(t) != 3:
        return False
    try:
        x, y, z = float(t[0]), float(t[1]), float(t[2])
    except Exception:
        return False
    return all(math.isfinite(v) for v in (x, y, z))

def _norm3(t) -> Tuple[float, float, float]:
    x, y, z = float(t[0]), float(t[1]), float(t[2])
    return (x, y, z)

def inspect_graph_integrity(pkl_path: str, max_report: int = 25):
    """
    Validate that all node keys and edge endpoints are 3D finite tuples (x,y,z).
    Report degenerate edges, non-finite coords, extreme values, or weird attrs.
    """
    print(f"\n🔎 Integrity check: {pkl_path}")
    with open(pkl_path, "rb") as f:
        G = pickle.load(f)

    # Basic graph stats
    print(f"  📊 Nodes: {len(G.nodes)} | Edges: {len(G.edges)}")
    bad_nodes = []
    extreme_nodes = []  # absurd coordinates, e.g., |x|>1e6
    for n, data in G.nodes(data=True):
        if not _is_finite3(n):
            bad_nodes.append(("node_key", n, data))
            continue
        x, y, z = _norm3(n)
        if abs(x) > 1e6 or abs(y) > 1e6 or abs(z) > 1e6:
            extreme_nodes.append((n, data))

        # Optional semantic checks
        t = data.get("type")
        if t and t not in {"graph", "door", "stair", "exit", "default_exit", "node"}:
            # not an error, just flag unusual tags
            data["_warn_unusual_type"] = True

        # Typical metadata sanity (doesn't fail)
        sid = data.get("source_id")
        if sid is not None and not isinstance(sid, (str, int)):
            data["_warn_source_id_not_str"] = True

    bad_edges = []
    degenerate_edges = []
    extreme_edges = []
    for u, v, edata in G.edges(data=True):
        if u == v:
            degenerate_edges.append((u, v, edata))
            continue
        if not (_is_finite3(u) and _is_finite3(v)):
            bad_edges.append((u, v, edata))
            continue
        ux, uy, uz = _norm3(u)
        vx, vy, vz = _norm3(v)
        if any(abs(val) > 1e6 for val in (ux, uy, uz, vx, vy, vz)):
            extreme_edges.append(((u, v), edata))

    # Report
    def _print_sample(title: str, items: Iterable, transform=None):
        items = list(items)
        if not items:
            return
        print(f"  ❗ {title}: {len(items)}")
        for idx, it in enumerate(items[:max_report]):
            if transform:
                it = transform(it)
            print("     -", it)
        if len(items) > max_report:
            print(f"     … (+{len(items) - max_report} more)")

    _print_sample("Bad node keys (non-3D/Non-finite)", bad_nodes, transform=lambda r: r[:2])
    _print_sample("Extreme node coords (>|1e6|)", extreme_nodes, transform=lambda r: r[0])
    _print_sample("Bad edges (endpoint non-finite)", bad_edges)
    _print_sample("Degenerate edges (u==v)", degenerate_edges)
    _print_sample("Extreme edges (>|1e6|)", extreme_edges, transform=lambda r: r[0])

    if not (bad_nodes or bad_edges or degenerate_edges or extreme_nodes or extreme_edges):
        print("  ✅ Geometry looks well-formed (3D, finite).")

def inspect_speckle_readiness(pkl_path: str, max_report: int = 25):
    """
    Emulate the Speckle conversion (Points/Lines) without sending:
    collect any nodes/edges that would be skipped by a strict finite-check.
    """
    print(f"\n🧪 Speckle-readiness check: {pkl_path}")
    with open(pkl_path, "rb") as f:
        G = pickle.load(f)

    bad_nodes = []
    bad_edges = []
    for n, data in G.nodes(data=True):
        if not _is_finite3(n):
            bad_nodes.append(n)

    for u, v, edata in G.edges(data=True):
        if (not _is_finite3(u)) or (not _is_finite3(v)) or (u == v):
            bad_edges.append((u, v))

    if not bad_nodes and not bad_edges:
        print("  ✅ All nodes & edges would pass finite-checks → safe to send.")
    else:
        if bad_nodes:
            print(f"  ❗ Nodes that would be dropped: {len(bad_nodes)}")
            for n in bad_nodes[:max_report]:
                print("     -", n)
            if len(bad_nodes) > max_report:
                print(f"     … (+{len(bad_nodes)-max_report} more)")
        if bad_edges:
            print(f"  ❗ Edges that would be dropped: {len(bad_edges)}")
            for e in bad_edges[:max_report]:
                print("     -", e)
            if len(bad_edges) > max_report:
                print(f"     … (+{len(bad_edges)-max_report} more)")

def _resolve_pkl_path(p):
    # If absolute path or exists relative to CWD, use it
    if os.path.isabs(p) or os.path.isfile(p):
        return p
    # Try in ./graphs/
    cand = os.path.join("graphs", p)
    if os.path.isfile(cand):
        return cand
    # Try a recursive search (first match)
    matches = glob.glob(f"**/{p}", recursive=True)
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Could not find PKL: {p}")

# then in inspect_level_pkl:
def inspect_level_pkl(pkl_path: str):
    pkl_path = _resolve_pkl_path(pkl_path)
    inspect_graph_integrity(pkl_path)
    inspect_speckle_readiness(pkl_path)



if __name__ == "__main__":
    
    inspect_graph_pkls()
    #inspect_sample_parameters()
    #inspect_doors_in_metadata()
    #inspect_start_and_exit_nodes("graphs")
    #inspect_paths("paths")
    #count_exit_nodes_from_paths("paths")
    #count_default_emergency_exits("graphs")
    #inspect_exit_door_ids_from_pkl("paths/paths_001.pkl")
    #inspect_exit_door_widths_from_pkl("paths/paths_001.pkl")
    #inspect_door_widths_in_graph("graphs/G_001.pkl")
    #inspect_node_room_metadata("graphs")
    #inspect_unique_room_names("graphs")
    #inspect_room_door_counts("graphs")
    #inspect_multi_door_room_starts("graphs")
    #inspect_all_doors_by_room("graphs")
