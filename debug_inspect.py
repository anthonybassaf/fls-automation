# debug_inspect.py
# Drop-in inspector for VeriFire graphs/paths PKLs
# Usage examples:
#   python debug_inspect.py --base "C:\\ProgramData\\VeriFire\\<PROJECT>_<MODEL>" --floor 00-GF-FFL
#   python debug_inspect.py --base "C:\\ProgramData\\VeriFire\\<PROJECT>_<MODEL>" --all

import argparse
import os
import pickle
import re
from collections import Counter, defaultdict
from pathlib import Path

def _s(v):
    return str(v).strip() if v is not None else ""

def _is_hex32(x: str) -> bool:
    x = _s(x)
    return bool(re.fullmatch(r"[0-9a-f]{32}", x))

def _flavor(x: str) -> str:
    x = _s(x)
    if not x:
        return "None"
    if x.startswith("door_"):
        return "door_*"
    if _is_hex32(x):
        return "hex32"
    return "other"

def _most(xs):
    return Counter(xs).most_common(1)[0][0] if xs else None

def _map_to_primary(x, alias_to_primary):
    x = _s(x)
    return alias_to_primary.get(x, x)

def load_graph(graph_path: Path):
    with graph_path.open("rb") as f:
        G = pickle.load(f)
    return G

def load_paths(paths_path: Path):
    with paths_path.open("rb") as f:
        obj = pickle.load(f)
    # Support old (list of dicts) and new ({"meta":..., "paths":[...]})
    if isinstance(obj, dict) and "paths" in obj:
        return obj.get("paths", []), obj.get("meta", {})
    return obj, {}

def analyze_graph(G):
    try:
        import networkx as nx  # noqa: F401  (needed for unpickle on some setups)
    except Exception:
        pass

    out = {}
    out["type"] = getattr(G, "graph", {}).get("type") or type(G).__name__
    nodes = list(G.nodes(data=True))
    edges = list(G.edges())
    out["nodes"] = len(nodes)
    out["edges"] = len(edges)

    # meta keys
    meta_keys = sorted(list(getattr(G, "graph", {}).keys()))
    out["meta_keys"] = meta_keys

    # writer / commit metadata (if any)
    meta = getattr(G, "graph", {})
    out["commit_id"]  = meta.get("commit_id")
    out["branch"]     = meta.get("branch")
    out["writer_sha"] = meta.get("writer_sha")
    out["written_at"] = meta.get("written_at")

    # node types
    node_type_counter = Counter((_s(d.get("type")) or None) for _, d in nodes)
    out["node_types"] = {k: node_type_counter[k] for k in sorted(node_type_counter, key=lambda k: (k is None, str(k)))}

    # room_ids (excluding None and door_* when asked)
    room_ids = []
    for _, d in nodes:
        rid = d.get("room_id")
        if rid is None:
            continue
        room_ids.append(_s(rid))

    out["room_ids_total"] = len(room_ids)
    unique_room_ids = sorted(set(room_ids))
    out["unique_room_ids"] = len(unique_room_ids)

    # flavor stats
    flavors = Counter(_flavor(r) for r in unique_room_ids)
    out["room_id_flavors"] = dict(flavors)

    # sample mapping: room_id -> top(name), top(level)
    samples = []
    # map room_id -> list of (name, level)
    m_name = defaultdict(list)
    m_lev  = defaultdict(list)
    for _, d in nodes:
        rid = _s(d.get("room_id"))
        if rid:
            rn = _s(d.get("room_name") or d.get("name"))
            lv = _s(d.get("level_name") or d.get("level") or d.get("Level"))
            if rn: m_name[rid].append(rn)
            if lv: m_lev[rid].append(lv)

    for rid in unique_room_ids[:12]:
        samples.append((rid, _flavor(rid), _most(m_name[rid]), _most(m_lev[rid])))
    out["room_samples"] = samples

    # door.connected_rooms flavors if present
    connected_room_ids = []
    for _, d in nodes:
        if _s(d.get("type")) == "door" or _s(d.get("node_type")) == "door":
            cr = d.get("connected_rooms")
            if isinstance(cr, (list, tuple, set)):
                for r in cr:
                    connected_room_ids.append(_s(r))
    out["door_connected_rooms_count"] = len(connected_room_ids)
    if connected_room_ids:
        out["door_connected_rooms_flavors"] = dict(Counter(_flavor(x) for x in connected_room_ids))

    # alias maps if present
    room_id_aliases = meta.get("room_id_aliases")
    alias_to_primary = meta.get("alias_to_primary")
    out["room_id_aliases_present"] = isinstance(room_id_aliases, (dict, defaultdict))
    out["alias_to_primary_present"] = isinstance(alias_to_primary, dict)
    out["room_id_aliases_size"] = len(room_id_aliases or {})
    out["alias_to_primary_size"] = len(alias_to_primary or {})

    return out, unique_room_ids, (alias_to_primary or {}), (room_id_aliases or {}), meta

