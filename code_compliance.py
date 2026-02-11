import os
import json
import pickle
import sys
import math
import time
import subprocess
from specklepy.objects import Base
from specklepy.objects.other import RenderMaterial
from typing import List, Dict, Any
from more_itertools import chunked
from path_of_travel import euclidean_distance

def _resolve_venv_python(venv_name: str = "langchain_venv") -> str:
    """
    Return an absolute path to the Python executable inside the given venv.
    Searches common locations relative to this file and the current working dir.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, venv_name, "Scripts", "python.exe"),  # Windows
        os.path.join(here, venv_name, "bin", "python"),          # POSIX
        os.path.join(os.getcwd(), venv_name, "Scripts", "python.exe"),
        os.path.join(os.getcwd(), venv_name, "bin", "python"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    raise FileNotFoundError(
        f"Could not locate Python for venv '{venv_name}'. "
        f"Tried: {candidates}"
    )

# 🔧 pin the interpreter used for ALL LLM subprocess calls
LANGCHAIN_PYTHON = _resolve_venv_python("langchain_venv")

def _run_langchain_script(script_name: str, *script_args: str, expect_json: bool = True) -> Any:
    """
    Run a helper script (e.g., llm_classify.py) under langchain_venv and optionally parse JSON.
    """
    cwd = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(cwd, script_name)

    if not os.path.isfile(script):
        raise FileNotFoundError(f"Missing helper script: {script}")

    env = os.environ.copy()
    # If your langchain_venv needs extra env vars, set them here: env["OPENAI_API_KEY"] = "..."
    result = subprocess.run(
        [LANGCHAIN_PYTHON, script, *script_args],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        env=env,
    )

    if not expect_json:
        return result.stdout

    # Parse the last valid JSON line (handles extra logs above)
    lines = result.stdout.strip().splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise ValueError(
        f"No JSON found in stdout.\n--- STDOUT ---\n{result.stdout}\n--- STDERR ---\n{result.stderr}"
    )

def run_llm_classify_batch(room_names: List[str]) -> Dict[str, str]:
    """
    Calls llm_classify.py (in langchain_venv) with chunks of room names.
    Expects each call to output a JSON dict mapping room_name -> classification.
    """
    results: Dict[str, str] = {}
    for batch in chunked(room_names, 10):
        try:
            payload = json.dumps(list(batch), ensure_ascii=False)
            out = _run_langchain_script("llm_classify.py", payload, expect_json=True)
            if isinstance(out, dict):
                results.update(out)
            else:
                print(f"[ERROR] Unexpected output type from llm_classify.py: {type(out)}", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Subprocess failed on batch {batch}: {e}", flush=True)
            print("STDOUT:\n", e.stdout, flush=True)
            print("STDERR:\n", e.stderr, flush=True)
        except Exception as e:
            print(f"[ERROR] Unknown error on batch {batch}: {e}", flush=True)
        time.sleep(0.2)
    return results

def run_llm_olf_batch(space_functions: List[str]) -> Dict[str, Any]:
    """
    If you have an OLF helper (e.g., llm_olf.py), run it under langchain_venv too.
    """
    try:
        payload = json.dumps(list(space_functions), ensure_ascii=False)
        out = _run_langchain_script("llm_olf.py", payload, expect_json=True)
        return out if isinstance(out, dict) else {}
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Subprocess error (olf):\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}", flush=True)
        return {}
    except Exception as e:
        print(f"[ERROR] Unknown error (olf): {e}", flush=True)
        return {}


def run_llm_max_occupancy_batch(classifications: List[str]) -> Dict[str, Any]:
    """
    Calls llm_max_occupancy.py (in langchain_venv) with a list of classifications.
    Expects JSON output (e.g., {classification: max_occupancy, ...}).
    """
    try:
        payload = json.dumps(list(classifications), ensure_ascii=False)
        out = _run_langchain_script("llm_max_occupancy.py", payload, expect_json=True)
        return out if isinstance(out, dict) else {}
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Subprocess error (max_occupancy):\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}", flush=True)
        return {}
    except Exception as e:
        print(f"[ERROR] Unknown error (max_occupancy): {e}", flush=True)
        return {}




# Temporary cache for the current floor
classification_results = {}
olf_results = {}
max_occupancy_results = {}

def building_classification(room: Base, all_rooms: list[Base] = None) -> str | None:
    global classification_results

    name = getattr(room, "name", "").strip()
    if name in classification_results:
        return classification_results[name]

    print(f"[ERROR] Classification not preloaded for room: {name}")
    return None

def occupant_load_factor(room_name: str, classification: str = "") -> float | None:
    if room_name in olf_results:
        val = olf_results[room_name]
        if isinstance(val, dict) and "olf" in val:
            return float(val["olf"])
        elif isinstance(val, (int, float)):
            return float(val)
    return None

def _hex_to_argb(hex_color: str, opacity: float = 1.0) -> int:
    """
    Convert '#RRGGBB' (or 'RRGGBB') + opacity (0..1) to ARGB int 0xAARRGGBB.
    """
    if not hex_color:
        hex_color = "#FF0000"
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        h = "FF0000"  # fallback red
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except Exception:
        r, g, b = 255, 0, 0
    a = max(0, min(255, int(round(opacity * 255))))
    return (a << 24) | (r << 16) | (g << 8) | b

def color_code_room(
    room: Base,
    *,
    color: str = None,                         # backward-compatible: allow color="#FF0000"
    material: RenderMaterial = None,           # or pass a RenderMaterial directly
    opacity: float = 1.0,                      # optional opacity (0..1)
    category: str = "non_compliant_room"       # optional tag for filtering
) -> Base:
    """
    Speckle 2.25-friendly coloring:
    - If 'material' is provided, use it.
    - Else build a RenderMaterial from 'color' (hex '#RRGGBB') and 'opacity'.
    - Apply material to room and to each item in room.displayValue (meshes), which the viewer uses.
    """
    if not isinstance(room, Base):
        return room

    # Build or reuse material
    mat = material
    if mat is None:
        argb = _hex_to_argb(color or "#FF0000", opacity=opacity)
        mat = RenderMaterial(diffuse=argb, opacity=opacity)

    # Set on the parent (harmless fallback and useful for downstream tooling)
    try:
        room["renderMaterial"] = mat
        if category:
            room["category"] = category
    except Exception:
        pass

    # Apply directly to visible geometry
    try:
        dv = getattr(room, "displayValue", None) or room.get("displayValue", None)
        if dv:
            items = dv if isinstance(dv, (list, tuple)) else [dv]
            for it in items:
                try:
                    it["renderMaterial"] = mat
                    if category:
                        it["category"] = category
                except Exception:
                    continue
    except Exception:
        pass

    return room

# 🛣️ Compute max path from room outline to door
def check_common_path_compliance(room: Base, door_points: list, max_distance: float = 23.0) -> dict:
    if not hasattr(room, "outline") or not room.outline or not door_points:
        return {
            "room": room,
            "common_path": None,
            "is_compliant": False,
            "color_code": color_code_room(room, "#FF0000", opacity=0.4),
            "fls_parameters": fls_parameters(room, comment="❌ Missing outline or door")
        }

    outline_points = []
    for seg in getattr(room.outline, "segments", []):
        if hasattr(seg, "start"):
            outline_points.append((seg.start.x, seg.start.y, seg.start.z))
        if hasattr(seg, "end"):
            outline_points.append((seg.end.x, seg.end.y, seg.end.z))

    max_common_path = max(min(euclidean_distance(pt, d) for d in door_points) for pt in outline_points)
    is_compliant = max_common_path <= max_distance
    note = f"Common Path = {max_common_path:.2f}m (Limit = {max_distance}m)"
    color = "#00FF00" if is_compliant else "#FF0000"
    status = "Compliant" if is_compliant else "Non-Compliant"

    return {
        "room": room,
        "common_path": round(max_common_path, 2),
        "is_compliant": is_compliant,
        "color_code": color_code_room(room, color),
        "fls_parameters": fls_parameters(room, comment=note, status=status)
    }

def fls_parameters(
    room: Base,
    all_rooms: list = None,
    all_doors: list = None,
    comment: str = "",
    status: str = "Non-Compliant",
    classification: str = "",
    travel_distance: float = None,
    common_path: float = None,
    sprinklers: bool = False,
    num_of_exits: int = 0,
    max_occupancy_results: dict = None
) -> Base:
    """
    FLS parameters logic (SAFE / NO SHARED-REFERENCE MUTATION):
    - Keep writing FLS values as generic Speckle properties (room["..."] = ...)
    - Push 3 values into Revit room parameters:
        Fire_Classification
        Fire_Occupant_Load
        Fire_Occupant_Load_Factor
    - Write remaining FLS fields into Revit Comments:
        ALL_MODEL_INSTANCE_COMMENTS.value  (upsert an [FLS] block)
    - IMPORTANT: do NOT overwrite room.applicationId
    """

    import re
    import math
    import json
    import copy
    from specklepy.objects.base import Base

    # ----------------------------
    # Helpers
    # ----------------------------
    def _as_str(x):
        try:
            return "" if x is None else str(x)
        except Exception:
            return ""

    def _get_base(obj, key, default=None):
        try:
            return obj[key]
        except Exception:
            return default

    def _ensure_unique_parameters(room_obj: Base):
        """
        Some connectors/objects may reuse the same 'parameters' Base instance across elements.
        Clone it per-room so later assignments don't leak.
        """
        params_root = getattr(room_obj, "parameters", None) or room_obj.get("parameters", None)
        if isinstance(params_root, Base):
            try:
                params_root = copy.deepcopy(params_root)
            except Exception:
                # fallback: new container (we still can add our params safely)
                params_root = Base()
        elif isinstance(params_root, dict):
            params_root = copy.deepcopy(params_root)
        else:
            params_root = Base()

        # assign back (both dict-style and attr-style)
        room_obj["parameters"] = params_root
        try:
            setattr(room_obj, "parameters", params_root)
        except Exception:
            pass

        return params_root

    def _iter_param_buckets(params_root):
        """
        Yields (bucket_name, bucket_obj) where bucket_obj is dict/Base of paramKey -> ParameterObject.
        Supports:
          - parameters["Instance Parameters"][<guid>] = Parameter
          - parameters[<guid>] = Parameter (flat)
        """
        if not params_root:
            return
        for bname in ("Instance Parameters", "Type Parameters", "Other"):
            b = _get_base(params_root, bname, None)
            if b:
                yield bname, b
        yield "__flat__", params_root

    def _find_param_instance(params_root: Base, *, want_name: str = None, want_internal: str = None):
        """
        Return (container, key, param_obj) for a parameter instance.
        """
        for _, bucket in _iter_param_buckets(params_root):
            try:
                keys = list(bucket.get_dynamic_member_names()) if isinstance(bucket, Base) else list(bucket.keys())
            except Exception:
                continue

            for k in keys:
                p = _get_base(bucket, k, None)
                if not p:
                    continue

                p_name = ""
                p_internal = ""
                try:
                    p_name = _as_str(getattr(p, "name", None) or p.get("name", None)).strip()
                except Exception:
                    pass
                try:
                    p_internal = _as_str(
                        getattr(p, "applicationInternalName", None) or p.get("applicationInternalName", None)
                    ).strip()
                except Exception:
                    pass

                if want_internal and p_internal == want_internal:
                    return bucket, k, p
                if want_name and p_name == want_name:
                    return bucket, k, p

        return None, None, None

    def _safe_clone_param(p):
        if not p:
            return None
        try:
            return copy.deepcopy(p)
        except Exception:
            # last-resort shallow clone
            try:
                q = Base()
                if isinstance(p, Base):
                    for n in p.get_dynamic_member_names():
                        q[n] = getattr(p, n, None)
                elif isinstance(p, dict):
                    for kk, vv in p.items():
                        q[kk] = vv
                return q
            except Exception:
                return p

    def _set_existing_param_value(params_root: Base, *, want_name: str = None, want_internal: str = None, value=None):
        """
        SAFE setter: clone the parameter object BEFORE modifying, then reassign into its container.
        Returns True if updated, False if not found.
        """
        container, key, p = _find_param_instance(params_root, want_name=want_name, want_internal=want_internal)
        if not p:
            return False

        p2 = _safe_clone_param(p)

        try:
            p2["value"] = value
        except Exception:
            try:
                setattr(p2, "value", value)
            except Exception:
                return False

        try:
            container[key] = p2
        except Exception:
            pass

        return True

    def _upsert_fls_block(existing: str, block: str) -> str:
        existing = existing or ""
        pattern = r"\[FLS\].*?\[/FLS\]\s*"
        if re.search(pattern, existing, flags=re.DOTALL):
            return re.sub(pattern, block + "\n", existing, flags=re.DOTALL).strip()
        if existing.strip():
            return (existing.rstrip() + "\n\n" + block).strip()
        return block.strip()

    # ----------------------------
    # Ensure room.parameters is NOT shared
    # ----------------------------
    params_root = _ensure_unique_parameters(room)

    # ----------------------------
    # Resolve classification
    # ----------------------------
    name = getattr(room, "name", "") or ""
    room_name = name.strip()
    key_norm = room_name.upper()

    def _good(s: str) -> bool:
        return bool(s) and s.strip() and s.strip().upper() not in {"UNKNOWN"}

    cls_from_param = (classification or "").strip()

    cls_on_room = ""
    try:
        val = room.get("buildingClassification")
        if isinstance(val, str):
            cls_on_room = val.strip()
    except Exception:
        pass

    cache_cls = ""
    try:
        cache_cls = (classification_results.get(room_name) or classification_results.get(key_norm) or "")
        cache_cls = cache_cls.strip() if isinstance(cache_cls, str) else ""
    except Exception:
        cache_cls = ""

    room_classification = ""
    for cand in (cls_from_param, cls_on_room, cache_cls):
        if _good(cand):
            room_classification = cand
            break
    if not room_classification:
        room_classification = "Unknown"

    room["buildingClassification"] = room_classification

    # ----------------------------
    # Area + OLF + occupancy load
    # ----------------------------
    area_raw = getattr(room, "area", None)
    try:
        area = float(area_raw) if area_raw is not None else 0.0
    except Exception:
        area = 0.0

    olf = occupant_load_factor(room_name, room_classification)
    occupancy_load = math.ceil(area / olf) if (area and olf) else None

    room["occupantLoadFactor"] = olf
    room["occupancyLoad"] = occupancy_load

    # ----------------------------
    # Exits + max occupant load
    # ----------------------------
    room["numOfExits"] = num_of_exits

    maximum_occupant_load = None
    if isinstance(max_occupancy_results, dict):
        entry = (max_occupancy_results.get(room_name) or max_occupancy_results.get(key_norm))
        if isinstance(entry, dict):
            maximum_occupant_load = entry.get("max_occupancy")
        elif isinstance(entry, (int, float)):
            maximum_occupant_load = int(entry)

    room["maximumOccupantLoad"] = maximum_occupant_load

    # ----------------------------
    # Travel distance compliance
    # ----------------------------
    max_distance = 75.0 if sprinklers else 60.0

    valid_td = []
    if isinstance(travel_distance, list):
        for d in travel_distance:
            if isinstance(d, (int, float)):
                valid_td.append(float(d))
    elif isinstance(travel_distance, (int, float)):
        valid_td = [float(travel_distance)]

    has_path = len(valid_td) > 0
    best_td = min(valid_td) if has_path else None
    travel_compliant = has_path and any(d <= max_distance for d in valid_td)

    try:
        num_exits_i = int(num_of_exits) if num_of_exits is not None else 0
    except Exception:
        num_exits_i = 0

    occupancy_exceeds = (
        (maximum_occupant_load is not None)
        and (occupancy_load is not None)
        and (occupancy_load > maximum_occupant_load)
    )
    exits_missing = num_exits_i <= 0
    exits_insufficient = (not exits_missing) and occupancy_exceeds and (num_exits_i < 2)

    exit_compliant = (not exits_missing) and (not exits_insufficient)
    overall_compliant = travel_compliant and exit_compliant

    if overall_compliant:
        status = "Compliant"
    else:
        room = color_code_room(room, color="#FF0000", opacity=0.4)
        status = "Non-Compliant"

        issues = []
        if not has_path:
            issues.append("No egress path to an exit")
        elif best_td is not None and best_td > max_distance:
            issues.append(f"Travel distance exceeded ({best_td:.2f}m > {max_distance:.0f}m)")

        if occupancy_exceeds:
            issues.append(f"Occupancy load exceeds maximum ({int(occupancy_load)} > {int(maximum_occupant_load)})")

        if exits_missing:
            issues.append("No exits detected")
        elif exits_insufficient:
            issues.append(f"Insufficient exits ({num_exits_i}/2)")

        if issues:
            comment = " ".join([f"❌ {s}." for s in issues])
        else:
            comment = "❌ Non-compliant."

    # ----------------------------
    # Generic Speckle properties
    # ----------------------------
    room["fireSafetyNote"] = comment
    room["complianceStatus"] = status
    room["travelDistance"] = travel_distance
    room["sprinklers"] = sprinklers

    # ----------------------------
    # (A) Push into existing Revit parameters (SAFE: clone+reassign)
    # ----------------------------
    _set_existing_param_value(params_root, want_name="Fire_Classification", value=room_classification)

    if occupancy_load is not None:
        _set_existing_param_value(params_root, want_name="Fire_Occupant_Load", value=int(occupancy_load))

    if olf is not None:
        _set_existing_param_value(params_root, want_name="Fire_Occupant_Load_Factor", value=float(olf))

    # ----------------------------
    # (B) Comments: upsert [FLS] block (SAFE: clone+reassign)
    # ----------------------------
    td_display = ""
    try:
        if isinstance(travel_distance, list):
            td_display = (
                f"best={best_td:.2f}m; all={json.dumps(travel_distance)}"
                if best_td is not None else json.dumps(travel_distance)
            )
        elif isinstance(travel_distance, (int, float)):
            td_display = f"{float(travel_distance):.2f}m"
        else:
            td_display = "None"
    except Exception:
        td_display = _as_str(travel_distance)

    fls_block = "\n".join([
        "[FLS]",
        f"Status: {status}",
        f"Note: {comment}",
        f"NumOfExits: {num_exits_i}",
        f"MaxOccupantLoad: {_as_str(maximum_occupant_load)}",
        f"TravelDistance: {td_display}",
        f"Sprinklers: {bool(sprinklers)}",
        f"Classification: {room_classification}",
        f"OccupantLoad: {_as_str(occupancy_load)}",
        f"OLF: {_as_str(olf)}",
        "[/FLS]",
    ])

    # Find by internal name first, then by display name
    container, key, p_comments = _find_param_instance(params_root, want_internal="ALL_MODEL_INSTANCE_COMMENTS")
    if not p_comments:
        container, key, p_comments = _find_param_instance(params_root, want_name="Comments")

    if p_comments:
        p2 = _safe_clone_param(p_comments)

        existing_comments = ""
        try:
            existing_comments = _as_str(getattr(p2, "value", None) or p2.get("value", ""))
        except Exception:
            existing_comments = ""

        new_comments = _upsert_fls_block(existing_comments, fls_block)

        try:
            p2["value"] = new_comments
        except Exception:
            try:
                setattr(p2, "value", new_comments)
            except Exception:
                pass

        try:
            container[key] = p2
        except Exception:
            pass

    return room


def floor_fls_parameters(floor: Base, rooms_on_level: list[Base], level_name: str = None) -> Base:
    """
    Assigns Fire and Life Safety metadata to a Floor object.
    Calculates total occupant load, required/provided exit capacity,
    and determines the building classification for the floor.
    """

    total_occupant_load = 0.0
    classification_counter = {}
    classification_area = {}

    # Use area from floor parameters if available
    floor_area = None
    if hasattr(floor, "parameters") and hasattr(floor.parameters, "Area"):
        floor_area = getattr(floor.parameters, "Area", None)

    for room in rooms_on_level:
        # Occupancy load
        if "occupancyLoad" in room.get_dynamic_member_names():
            total_occupant_load += getattr(room, "occupancyLoad", 0) or 0

        # Classification count
        classification = getattr(room, "buildingClassification", None)
        if classification:
            classification_counter[classification] = classification_counter.get(classification, 0) + 1

        # Area accumulation per classification
        area = getattr(room, "area", 0) or 0
        if classification:
            classification_area[classification] = classification_area.get(classification, 0) + area

    total_occupant_load = round(total_occupant_load)

    # Determine primary and secondary classifications
    if classification_counter:
        main_classification = max(classification_counter, key=classification_counter.get)
    else:
        main_classification = "Unknown"

    if floor_area is None:
        floor_area = sum(classification_area.values())

    secondary_classifications = [
        (cls, area) for cls, area in classification_area.items()
        if cls != main_classification and (area / floor_area) > 0.1
    ]

    if secondary_classifications:
        secondary_classes_only = [cls for cls, _ in sorted(secondary_classifications, key=lambda x: x[1], reverse=True)]
        floor_classification = "Mixed: " + ", ".join([main_classification] + secondary_classes_only)
    else:
        floor_classification = main_classification

    # ✅ Exit data based on .pkl paths
    exit_unit_factor = 5.08  # mm per person

    if not level_name:
        print("⚠️ No level_name provided — skipping exit capacity calculation.")
        exit_door_ids = []
        total_door_width = 0
    else:
        from fls_utils import get_default_exit_ids_from_all_paths, get_exit_door_widths_from_all_paths, get_required_exits

        # Step 1: Default exits from paths
        exit_door_lookup = get_default_exit_ids_from_all_paths("paths")
        exit_door_ids = exit_door_lookup.get(level_name, [])
        print(f"📘 Floor {level_name}: {len(exit_door_ids)} default exits → {exit_door_ids}")

        # Step 2: Corresponding widths from paths
        all_widths = get_exit_door_widths_from_all_paths("paths")
        door_width_lookup = all_widths.get(level_name, {})

        total_door_width = sum(door_width_lookup.get(door_id, 0) for door_id in exit_door_ids)

    exit_capacity_people = round(total_door_width / exit_unit_factor) if total_door_width else 0

    # Attach final metadata
    floor["occupantLoad"] = total_occupant_load
    floor["requiredExits"] = get_required_exits(total_occupant_load)
    floor["providedExits"] = len(exit_door_ids)
    floor["totalExitDoorWidthMM"] = total_door_width
    floor["exitUnitFactorMMPerPerson"] = exit_unit_factor
    floor["exitCapacityPeople"] = exit_capacity_people
    floor["buildingClassification"] = floor_classification

    return floor

def compute_compliance_check(
    paths,
    graph=None,
    all_rooms=None,
    all_floors=None,
    all_doors=None,
    max_occupancy_results=None,
):
    """
    Robust compliance consumer:
      - Accepts many path encodings (path/nodes/node_ids/coords/points).
      - Resolves rooms STRICTLY by Speckle id; any name/level fallback must match id,
        otherwise we keep room=None so results remain keyed by the true room_id.
      - NEW: applies alias_to_primary normalization consistently (graph rooms, paths, door links),
        so num_of_exits counts line up with the ids we iterate over.
    """
    if graph is None:
        raise ValueError("graph is required.")

    from collections import defaultdict, Counter
    import re, math

    # ---------- tiny utils ----------
    def _norm(s):
        s = (s or "").strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^A-Z0-9 ]+", "", s.upper())
        return s

    def _ed(p1, p2):
        x1, y1, z1 = (p1 + (0.0, 0.0, 0.0))[:3] if len(p1) < 3 else p1[:3]
        x2, y2, z2 = (p2 + (0.0, 0.0, 0.0))[:3] if len(p2) < 3 else p2[:3]
        return math.sqrt((x1-x2)**2 + (y1-y2)**2 + (z1-z2)**2)

    def _as_tuple3(p):
        if hasattr(p, "x") and hasattr(p, "y"):
            try: return (float(p.x), float(p.y), float(getattr(p, "z", 0.0)))
            except Exception: return None
        if isinstance(p, dict):
            if "x" in p and "y" in p:
                try: return (float(p["x"]), float(p["y"]), float(p.get("z", 0.0)))
                except Exception: return None
            if "point" in p:
                return _as_tuple3(p["point"])
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                x, y = float(p[0]), float(p[1]); z = float(p[2]) if len(p) > 2 else 0.0
                return (x, y, z)
            except Exception:
                return None
        return None

    def _s(v):
        try:
            return str(v).strip()
        except Exception:
            return ""

    # ---------- alias normalization pulled from graph ----------
    alias_to_primary = {}
    if isinstance(graph.graph.get("alias_to_primary"), dict):
        for a, p in graph.graph["alias_to_primary"].items():
            alias_to_primary[_s(a)] = _s(p)
    if isinstance(graph.graph.get("room_id_aliases"), dict):
        for primary, aliases in graph.graph["room_id_aliases"].items():
            p = _s(primary)
            alias_to_primary[p] = p
            try:
                for a in (aliases or []):
                    alias_to_primary[_s(a)] = p
            except Exception:
                pass

    def _map_id(x: str) -> str:
        x = _s(x)
        return alias_to_primary.get(x, x)

    # ----- graph-wide reverse index for node indices or foreign ids -----
    key_by_idx = {}
    for k, d in graph.nodes(data=True):
        for tag in ("idx", "index", "i", "node_index"):
            if tag in d:
                try: key_by_idx[int(d[tag])] = k
                except Exception: pass

    def _nodekey_to_xyz(node_key):
        t = _as_tuple3(node_key)
        if t: return t
        try:
            d = graph.nodes[node_key]
            t = _as_tuple3(d.get("pos") or d.get("position") or d.get("xyz")
                           or (d.get("x"), d.get("y"), d.get("z", 0.0)))
            if t: return t
        except Exception:
            pass
        try:
            idx = int(node_key)
            if idx in key_by_idx:
                d2 = graph.nodes[key_by_idx[idx]]
                return _as_tuple3(d2.get("pos") or d2.get("position") or d2.get("xyz")
                                  or (d2.get("x"), d2.get("y"), d2.get("z", 0.0)))
        except Exception:
            pass
        return None

    def _extract_path_points(obj):
        seq = (obj.get("path") or obj.get("nodes") or obj.get("node_ids")
               or obj.get("coords") or obj.get("points") or [])
        if not isinstance(seq, (list, tuple)):
            return []
        local_map = obj.get("node_map") or obj.get("coord_map") or obj.get("coords_table")
        if isinstance(local_map, dict):
            def _from_local(idx):
                v = local_map.get(str(idx), None) if str(idx) in local_map else local_map.get(idx, None)
                return _as_tuple3(v)
        elif isinstance(local_map, (list, tuple)):
            def _from_local(idx):
                try: return _as_tuple3(local_map[int(idx)])
                except Exception: return None
        else:
            def _from_local(_): return None

        pts = []
        for v in seq:
            t = _as_tuple3(v) or _nodekey_to_xyz(v) or _from_local(v)
            if t: pts.append(t)
        if pts:
            mx = max(abs(p[0]) for p in pts); my = max(abs(p[1]) for p in pts)
            if mx > 1000 or my > 1000:
                pts = [(p[0]/1000.0, p[1]/1000.0, p[2]/1000.0) for p in pts]
        return pts

    # ---------- index all_rooms by id; keep name/level indices only for diagnostics ----------
    rooms = list(all_rooms or [])
    by_id = { _map_id(getattr(r, "id", "") or ""): r for r in rooms }  # << normalize ids
    by_name, by_name_lv = defaultdict(list), defaultdict(list)
    for r in rooms:
        name = getattr(r, "name", None) or getattr(r, "room_name", None)
        level_name = getattr(getattr(r, "level", None), "name", None) or getattr(r, "level_name", None)
        if name:
            by_name[_norm(name)].append(r)
            if level_name:
                by_name_lv[f"{_norm(level_name)}::{_norm(name)}"].append(r)

    # ---------- collect graph rooms (exclude door_*) ----------
    graph_room_ids, graph_name, graph_level = set(), defaultdict(list), defaultdict(list)
    room_nodes_cache = defaultdict(list)
    for n, d in graph.nodes(data=True):
        rid = d.get("room_id")
        if not rid: 
            continue
        rid = _map_id(str(rid))
        if rid.startswith("door_"): 
            continue
        graph_room_ids.add(rid)
        if d.get("room_name"): graph_name[rid].append(str(d["room_name"]))
        lv = d.get("level_name") or d.get("level") or d.get("Level")
        if lv: graph_level[rid].append(str(lv))
        if isinstance(n, (tuple, list)) and len(n) >= 2:
            room_nodes_cache[rid].append((float(n[0]), float(n[1]), float(n[2] if len(n) > 2 else 0.0)))

    # ---------- group paths by room_id ----------
    path_groups = defaultdict(list)
    for p in (paths or []):
        rid_raw = str(p.get("room_id", "") or "") or str(p.get("roomId", "") or "")
        rid = _map_id(rid_raw)
        if rid and not rid.startswith("door_"):
            path_groups[rid].append(p)

    # ---------- door counts (normalized & tolerant to field names) ----------
    def _door_counts(G):
        out = defaultdict(set)  # use a set so each door contributes once per room
        for _, d in G.nodes(data=True):
            if (d.get("type") or "").lower() != "door":
                continue
            # tolerate multiple possible field names for connected room ids
            cr = (d.get("connected_rooms") or d.get("connected_room_ids")
                  or d.get("rooms") or d.get("room_ids") or [])
            try:
                it = list(cr) if isinstance(cr, (list, tuple, set)) else list(cr or [])
            except Exception:
                it = []
            for rid in it:
                rid = _map_id(rid)
                if rid and not str(rid).startswith("door_"):
                    out[rid].add(id(d))  # unique door identity
        # finalize as counts
        return {k: len(v) for k, v in out.items()}
    door_counts = _door_counts(graph)

    # ---------- classification map ----------
    _class_map = globals().get("classification_results", {}) or {}
    def _class_for_room(room_obj):
        nm = (getattr(room_obj, "name", "") or "").strip().upper()
        return _class_map.get(nm, "Unknown")

    # ---------- strict resolver ----------
    def resolve_room_by_id(gr_id: str):
        return by_id.get(_map_id(gr_id))

    rejected_fallback = 0

    # ---------- iterate union ----------
    iteration_ids = set(graph_room_ids) | set(path_groups.keys())
    results, unresolved = [], 0

    for gr_id in sorted(iteration_ids):
        # 1) strict id
        room = resolve_room_by_id(gr_id)

        # 2) (diagnostic only) if missing, try name/level — but **reject** if id mismatches
        if room is None:
            gname = graph_name.get(gr_id, [None])[0]
            glev  = graph_level.get(gr_id, [None])[0]
            cand = None
            if gname and glev:
                cand_list = by_name_lv.get(f"{_norm(glev)}::{_norm(gname)}", [])
                cand = cand_list[0] if cand_list else None
            elif gname:
                cand_list = by_name.get(_norm(gname), [])
                cand = cand_list[0] if cand_list else None
            # if a fallback candidate exists but its id != gr_id, reject it to keep keys consistent
            if cand is not None and _map_id(getattr(cand, "id", "")).strip() != _map_id(gr_id):
                rejected_fallback += 1
                cand = None
            room = cand  # may stay None

        travel_distances, common_paths = [], []
        for obj in path_groups.get(gr_id, []):
            pts = _extract_path_points(obj)
            if len(pts) >= 2:
                d = sum(_ed(a, b) for a, b in zip(pts[:-1], pts[1:]))
                travel_distances.append(round(d, 2))
                rn = room_nodes_cache.get(gr_id, [])
                if rn:
                    common = max((_ed(pts[0], q) for q in rn), default=0.0)
                    common_paths.append(round(common, 2))

        num_of_exits = door_counts.get(_map_id(gr_id), 0)

        if not travel_distances:
            payload = {
                "room_id": _map_id(gr_id),
                "room": room,  # may be None (and that's OK)
                "path": None,
                "travel_distance": None,
                "common_path": None,
                "is_compliant": False,
            }
            if room is not None:
                payload["fls_parameters"] = fls_parameters(
                    room,
                    classification=_class_for_room(room),
                    max_occupancy_results=max_occupancy_results,
                    all_rooms=all_rooms,
                    all_doors=all_doors,
                    comment="❌ No path to exit",
                    status="Non-Compliant",
                    num_of_exits=num_of_exits,
                )
            else:
                unresolved += 1
            results.append(payload)
            continue

        best = min(travel_distances)
        sprinklers = getattr(room, "sprinklers", False) if room is not None else False
        limit = 75.0 if sprinklers else 60.0
        compliant = best <= limit
        note = f"Best Distance = {best:.2f}m (Limit = {limit}m)"

        payload = {
            "room_id": _map_id(gr_id),
            "room": room,  # may be None
            "travel_distance": travel_distances,
            "common_path": common_paths,
            "is_compliant": compliant,
        }
        if room is not None:
            payload["fls_parameters"] = fls_parameters(
                room,
                classification=_class_for_room(room),
                max_occupancy_results=max_occupancy_results,
                all_rooms=all_rooms,
                all_doors=all_doors,
                comment=note,
                status=("Compliant" if compliant else "Non-Compliant"),
                travel_distance=travel_distances,
                common_path=common_paths,
                sprinklers=sprinklers,
                num_of_exits=num_of_exits,
            )
        else:
            unresolved += 1

        results.append(payload)

    if rejected_fallback:
        print(f"[COMPLIANCE] rejected {rejected_fallback} name/level fallbacks (id mismatch) to keep keys stable.", flush=True)
    if unresolved:
        print(f"[COMPLIANCE] unresolved rooms (no Speckle match): {unresolved}", flush=True)

    if all_floors:
        results.extend(all_floors)

    return results