#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
run_paths_main.py — ProgramData + mark exit nodes from user_inputs (spaces only)

Key change (2026-01):
- Prevent spurious edges appearing only "after pathfinding" by:
  1) making subgraph stitching opt-in (VERIFIRE_ENABLE_STITCHING=1)
  2) running pathfinding on a WORKING COPY of the graph (so any algorithm that mutates edges
     cannot pollute the graph we export to Speckle)
  3) avoiding overwriting the original graph pickle unless explicitly enabled
     (VERIFIRE_SAVE_UPDATED_GRAPH=1)

Why:
- The grid you see from run_grid_main.py is the graph as generated.
- run_paths_main.py was previously calling stitch_subgraphs(G) in-place whenever the graph
  had >1 connected component, and then exporting that mutated graph.
- Some Theta*/helper implementations also add temporary "jump" edges.

This script now keeps a clean visualization graph (G_vis) and a working graph for pathfinding (G_work).
"""

import io
import os
import sys
import json
import stat
import pickle
from pathlib import Path

# Ensure UTF-8 stdout on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Speckle credentials (NO static tokens) ────────────────────────────────────
from speckle_credentials import (
    SPECKLE_SERVER_URL,
    PROJECT_ID as PROJECT_ID_DEFAULT,
    MODEL_ID as MODEL_ID_DEFAULT,
)

# Per-request overrides from backend (headers → env)
PROJECT_ID = (os.getenv("PROJECT_ID") or PROJECT_ID_DEFAULT).strip()
MODEL_ID   = ((os.getenv("MODEL_ID") or MODEL_ID_DEFAULT).strip()).split("@", 1)[0]

# 🔐 REQUIRED per-user token injected by backend
SPECKLE_USER_TOKEN = os.getenv("SPECKLE_USER_TOKEN")
if not PROJECT_ID:
    raise RuntimeError("PROJECT_ID is not set (env or default).")
if not MODEL_ID:
    raise RuntimeError("MODEL_ID is not set (env or default).")
if not SPECKLE_USER_TOKEN:
    # Do not fall back to any .env token – force users to authenticate.
    raise RuntimeError(
        "Missing SPECKLE_USER_TOKEN in environment. "
        "Please authenticate via the app so the backend can inject your per-user token."
    )

# -----------------------------
# Behavior toggles
# -----------------------------
# IMPORTANT: stitching can create "fake doors" by connecting nearest nodes across components.
# Keep it OFF unless you are diagnosing disconnected graphs.
ENABLE_STITCHING = os.getenv("VERIFIRE_ENABLE_STITCHING", "0").strip().lower() in ("1", "true", "yes")

# If ON, we overwrite the original G_<Level>.pkl with updated node metadata.
# Default OFF to avoid persisting any accidental topology changes.
SAVE_UPDATED_GRAPH = os.getenv("VERIFIRE_SAVE_UPDATED_GRAPH", "0").strip().lower() in ("1", "true", "yes")

# Debug: print any edges that exist in the working graph but not the visualization graph
PRINT_EDGE_DIFFS = os.getenv("VERIFIRE_PRINT_EDGE_DIFFS", "0").strip().lower() in ("1", "true", "yes")

from specklepy.api.client import SpeckleClient
from specklepy.api.wrapper import StreamWrapper

import networkx as nx

from path_of_travel import (
    stitch_subgraphs,
    map_room_center_to_start_nodes,
    map_farthest_point_from_door,
    find_shortest_paths,
    visualize_shortest_paths,
)

from debug_utils import (
    report_unreachable_start_nodes,
    inspect_exit_node_connectivity,
    inspect_graph_z_levels,
    check_graph_connectivity,
    clean_speckle_objects,
    debug_door_connections,
)

from send_utils_225 import send_paths_to_speckle, graph_to_speckle_objects


# ---- ProgramData locations ----
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\VeriFire")

# Per-model working folder:  C:\ProgramData\VeriFire\<PROJECT_ID>_<MODEL_ID>\
PROJ_DIR   = PROGRAMDATA_ROOT / f"{PROJECT_ID}_{MODEL_ID}"
GRAPHS_DIR = PROJ_DIR / "graphs"
PATHS_DIR  = PROJ_DIR / "paths"

USER_INPUTS_PATH = PROGRAMDATA_ROOT / "user_inputs" / "user_inputs.json"

for p in (PROGRAMDATA_ROOT, PROJ_DIR, GRAPHS_DIR, PATHS_DIR, USER_INPUTS_PATH.parent):
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        # best-effort only
        pass

def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "t", "yes", "y", "on")

DEV = _env_bool("DEV", default=False)
print(f"[paths] DEV={DEV} (raw env DEV={os.getenv('DEV')!r})")

def _safe_unlink(p: Path) -> None:
    try:
        p.unlink()
    except PermissionError:
        try:
            os.chmod(p, stat.S_IWRITE)
            p.unlink()
        except Exception:
            pass
    except FileNotFoundError:
        pass


def _read_json_bom_safe(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _normalize_id(x) -> str:
    return str(x).strip()


def _mark_exits_from_ids(G: nx.Graph, ids) -> int:
    """Tag nodes as exits if any of their common ID fields match provided IDs."""
    if not ids:
        return 0

    idset = set(_normalize_id(i) for i in ids if _normalize_id(i))
    hits = 0

    for _, data in G.nodes(data=True):
        candidates = (
            data.get("source_id"),
            data.get("speckle_id"),
            data.get("element_id"),
            data.get("id"),
            data.get("door_id"),
        )
        for c in candidates:
            if _normalize_id(c) in idset:
                if not data.get("is_exit", False):
                    data["is_exit"] = True
                    hits += 1
                break

    return hits


def _edge_key(u, v):
    # undirected graph edge key
    return (u, v) if u <= v else (v, u)


def _print_edge_diffs(G_vis: nx.Graph, G_work: nx.Graph, limit: int = 25) -> None:
    vis = set(_edge_key(u, v) for u, v in G_vis.edges())
    work = set(_edge_key(u, v) for u, v in G_work.edges())
    added = list(work - vis)
    if not added:
        print("✅ No extra edges were introduced in the working graph.")
        return

    print(f"⚠️  Working graph has {len(added)} edge(s) not present in the visualization graph.")
    # Show a few with room metadata to quickly spot cross-room leaks
    shown = 0
    for u, v in added:
        du = G_work.nodes[u]
        dv = G_work.nodes[v]
        ru = du.get("room_id")
        rv = dv.get("room_id")
        tu = du.get("type")
        tv = dv.get("type")
        w = G_work.get_edge_data(u, v) or {}
        print(f"  + {u} ({tu}, room={ru})  <->  {v} ({tv}, room={rv}) | attrs={{{', '.join(list(w.keys())[:6])}}}")
        shown += 1
        if shown >= limit:
            break


def main() -> None:
    print("🔥 Starting Fire Safety Compliance Check...")
    print(f"[paths] PROJECT_ID={PROJECT_ID} MODEL_ID={MODEL_ID}")
    print(f"[paths] GRAPHS_DIR={GRAPHS_DIR}")
    print(f"[paths] PATHS_DIR={PATHS_DIR}")
    print(f"[paths] USER_INPUTS_PATH={USER_INPUTS_PATH}")
    print(f"[paths] ENABLE_STITCHING={ENABLE_STITCHING} SAVE_UPDATED_GRAPH={SAVE_UPDATED_GRAPH}")

    # Load user inputs (BOM-safe)
    user_inputs = {}
    try:
        if USER_INPUTS_PATH.exists():
            user_inputs = _read_json_bom_safe(USER_INPUTS_PATH)
            print(f"📥 Loaded user inputs from {USER_INPUTS_PATH}")
            try:
                print(f" Keys: {list(user_inputs.keys())}")
            except Exception:
                pass
        else:
            print(f"ℹ️ No user_inputs.json at {USER_INPUTS_PATH} (continuing)")
    except Exception as e:
        print(f"❌ Failed to read user inputs: {e} (continuing)")

    # Speckle auth (per-user token only)
    wrapper = StreamWrapper(f"{SPECKLE_SERVER_URL}/streams/{PROJECT_ID}/branches/main")
    client = SpeckleClient(host=wrapper.host)
    client.authenticate_with_token(SPECKLE_USER_TOKEN)
    print("✅ Authenticated to Speckle with per-user token.")

    # Load graphs from ProgramData
    graph_files = sorted([str(p) for p in GRAPHS_DIR.glob("G_*.pkl")])
    if not graph_files:
        print(f"❌ No graph pickle files found in '{GRAPHS_DIR}'")
        return

    for graph_path in graph_files:
        level_name = Path(graph_path).stem.split("_", 1)[-1]
        print(f"\n🏗️ Processing Floor: {level_name}")

        # Load graph
        try:
            with open(graph_path, "rb") as f:
                G_loaded: nx.Graph = pickle.load(f)
        except Exception as e:
            print(f"❌ Failed to load graph from {graph_path}: {e}")
            continue

        # Keep a clean graph for visualization/export.
        G_vis = G_loaded

        # Working copy for any operation that might add edges.
        G_work = G_loaded.copy()

        # Optional stitching (ON WORK GRAPH ONLY)
        try:
            cc = nx.number_connected_components(G_work)
            if cc > 1:
                print(f"🔗 Graph has {cc} connected component(s).")
                if ENABLE_STITCHING:
                    print("🔧 Stitching enabled → connecting subgraphs (may add artificial edges).")
                    stitch_subgraphs(G_work)
                    print("✅ Subgraphs stitched (working graph only).")
                else:
                    print("ℹ️ Stitching disabled → leaving subgraphs disconnected (recommended).")
            else:
                print("✅ Graph is already fully connected.")
        except Exception as e:
            print(f"❌ Stitching step failed: {e}")
            continue

        # Per-floor IDs from user_inputs
        ids = []
        try:
            raw = user_inputs.get(level_name, [])
            if isinstance(raw, str):
                ids = [s.strip() for s in raw.split(",") if s.strip()]
            elif isinstance(raw, list):
                ids = [str(x).strip() for x in raw if str(x).strip()]
            else:
                ids = []
        except Exception:
            ids = []

        print(f"✅ Floor {level_name} → Emergency exit IDs (from ProgramData): {ids}")

        # Start/exit inference + tag exits (apply to both graphs; node-attrs only)
        try:
            # map_room_center_to_start_nodes(G_vis)  # optional
            # map_room_center_to_start_nodes(G_work)

            map_farthest_point_from_door(G_vis)
            map_farthest_point_from_door(G_work)

            tagged_vis = _mark_exits_from_ids(G_vis, ids)
            tagged_work = _mark_exits_from_ids(G_work, ids)
            print(f"🏷️ Marked exits: vis={tagged_vis}, work={tagged_work}")

            # Debug helper might add edges in some implementations → run ONLY on work.
            try:
                debug_door_connections(G_work)
            except Exception as e:
                print(f"⚠️ debug_door_connections failed (non-fatal): {e}")

            if SAVE_UPDATED_GRAPH:
                with open(graph_path, "wb") as f_out:
                    pickle.dump(G_vis, f_out)
                print("💾 Updated graph saved (node metadata only).")
            else:
                print("ℹ️ Not overwriting graph pickle (VERIFIRE_SAVE_UPDATED_GRAPH=0).")

        except Exception as e:
            print(f"❌ Failed to update graph with start/exit metadata: {e}")
            continue

        if PRINT_EDGE_DIFFS:
            _print_edge_diffs(G_vis, G_work)

        # Paths (RUN ON WORK GRAPH ONLY)
        try:
            G_work.graph["level_name"] = level_name
            G_work.graph["programdata_root"] = str(PROJ_DIR)
            G_work.graph["selected_exit_ids"] = ids

            paths = find_shortest_paths(
                G_work,
                algorithm="theta_star",
                max_jump_distance=2.0,
            )
        except Exception as e:
            print(f"❌ Pathfinding failed for floor {level_name}: {e}")
            paths = []

        # Save paths in ProgramData
        PATHS_DIR.mkdir(parents=True, exist_ok=True)
        path_file = PATHS_DIR / f"paths_{level_name}.pkl"
        if path_file.exists():
            print(f"♻️ Removing existing paths file: {path_file}")
            _safe_unlink(path_file)

        try:
            with path_file.open("wb") as f_out:
                pickle.dump(paths, f_out)
            print(f"✅ Paths saved to {path_file}")
        except Exception as e:
            print(f"❌ Failed to save paths: {e}")
            continue

        # Debug (non-fatal) — report based on working graph used for routing
        try:
            inspect_graph_z_levels(G_work)
            report_unreachable_start_nodes(G_work, paths)
            inspect_exit_node_connectivity(G_work)
            check_graph_connectivity(G_work)
        except Exception as e:
            print(f"⚠️ Debugging failed for floor {level_name}: {e}")

        # Upload
        # - Paths come from G_work
        # - Graph visualization should remain the original generated grid (G_vis)
        path_lines = visualize_shortest_paths(paths, level_name=level_name)
        raw_graph_objects = graph_to_speckle_objects(
            G_vis,
            level_name=level_name,
            wall_lines=[],
            commit_edges=True,
        )
        graph_objects = clean_speckle_objects(raw_graph_objects)

        if DEV:
            try:
                send_paths_to_speckle(
                    graph_objects,
                    path_lines,
                    client,
                    PROJECT_ID,
                    level_name,
                )
            except Exception as e:
                print(f"❌ Failed to upload results for {level_name}: {e}")
        else:
            print(f"DEV=false → skipping send_paths_to_speckle for {level_name}")


    print("\n✅ Fire Safety Compliance Check Complete.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # make sure errors show up in backend logs
        print(f"❌ Unhandled error in run_paths_main.py: {e}", file=sys.stderr)
        raise