def analyze_paths(paths_list):
    # Expect each item ~ {"room_id": "...", "path": [...], ...}
    out = {}
    if not isinstance(paths_list, list):
        return {"error": f"paths container is {type(paths_list)} not list"}, set()
    out["entries"] = len(paths_list)

    rids = []
    lengths = []
    for p in paths_list:
        rid = _s(p.get("room_id"))
        if rid:
            rids.append(rid)
        path = p.get("path") or p.get("nodes") or p.get("node_ids")
        if isinstance(path, (list, tuple)):
            lengths.append(len(path))
    out["unique_room_ids"] = len(set(rids))
    out["room_id_flavors"] = dict(Counter(_flavor(r) for r in set(rids)))
    if lengths:
        out["path_nodes_min"] = min(lengths)
        out["path_nodes_avg"] = round(sum(lengths)/len(lengths), 2)
        out["path_nodes_max"] = max(lengths)
    # Show a few
    out["room_samples"] = [(rid, _flavor(rid)) for rid in sorted(set(rids))[:12]]
    return out, set(rids)

def cross_compare(graph_rids, paths_rids, alias_to_primary=None):
    alias_to_primary = alias_to_primary or {}
    # raw comparison
    only_graph = sorted(set(graph_rids) - set(paths_rids))
    only_paths = sorted(set(paths_rids) - set(graph_rids))

    # normalized comparison (if alias map exists)
    if alias_to_primary:
        g_norm = {_map_to_primary(r, alias_to_primary) for r in graph_rids}
        p_norm = {_map_to_primary(r, alias_to_primary) for r in paths_rids}
        only_graph_norm = sorted(g_norm - p_norm)
        only_paths_norm = sorted(p_norm - g_norm)
    else:
        only_graph_norm = only_graph
        only_paths_norm = only_paths

    return {
        "only_in_graph_raw_count": len(only_graph),
        "only_in_paths_raw_count": len(only_paths),
        "only_in_graph_norm_count": len(only_graph_norm),
        "only_in_paths_norm_count": len(only_paths_norm),
        "only_in_graph_norm_samples": only_graph_norm[:20],
        "only_in_paths_norm_samples": only_paths_norm[:20],
    }

