#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
End-to-end script:
1) Authenticate to Speckle and fetch latest commit from a branch (MODEL_ID).
2) Extract Rooms/Walls/Doors/Stairs from the commit object.
3) Build/trim gridlines, construct a per-floor graph, add doors/stairs overlays.
4) Persist each floor's graph as a pickle under \ProgramData\VeriFire\<PROJECT_ID>_<MODEL_ID>\graphs.
5) Send graph geometry back to Speckle.

Notes:
- Keeps your original logic and structure, but wraps it in functions with light
  error handling and logging. Behavior is otherwise the same.
- Still patches Speckle units to avoid crashing on unexpected unit strings.
"""

from __future__ import annotations

import io
import os
import re
import sys
import stat
import shutil
import pickle
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, List

# Ensure UTF-8 encoding for stdout (Windows-safe)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
log = logging.getLogger("verifire.grid")

# -----------------------------
# Third-party / project imports
# -----------------------------
from specklepy.api.client import SpeckleClient
from specklepy.api.wrapper import StreamWrapper
from specklepy.api import operations
from specklepy.transports.server import ServerTransport
from specklepy.objects.base import Base
from specklepy.objects.units import get_units_from_string

from speckle_credentials import (
    SPECKLE_SERVER_URL,
    PROJECT_ID as PROJECT_ID_DEFAULT,
    MODEL_ID as MODEL_ID_DEFAULT,
)

from extract_elements import extract_elements_by_type
from generate_grid import (
    group_rooms_by_level,
    group_walls_by_level,
    group_doors_by_level,
    group_stairs_by_level,
    generate_extended_gridlines_per_floor,
    trim_gridlines,
    compute_global_bounds,
    create_graph,
    add_doors_on_grid,
    add_stairs_on_grid,
)
from send_utils_225 import graph_to_speckle_objects, send_graph_to_speckle_per_floor

# --- Per-request overrides from backend (headers -> env) ---
# Backend sets these via subprocess env when invoking this script.
PROJECT_ID = (os.getenv("PROJECT_ID") or PROJECT_ID_DEFAULT).strip()
MODEL_ID   = ((os.getenv("MODEL_ID") or MODEL_ID_DEFAULT).strip()).split("@", 1)[0]

# 🔐 Per-user token injected by backend. REQUIRED.
SPECKLE_USER_TOKEN = os.getenv("SPECKLE_USER_TOKEN")

if not PROJECT_ID:
    raise RuntimeError("PROJECT_ID is not set (env or default).")
if not MODEL_ID:
    raise RuntimeError("MODEL_ID is not set (env or default).")

# -----------------------------
# ProgramData paths (match extract_elements.py logic)
# -----------------------------
def _sanitize(s: str) -> str:
    # keep simple chars to avoid filesystem issues
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s or "")

PROGRAMDATA_BASE = Path(r"C:\ProgramData\VeriFire")
PROJECT_MODEL = f"{_sanitize(PROJECT_ID)}_{_sanitize(MODEL_ID)}"

# Final folders we’ll use for this run
GRAPHS_DIR = PROGRAMDATA_BASE / PROJECT_MODEL / "graphs"
LOGS_DIR   = PROGRAMDATA_BASE / PROJECT_MODEL / "invalid_units"

for p in (GRAPHS_DIR, LOGS_DIR):
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Best-effort only; downstream writes may still succeed
        pass

INVALID_UNITS_LOG = LOGS_DIR / "invalid_units_log.txt"

# Tunables (kept as in your script; can be env-overridden)
GRID_SPACING_M = float(os.getenv("GRID_SPACING_M", "0.5"))
GAP_OFFSET_M = float(os.getenv("GAP_OFFSET_M", "0.3"))

# -----------------------------
# Patch: Speckle Base.units setter
# -----------------------------
_invalid_units_seen = set()

def _safe_units_setter(self: Base, value: Any) -> None:
    if not isinstance(value, str) or not value:
        self.__dict__["units"] = None
        return
    try:
        self.__dict__["units"] = get_units_from_string(value)
    except Exception:
        if value not in _invalid_units_seen:
            _invalid_units_seen.add(value)
            try:
                with INVALID_UNITS_LOG.open("a", encoding="utf-8") as f:
                    f.write(f"{value}\n")
            except Exception:
                pass  # logging must never crash the run
        self.__dict__["units"] = None

_original_units_prop = Base.__dict__.get("units")
Base.units = property(
    fget=_original_units_prop.fget if _original_units_prop else None,
    fset=_safe_units_setter,
    fdel=_original_units_prop.fdel if _original_units_prop else None,
)

# -----------------------------
# Helpers
# -----------------------------
def _rm_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

def clear_graphs_dir(graphs_dir: Path) -> None:
    """Safely clear graphs dir. If it's a symlink/junction, only clear contents."""
    if graphs_dir.exists():
        try:
            is_link = graphs_dir.is_symlink()
        except Exception:
            is_link = os.path.islink(str(graphs_dir))

        if is_link:
            for child in graphs_dir.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, onerror=_rm_readonly)
                    else:
                        child.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            shutil.rmtree(graphs_dir, onerror=_rm_readonly)
            graphs_dir.mkdir(parents=True, exist_ok=True)
    else:
        graphs_dir.mkdir(parents=True, exist_ok=True)

