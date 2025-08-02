import os
import pickle
from collections import defaultdict

def get_default_exit_ids_from_all_paths(path_dir="paths") -> dict:
    level_to_exit_ids = {}
    for filename in os.listdir(path_dir):
        if not filename.startswith("paths_") or not filename.endswith(".pkl"):
            continue

        level_name = filename.replace("paths_", "").replace(".pkl", "")
        file_path = os.path.join(path_dir, filename)

        try:
            with open(file_path, "rb") as f:
                data = pickle.load(f)

            default_exits = {
                obj.get("exit_source_id")
                for obj in data
                if obj.get("exit_type") == "default_exit"
            }
            level_to_exit_ids[level_name] = sorted(e for e in default_exits if e)

        except Exception as e:
            print(f"⚠️ Failed to load default exits from {file_path}: {e}")

    return level_to_exit_ids


def get_exit_door_widths_from_all_paths(directory: str) -> dict[str, dict[str, float]]:
    level_door_widths = defaultdict(dict)
    for fname in os.listdir(directory):
        if fname.startswith("paths_") and fname.endswith(".pkl"):
            level_name = fname[len("paths_"):-len(".pkl")]
            try:
                with open(os.path.join(directory, fname), "rb") as f:
                    data = pickle.load(f)
                    if isinstance(data, list):
                        for path in data:
                            eid = path.get("exit_source_id")
                            width = path.get("exit_door_width")
                            if eid and isinstance(width, (int, float)):
                                level_door_widths[level_name][eid] = width
            except Exception as e:
                print(f"⚠️ Failed to process {fname}: {e}")
    return dict(level_door_widths)

def get_required_exits(occupant_load: int) -> int:
    if occupant_load <= 500:
        return 2
    elif occupant_load <= 1000:
        return 3
    else:
        return 4