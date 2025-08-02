import os
import json
import pickle
import math
import subprocess
from collections import defaultdict
from specklepy.objects import Base
from path_of_travel import euclidean_distance

# 🧠 LLM subprocess call utility
VENV_PYTHON = os.path.join("langchain_venv", "Scripts" if os.name == "nt" else "bin", "python")

def run_llm_classify_batch(room_names: list[str]) -> dict:
    import json
    import time
    from more_itertools import chunked

    results = {}

    for batch in chunked(room_names, 10):
        try:
            env = os.environ.copy()
            result = subprocess.run(
                [VENV_PYTHON, "llm_classify.py", json.dumps(batch)],
                capture_output=True,
                text=True,
                check=True, 
                env=env
            )

            # ➕ Extract JSON from mixed stdout
            lines = result.stdout.strip().splitlines()
            json_part = None
            for line in reversed(lines):
                try:
                    json_part = json.loads(line)
                    break  # Stop at the first valid JSON
                except json.JSONDecodeError:
                    continue

            if json_part:
                results.update(json_part)
            else:
                print(f"[ERROR] No valid JSON found in output:\n{result.stdout}", flush=True)

        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Subprocess failed on batch {batch}: {e}", flush=True)
            print("STDERR:\n", e.stderr, flush=True)
        except Exception as e:
            print(f"[ERROR] Unknown error on batch {batch}: {e}", flush=True)

        time.sleep(0.5)

    return results


# def run_llm_olf_batch(room_names: list[str]) -> dict:
#     import sys
#     import time
#     import json
#     from more_itertools import chunked
#     import subprocess

#     results = {}

#     for batch in chunked(room_names, 5):  # smaller batch for better debugging
#         try:
#             env = os.environ.copy()
#             result = subprocess.run(
#                 [sys.executable, "llm_olf.py", *batch],
#                 capture_output=True,
#                 text=True, env=env
#             )

#             if result.returncode != 0:
#                 print(f"[ERROR] Subprocess failed on batch: {batch}", flush=True)
#                 print("[ERROR] STDOUT:\n", result.stdout, flush=True)
#                 print("[ERROR] STDERR:\n", result.stderr, flush=True)
#                 continue  # skip this batch

#             # Try to parse from last valid JSON line
#             lines = result.stdout.strip().splitlines()
#             print(f"[DEBUG] Raw output for batch {batch}:\n{lines}", flush=True)
#             json_part = None
#             for line in reversed(lines):
#                 try:
#                     print(f"[DEBUG] Trying to load line: {line}", flush=True)
#                     json_part = json.loads(line)
#                     break
#                 except json.JSONDecodeError:
#                     continue

#             if json_part:
#                 results.update(json_part)
#             else:
#                 print(f"[WARNING] No valid JSON found in output for batch: {batch}\nRaw output:\n{result.stdout}")

#         except Exception as e:
#             print(f"[ERROR] Unknown error on batch {batch}: {e}", flush=True)

#         time.sleep(0.5)

#     return results

def run_llm_olf_batch(room_names: list[str]) -> dict:
    import sys
    import time
    import json
    from more_itertools import chunked
    import subprocess
    import os

    results = {}

    # Absolute path to the Python interpreter in langchain_venv
    venv_python = r"C:\Users\abassaf\scripting\python\fire_safety\langchain_venv\Scripts\python.exe"

    # Determine working directory (same folder as this file)
    cwd = os.path.dirname(os.path.abspath(__file__))

    # Path to vector_db folder
    faiss_index_dir = os.path.join(cwd, "vector_db")

    for batch in chunked(room_names, 5):  # smaller batch for better debugging
        try:
            env = os.environ.copy()
            # Ensure FAISS index dir is available to subprocess
            env["FAISS_INDEX_DIR"] = faiss_index_dir

            # Always call llm_olf.py with langchain_venv interpreter
            cmd = [venv_python, "llm_olf.py", *batch]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                cwd=cwd  # Important: so vector_db resolves correctly
            )

            if result.returncode != 0:
                print(f"[ERROR] Subprocess failed on batch: {batch}", flush=True)
                print("[ERROR] STDOUT:\n", result.stdout, flush=True)
                print("[ERROR] STDERR:\n", result.stderr, flush=True)
                continue  # skip this batch

            # Try to parse from last valid JSON line
            lines = result.stdout.strip().splitlines()
            print(f"[DEBUG] Raw output for batch {batch}:\n{lines}", flush=True)
            json_part = None
            for line in reversed(lines):
                try:
                    print(f"[DEBUG] Trying to load line: {line}", flush=True)
                    json_part = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

            if json_part:
                results.update(json_part)
            else:
                print(f"[WARNING] No valid JSON found in output for batch: {batch}\nRaw output:\n{result.stdout}")

        except Exception as e:
            print(f"[ERROR] Unknown error on batch {batch}: {e}", flush=True)

        time.sleep(0.5)

    return results