def speckle_authenticate() -> Tuple[SpeckleClient, str]:
    if not SPECKLE_USER_TOKEN:
        # Do not fall back to any .env token – force users to authenticate.
        raise RuntimeError(
            "Missing SPECKLE_USER_TOKEN in environment. "
            "Please authenticate via the app so the backend can inject your per-user token."
        )

    project_url = f"{SPECKLE_SERVER_URL}/streams/{PROJECT_ID}/branches/main"
    _ = StreamWrapper(project_url)  # optional parity with your old flow

    client = SpeckleClient(host=SPECKLE_SERVER_URL)
    client.authenticate_with_token(SPECKLE_USER_TOKEN)
    log.info("Speckle client authenticated with per-user token.")
    return client, project_url

def load_latest_commit_object(client: SpeckleClient) -> Any:
    stream = client.stream.get(PROJECT_ID)
    if not stream:
        raise RuntimeError(f"Stream not found: {PROJECT_ID}")

    branch = client.branch.get(PROJECT_ID, MODEL_ID)
    if not branch:
        raise RuntimeError(f"Branch (model) not found: {MODEL_ID}")

    log.info(f"Branch Name: {branch.name}")
    log.info(f"Branch ID:   {branch.id}")

    # Prefer the branch's commits listing; fall back to commit.list
    default_commit = branch.commits.items[-1] if branch.commits and branch.commits.items else None
    if not default_commit:
        commits = client.commit.list(PROJECT_ID, MODEL_ID)
        if not commits:
            raise RuntimeError("No commits found on the specified branch/model.")
        default_commit = commits[0]  # most recent first

    log.info(f"Using commit: {default_commit.id}")

    transport = ServerTransport(client=client, stream_id=PROJECT_ID)
    root_obj = operations.receive(default_commit.referencedObject, transport)
    return root_obj

def build_level_alias_map_from(elements_lists) -> dict:
    """
    Build a mapping that normalizes any '*-CL' (or variants) to the corresponding '*-FFL'.
    Examples handled:
      "P-03-3RD-CL"    -> "P-03-3RD-FFL"
      "Level 3 - CL"   -> "Level 3 - FFL"
      "LEVEL 5 (CL)"   -> "LEVEL 5 (FFL)"
      "L2_CL"          -> "L2_FFL"
    If an FFL twin doesn't exist, we still map to the synthesized FFL key so that
    grouping collapses under FFL.
    """
    level_names = set()

    def _collect(obj):
        lvl = getattr(obj, "level", None)
        name = getattr(lvl, "name", None) if lvl else None
        if isinstance(name, str) and name.strip():
            level_names.add(name.strip())

    # Collect level names from all categories
    for lst in elements_lists:
        for obj in lst:
            _collect(obj)

    alias = {}
    for name in list(level_names):
        n = name.strip()

        # Normalize common CL suffix forms
        # Case 1: "...-CL"
        if n.endswith("-CL"):
            alias[n] = n[:-3] + "-FFL"
            continue

        # Case 2: "... - CL"
        if n.endswith(" - CL"):
            alias[n] = n[:-5] + " - FFL"
            continue

        # Case 3: "... (CL)"
        if n.endswith("(CL)"):
            alias[n] = n[:-4] + "(FFL)"
            continue

        # Case 4: "..._CL"
        if n.endswith("_CL"):
            alias[n] = n[:-3] + "_FFL"
            continue

        # Conservative generic: lone trailing " CL"
        if n.endswith(" CL"):
            alias[n] = n[:-3] + " FFL"
            continue

    return alias

def extract_and_group(root_obj: Any) -> Tuple[Dict[str, list], Dict[str, list], Dict[str, list], Dict[str, list], Dict[str, Any]]:
    elements = getattr(root_obj, "elements", None)
    if not elements:
        raise RuntimeError("No 'elements' array found on commit object.")

    elements_extracted = extract_elements_by_type(elements, save_to_path=True)
    log.info("\n✅ Extraction Summary:")
    for k, v in elements_extracted.items():
        log.info("  %-6s: %d elements", k, len(v))

    rooms = elements_extracted.get("Rooms", [])
    walls = elements_extracted.get("Walls", [])
    doors = elements_extracted.get("Doors", [])
    stairs = elements_extracted.get("Stairs", [])

    global_bounds = compute_global_bounds(rooms, walls, doors)

    # NEW: build a CL→FFL alias map and pass it to all groupers
    level_alias_map = build_level_alias_map_from([rooms, walls, doors, stairs])
    if level_alias_map:
        log.info("🔁 Level alias map (CL→FFL) enabled with %d rule(s).", len(level_alias_map))

    room_floors  = group_rooms_by_level(rooms,  level_alias_map=level_alias_map)
    wall_floors  = group_walls_by_level(walls,  level_alias_map=level_alias_map)
    door_floors  = group_doors_by_level(doors,  level_alias_map=level_alias_map)
    stair_floors = group_stairs_by_level(stairs, level_alias_map=level_alias_map)

    return room_floors, wall_floors, door_floors, stair_floors, {"global_bounds": global_bounds}


