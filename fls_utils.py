import os
import pickle
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional

# ProgramData base
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\VeriFire")

# --- NEW: infer per-project base from env (preferred) ---
def _project_base_from_env() -> Optional[Path]:
    """
    Build \ProgramData\VeriFire\<PROJECT_ID>_<MODEL_ID>\  from env vars, if present.
    MODEL_ID may include '@commit'—strip it.
    """
    proj = (os.environ.get("PROJECT_ID") or "").strip()
    model = (os.environ.get("MODEL_ID") or "").strip().split("@", 1)[0]
    if proj and model:
        key = f"{proj}_{model}"
        # keep it simple; sanitize if you expect spaces
        return PROGRAMDATA_ROOT / key
    return None

# Optional override for testing
ENV_PATHS_DIR = (os.environ.get("FLS_PATHS_DIR") or "").strip()

# Default search base:
# 1) explicit FLS_PATHS_DIR (absolute or relative to project base)   [most specific]
# 2) per-project base from env -> <base>/paths                      [preferred]
# 3) legacy global: C:\ProgramData\VeriFire\paths                   [fallback]
def _default_paths_dir() -> Path:
    if ENV_PATHS_DIR:
        p = Path(ENV_PATHS_DIR)
        if p.is_absolute():
            return p
        proj_base = _project_base_from_env()
        return (proj_base / p) if proj_base else (PROGRAMDATA_ROOT / p)
    proj_base = _project_base_from_env()
    if proj_base:
        return proj_base / "paths"
    return PROGRAMDATA_ROOT / "paths"  # legacy fallback

def _resolve_dir(path_dir) -> Path:
    """
    Resolve the directory to read path pickles from.
    - If path_dir is absolute -> use it.
    - If path_dir is relative or falsy -> resolve under the per-project base if available,
      otherwise fall back to the legacy global folder.
    """
    if isinstance(path_dir, Path):
        return path_dir if path_dir.is_absolute() else _default_paths_dir()
    if not path_dir:
        return _default_paths_dir()
    p = Path(str(path_dir))
    if p.is_absolute():
        return p
    # relative: prefer per-project base
    proj_base = _project_base_from_env()
    return (proj_base / p) if proj_base else (PROGRAMDATA_ROOT / p)


def get_default_exit_ids_from_all_paths(path_dir=None) -> dict:
    """
    Return { level_name: [exit_source_id, ...] } extracted from paths_*.pkl
    for exits tagged as 'default_exit'.
    """
    root = _resolve_dir(path_dir)
    level_to_exit_ids: Dict[str, list] = {}

    if not root.exists():
        print(f"⚠️ paths directory not found: {root}")
        return level_to_exit_ids

    try:
        for fname in os.listdir(root):
            if not (fname.startswith("paths_") and fname.endswith(".pkl")):
                continue

            level_name = fname[len("paths_"):-len(".pkl")]
            file_path = root / fname

            try:
                with open(file_path, "rb") as f:
                    data = pickle.load(f)

                default_exits = {
                    obj.get("exit_source_id")
                    for obj in (data if isinstance(data, list) else [])
                    if isinstance(obj, dict) and obj.get("exit_type") == "default_exit"
                }
                level_to_exit_ids[level_name] = sorted(e for e in default_exits if e)

            except Exception as e:
                print(f"⚠️ Failed to load default exits from {file_path}: {e}")
    except Exception as e:
        print(f"⚠️ Failed to list directory {root}: {e}")

    return level_to_exit_ids


def get_exit_door_widths_from_all_paths(directory: str | Path | None = None) -> dict[str, dict[str, float]]:
    """
    Return { level_name: { exit_source_id: width, ... }, ... } by scanning paths_*.pkl.
    """
    root = _resolve_dir(directory)
    level_door_widths: defaultdict[str, dict[str, float]] = defaultdict(dict)

    if not root.exists():
        print(f"⚠️ paths directory not found: {root}")
        return dict(level_door_widths)

    try:
        for fname in os.listdir(root):
            if not (fname.startswith("paths_") and fname.endswith(".pkl")):
                continue
            level_name = fname[len("paths_"):-len(".pkl")]
            try:
                with open(root / fname, "rb") as f:
                    data = pickle.load(f)
                    if isinstance(data, list):
                        for path in data:
                            if not isinstance(path, dict):
                                continue
                            eid = path.get("exit_source_id")
                            width = path.get("exit_door_width")
                            if eid and isinstance(width, (int, float)):
                                level_door_widths[level_name][str(eid)] = float(width)
            except Exception as e:
                print(f"⚠️ Failed to process {fname}: {e}")
    except Exception as e:
        print(f"⚠️ Failed to list directory {root}: {e}")

    return dict(level_door_widths)


def get_required_exits(occupant_load: int) -> int:
    if occupant_load <= 500:
        return 2
    elif occupant_load <= 1000:
        return 3
    else:
        return 4