def run_llm_max_occupancy_batch(classifications: list[str]) -> dict:
    import json
    try:
        env = os.environ.copy()
        result = subprocess.run(    
            [VENV_PYTHON, "llm_max_occupancy.py", json.dumps(classifications)],
            capture_output=True,
            text=True,
            check=True,
            env=env
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Subprocess error:\n{e.stderr}", flush=True)
        return {}
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON decode error:\nRaw stdout: {result.stdout}", flush=True)
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


# def occupant_load_factor(room_name: str, classification: str = "") -> float:
#     global olf_results
#     if room_name in olf_results:
#         return float(olf_results[room_name]) if olf_results[room_name] is not None else None

#     print(f"❌ OLF not preloaded for room: {room_name}")
#     return None

def occupant_load_factor(room_name: str, classification: str = "") -> float | None:
    if room_name in olf_results:
        val = olf_results[room_name]
        if isinstance(val, dict) and "olf" in val:
            return float(val["olf"])
        elif isinstance(val, (int, float)):
            return float(val)
    return None


# 🔴 Helper: RGB color converter
def hex_to_rgb(hex_color: str) -> list:
    hex_color = hex_color.lstrip("#")
    return [round(int(hex_color[i:i+2], 16) / 255.0, 4) for i in (0, 2, 4)]

# 🎨 Visual styling for rooms
def color_code_room(room: Base, color: str = "#FF0000") -> Base:
    rgb = hex_to_rgb(color)
    room["renderMaterial"] = {
        "diffuse": rgb, "opacity": 1.0, "metalness": 0.0,
        "roughness": 0.5, "useVertexColors": False, "useCustomMaterial": True
    }
    room["displayStyle"] = {"color": color, "opacity": 1.0}
    return room

# 🛣️ Compute max path from room outline to door
def check_common_path_compliance(room: Base, door_points: list, max_distance: float = 23.0) -> dict:
    if not hasattr(room, "outline") or not room.outline or not door_points:
        return {
            "room": room,
            "common_path": None,
            "is_compliant": False,
            "color_code": color_code_room(room, "#FF0000"),
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
    Assign all Fire and Life Safety (FLS) parameters to a Room, including compliance checks.
    """

    # Classification
    room_classification = classification or room.get("buildingClassification", "Unknown")
    room["buildingClassification"] = room_classification


    # Room Area
    area_raw = getattr(room, "area", None)
    area = float(area_raw) if area_raw else 0.0

    # Occupant Load Factor and Load
    name = getattr(room, "name", "")
    olf = occupant_load_factor(name, classification)
    occupancy_load = math.ceil(area / olf) if area and olf else None

    # Number of Exits
    room["numOfExits"] = num_of_exits

    # Maximum Occupancy allowed (from GPT/cache)
    room_name = name.strip()
    maximum_occupant_load = None
    
    room_entry = max_occupancy_results.get(room_name)
    if room_entry is None:
        print(f"[DEBUG] ❌ Room '{room_name}' not found in max_occupancy_results")
    elif isinstance(room_entry, dict):
        maximum_occupant_load = room_entry.get("max_occupancy")

    # === DEBUGGING ===
    print(f"[DEBUG] Room name raw: {name} | Lookup key: {room_name}")
    print(f"[DEBUG] Classification: {room_classification}")
    print(f"[DEBUG] Max Occupant Load = {maximum_occupant_load}")

    room["maximumOccupantLoad"] = maximum_occupant_load

    # Exit Compliance Check
    exit_compliant = True
    if maximum_occupant_load and occupancy_load:
        if occupancy_load > maximum_occupant_load and num_of_exits < 2:
            exit_compliant = False

    # Travel Distance Compliance Check
    max_distance = 75.0 if sprinklers else 60.0
    if isinstance(travel_distance, list):
        travel_compliant = any(d <= max_distance for d in travel_distance)
    else:
        travel_compliant = travel_distance <= max_distance if travel_distance is not None else False


    # Overall compliance
    overall_compliant = travel_compliant and exit_compliant

    if not overall_compliant:
        room = color_code_room(room, color="#FF0000")
        status = "Non-Compliant"
        if not exit_compliant:
            comment = "❌ Exceeds occupancy or lacks exits."
        elif not travel_compliant:
            comment = "❌ Travel distance exceeded."

    # Set FLS metadata
    room["applicationId"] = getattr(room, "id", "")
    room["fireSafetyNote"] = comment
    room["complianceStatus"] = status
    room["travelDistance"] = travel_distance
    room["commonPath"] = common_path
    room["occupancyLoadFactor"] = olf
    room["occupancyLoad"] = occupancy_load
    room["sprinklers"] = sprinklers

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

def compute_compliance_check(paths, graph=None, all_rooms=None, all_floors=None, all_doors=None, max_occupancy_results=None):
    results = []
    if not paths or not graph:
        raise ValueError("Both paths and graph are required.")

    from collections import defaultdict
    room_lookup = {str(getattr(room, "id", "")): room for room in all_rooms or []}
    path_groups = defaultdict(list)
    for p in paths:
        path_groups[p["room_id"]].append(p)

    # Count how many door nodes per room
    def count_doors_per_room(graph):
        room_door_counts = {}
        for node, data in graph.nodes(data=True):
            if data.get("type") == "door":
                for room_id in data.get("connected_rooms", []):
                    room_door_counts[room_id] = room_door_counts.get(room_id, 0) + 1
        return room_door_counts

    door_counts = count_doors_per_room(graph)

    for room_id, path_objs in path_groups.items():
        room = room_lookup.get(room_id)
        if not room:
            print(f"⚠️ Room {room_id} not found in room list.")
            continue

        travel_distances = []
        common_paths = []
        num_of_exits = door_counts.get(room_id, 0)

        for path_obj in path_objs:
            path_nodes = path_obj.get("path")
            if not path_nodes:
                continue

            dist = sum(euclidean_distance(p1, p2) for p1, p2 in zip(path_nodes[:-1], path_nodes[1:]))
            room_nodes = [n for n, data in graph.nodes(data=True) if data.get("room_id") == room_id]
            common = max(euclidean_distance(path_nodes[0], pt) for pt in room_nodes) if room_nodes else 0

            travel_distances.append(round(dist, 2))
            common_paths.append(round(common, 2))

        if not travel_distances:
            results.append({
                "room": room,
                "path": None,
                "travel_distance": None,
                "is_compliant": False,
                "fls_parameters": fls_parameters(
                    room,
                    all_rooms=all_rooms,
                    all_doors=all_doors,
                    comment="❌ No path to exit",
                    status="Non-Compliant",
                    num_of_exits=num_of_exits
                )
            })
            continue

        # Pick shortest distance for compliance
        best_total = min(travel_distances)
        sprinklers = getattr(room, "sprinklers", False)
        max_distance = 75.0 if sprinklers else 60.0
        compliant = best_total <= max_distance

        status = "Compliant" if compliant else "Non-Compliant"
        note = f"Best Distance = {best_total:.2f}m (Limit = {max_distance}m)"
        if not compliant:
            room = color_code_room(room, color="#FF0000")

        results.append({
            "room": room,
            "travel_distance": travel_distances,
            "common_path": common_paths,
            "is_compliant": compliant,
            "color_code": room,
            "fls_parameters": fls_parameters(
                room,
                classification=classification_results.get(getattr(room, "name", "").strip().upper(), "Unknown"),
                max_occupancy_results=max_occupancy_results,
                all_rooms=all_rooms,
                all_doors=all_doors,
                comment=note,
                status=status,
                travel_distance=travel_distances,
                common_path=common_paths,
                sprinklers=sprinklers,
                num_of_exits=num_of_exits
            )
        })

    if all_floors:
        results.extend(all_floors)

    return results