def process_floor(
    level_name: str,
    rooms_on_level: list,
    walls_on_level: list,
    doors_on_level: list,
    stairs_on_level: list,
    global_bounds: Any,
    client: SpeckleClient,
) -> None:
    log.info("\n🔄 Processing Floor: %s (%d rooms)", level_name, len(rooms_on_level))

    # 1) Generate raw gridlines
    gridlines_raw, _, grid_dict = generate_extended_gridlines_per_floor(
        rooms=rooms_on_level,
        walls=walls_on_level,
        doors=doors_on_level,
        spacing=GRID_SPACING_M,
        level_name=level_name,
        global_bounds=global_bounds,
    )

    # 2) Extract wall lines (2D) for trimming
    _, wall_lines_2d = create_graph(
        rooms=rooms_on_level,
        walls=walls_on_level,
        doors=doors_on_level,
        gridlines=[],  # only want wall_lines_2d here
        level_name=level_name,
    )

    # 3) Trim gridlines against walls/doors
    final_gridlines = trim_gridlines(
        gridlines_raw,
        wall_lines_2d,
        doors_on_level,
        grid_dict,
        spacing=GRID_SPACING_M,
        gap_offset=GAP_OFFSET_M,
    )

    # 4) Build final graph
    G_floor, wall_lines_2d = create_graph(
        rooms=rooms_on_level,
        walls=walls_on_level,
        doors=doors_on_level,
        gridlines=final_gridlines,
        level_name=level_name,
    )

    # 5) Overlays
    add_doors_on_grid(G_floor, doors_on_level)
    add_stairs_on_grid(G_floor, stairs_on_level)

    # 6) Persist pickle in ProgramData\<PROJECT_ID>_<MODEL_ID>\graphs
    out_pkl = GRAPHS_DIR / f"G_{level_name}.pkl"
    try:
        with open(out_pkl, "wb") as f:
            pickle.dump(G_floor, f)
        log.info("Saved graph: %s", out_pkl)
    except Exception as e:
        log.warning("Failed to write graph pickle for %s: %s", level_name, e)

    # 7) Send Speckle objects (unchanged)
    graph_objects = graph_to_speckle_objects(
        G_floor,
        level_name=level_name,
        wall_lines=wall_lines_2d,
        commit_edges=True,
    )

    send_graph_to_speckle_per_floor(
        graph_objects,
        client,
        PROJECT_ID,
        level_name,
    )

def main() -> int:
    # Show where we’re writing for this run (handy for debugging)
    log.info("[INFO] Output root  : %s", (PROGRAMDATA_BASE / PROJECT_MODEL))
    log.info("[INFO] Graphs dir   : %s", GRAPHS_DIR)
    log.info("[INFO] Logs dir     : %s", LOGS_DIR)

    # Prep output folder for graphs (clear only the graphs folder for this project/model)
    clear_graphs_dir(GRAPHS_DIR)

    # Speckle
    client, _ = speckle_authenticate()

    # Latest commit object
    root_obj = load_latest_commit_object(client)

    # Extract + group
    room_floors, wall_floors, door_floors, stair_floors, ctx = extract_and_group(root_obj)
    global_bounds = ctx["global_bounds"]

    # Per-floor processing
    for level_name, rooms_on_level in room_floors.items():
        # If a CL key somehow survived and we also have the FFL twin, skip the CL
        if level_name.endswith("-CL"):
            ffl_twin = level_name[:-3] + "-FFL"
            if ffl_twin in room_floors:
                log.info("↩️  Skipping '%s' because '%s' exists (collapsed to FFL).", level_name, ffl_twin)
                continue

        walls_on_level  = wall_floors.get(level_name, [])
        doors_on_level  = door_floors.get(level_name, [])
        stairs_on_level = stair_floors.get(level_name, [])

        process_floor(
            level_name=level_name,
            rooms_on_level=rooms_on_level,
            walls_on_level=walls_on_level,
            doors_on_level=doors_on_level,
            stairs_on_level=stairs_on_level,
            global_bounds=global_bounds,
            client=client,
        )

    log.info("\n✅ Done.")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
        raise SystemExit(130)