def main():
    ap = argparse.ArgumentParser(description="Inspect VeriFire graph/paths PKLs")
    ap.add_argument("--base", required=True, help="Base ProgramData folder for this project (e.g. C:\\ProgramData\\VeriFire\\<PROJ>_<MODEL>)")
    ap.add_argument("--floor", help="Floor name (e.g. 00-GF-FFL)")
    ap.add_argument("--all", action="store_true", help="Scan all G_*.pkl found")
    args = ap.parse_args()

    base = Path(args.base)
    graphs_dir = base / "graphs"
    paths_dir  = base / "paths"
    if not graphs_dir.exists():
        print(f"❌ graphs dir not found: {graphs_dir}")
        return
    if not paths_dir.exists():
        print(f"❌ paths dir not found:  {paths_dir}")
        return

    targets = []
    if args.all:
        for p in sorted(graphs_dir.glob("G_*.pkl")):
            floor = p.stem.split("_", 1)[-1]
            targets.append(floor)
    elif args.floor:
        targets = [args.floor]
    else:
        print("❌ Specify --floor <name> or --all")
        return

    for floor in targets:
        gpath = graphs_dir / f"G_{floor}.pkl"
        ppath = paths_dir  / f"paths_{floor}.pkl"
        print("\n" + "="*80)
        print(f"[FILE] graph: {gpath}")
        print(f"[FILE] paths: {ppath}")

        if not gpath.exists():
            print("  → Missing graph file.")
            continue
        if not ppath.exists():
            print("  → Missing paths file.")
            continue

        # Load
        try:
            G = load_graph(gpath)
        except Exception as e:
            print(f"  ❌ Failed to load graph: {e}")
            continue

        try:
            paths_list, paths_meta = load_paths(ppath)
        except Exception as e:
            print(f"  ❌ Failed to load paths: {e}")
            continue

        # Analyze
        g_info, g_room_ids, alias_to_primary, room_id_aliases, g_meta = analyze_graph(G)
        p_info, p_room_ids = analyze_paths(paths_list)

        # Print graph summary
        print(f"\n[GRAPH] type={g_info['type']}, nodes={g_info['nodes']}, edges={g_info['edges']}")
        print(f"[GRAPH.meta] keys: {', '.join(g_info['meta_keys']) or '(none)'}")
        print(f"[GRAPH.meta] commit_id={g_info['commit_id']} | branch={g_info['branch']} | writer_sha={g_info['writer_sha']} | written_at={g_info['written_at']}")
        print(f"[GRAPH] node types: {g_info['node_types']}")
        print(f"[GRAPH] unique room_ids (all): {g_info['unique_room_ids']}")
        print(f"[GRAPH] room_id flavors: {g_info['room_id_flavors']}")
        if g_info.get("room_samples"):
            print(f"[GRAPH] sample room_id → top(name), top(level):")
            for rid, flav, nm, lv in g_info["room_samples"]:
                print(f"  • {rid}  [{flav}]  → name='{nm or '?'}' | level='{lv or '?'}'")

        if g_info["door_connected_rooms_count"]:
            print(f"[GRAPH] door.connected_rooms id flavors: {g_info.get('door_connected_rooms_flavors')}")

        print(f"[GRAPH] room_id_aliases: present={g_info['room_id_aliases_present']} size={g_info['room_id_aliases_size']}")
        print(f"[GRAPH] alias_to_primary: present={g_info['alias_to_primary_present']} size={g_info['alias_to_primary_size']}")

        # ---- NEW: Inspect room_start_nodes + start_nodes
        rsn = g_meta.get("room_start_nodes")
        if isinstance(rsn, dict):
            keys = list(rsn.keys())
            flavors = Counter(
                ("door_*" if str(k).startswith("door_")
                 else ("hex32" if re.fullmatch(r"[0-9a-f]{32}", str(k)) else "other"))
                for k in keys
            )
            print(f"[GRAPH] room_start_nodes: keys={len(keys)} flavors={dict(flavors)}")
            bad = [k for k in keys if str(k).startswith("door_")]
            if bad[:10]:
                print("  • sample door_* keys in room_start_nodes:", bad[:10])
            sizes = [len(v) for v in rsn.values() if isinstance(v, (list, tuple, set))]
            if sizes:
                from statistics import mean
                print(f"  • start_nodes per key: min={min(sizes)}, avg={round(mean(sizes),2)}, max={max(sizes)}")
        else:
            if rsn is not None:
                print(f"[GRAPH] room_start_nodes present but not a dict (type={type(rsn).__name__})")
            else:
                print("[GRAPH] room_start_nodes: (absent)")

        sn = g_meta.get("start_nodes")
        if sn is not None:
            if isinstance(sn, (list, tuple, set)):
                print(f"[GRAPH] start_nodes: count={len(sn)}")
            elif isinstance(sn, dict):
                print(f"[GRAPH] start_nodes: dict-typed keys={len(sn)}")
            else:
                print(f"[GRAPH] start_nodes: present (type={type(sn).__name__})")
        else:
            print("[GRAPH] start_nodes: (absent)")

        # Print paths summary
        print(f"\n[PATHS] container type={type(paths_list).__name__} → entries detected={p_info.get('entries')}")
        if paths_meta:
            print(f"[PATHS.meta] commit_id={paths_meta.get('commit_id')} | branch={paths_meta.get('branch')} | writer_sha={paths_meta.get('writer_sha')} | written_at={paths_meta.get('written_at')}")
        print(f"[PATHS] unique room_ids: {p_info.get('unique_room_ids')}")
        print(f"[PATHS] room_id flavors: {p_info.get('room_id_flavors')}")
        if "path_nodes_min" in p_info:
            print(f"[PATHS] path node counts: min={p_info['path_nodes_min']}, avg={p_info['path_nodes_avg']}, max={p_info['path_nodes_max']}")
        if p_info.get("room_samples"):
            print(f"[PATHS] sample room_id flavors:")
            for rid, flav in p_info["room_samples"]:
                print(f"  • {rid}  [{flav}]")

        # Cross compare
        x = cross_compare(g_room_ids, p_room_ids, alias_to_primary=alias_to_primary if alias_to_primary else None)
        print("\n[CROSS] raw mismatch: only_in_graph={}, only_in_paths={}".format(
            x["only_in_graph_raw_count"], x["only_in_paths_raw_count"]
        ))
        print("[CROSS] normalized mismatch (using alias_to_primary): only_in_graph={}, only_in_paths={}".format(
            x["only_in_graph_norm_count"], x["only_in_paths_norm_count"]
        ))
        if x["only_in_graph_norm_samples"]:
            print("  • only_in_graph_norm (samples):")
            for rid in x["only_in_graph_norm_samples"][:12]:
                print(f"    - {rid}")
        if x["only_in_paths_norm_samples"]:
            print("  • only_in_paths_norm (samples):")
            for rid in x["only_in_paths_norm_samples"][:12]:
                print(f"    - {rid}")

if __name__ == "__main__":
    main()
