import os
import pickle
import json
import numpy as np
import sys
import io
# sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)  # Ensure UTF-8 encoding for stdout

from specklepy.api.client import SpeckleClient
from specklepy.api.wrapper import StreamWrapper
from specklepy.transports.server import ServerTransport
from specklepy.objects.base import Base
from specklepy.api import operations
from specklepy.objects.units import get_units_from_string

from extract_elements import extract_elements_by_type
from code_compliance import compute_compliance_check, floor_fls_parameters
from send_utils import send_model_to_speckle_per_floor
from speckle_credentials import SPECKLE_SERVER_URL, PROJECT_ID, MODEL_ID, SPECKLE_TOKEN_FLS


# Patch Speckle Base.units for invalid unit strings
invalid_units_seen = set()

def safe_units_setter(self, value):
    try:
        self.__dict__["units"] = get_units_from_string(value)
    except Exception:
        if value not in invalid_units_seen:
            with open("invalid_units_log.txt", "a", encoding="utf-8") as f:
                f.write(f"{value}\n")
            invalid_units_seen.add(value)
        self.__dict__["units"] = None

def safe_units_getter(self):
    return self.__dict__.get("units", None)

Base.units = property(fget=safe_units_getter, fset=safe_units_setter)

# Authenticate Speckle client
client = SpeckleClient(host=SPECKLE_SERVER_URL)
client.authenticate_with_token(SPECKLE_TOKEN_FLS)

wrapper = StreamWrapper(f"{SPECKLE_SERVER_URL}/streams/{PROJECT_ID}/branches/main")

# Retrieve latest commit
branch = client.branch.get(PROJECT_ID, MODEL_ID)
commits = client.commit.list(PROJECT_ID, MODEL_ID)
default_commit = branch.commits.items[-1] if branch.commits.items else None

transport = ServerTransport(client=client, stream_id=PROJECT_ID)
speckle_data = operations.receive(default_commit.referencedObject, transport)

# Extract building elements
elements_extracted = extract_elements_by_type(speckle_data.elements)

# Load user-selected PDF name from selection
# try:
#     with open("selected_pdf.json", "r", encoding="utf-8") as f:
#         selected = json.load(f)
#         selected_pdf = selected.get("selected_pdf", None)
# except Exception as e:
#     print(f"❌ Failed to load selected PDF: {e}")
#     selected_pdf = None

# if not selected_pdf:
#     print("❌ No PDF selected. Please select a code document from the UI before running this script.")
#     sys.exit(1)


# # Inform the model logic about the selected PDF
# os.environ["SELECTED_CODE_PDF"] = selected_pdf
# print(f"📘 Using selected PDF knowledge base: {selected_pdf}")

# Load user-selected PDF name from environment
selected_pdf = os.getenv("SELECTED_CODE_PDF", None)

if not selected_pdf:
    print("❌ No PDF selected. Please select a code document from the UI before running this script.")
    sys.exit(1)

print(f"📘 Using selected PDF knowledge base: {selected_pdf}", flush=True)


# Utility for JSON-safe values
def safe_json_value(val):
    if isinstance(val, (str, int, float, bool)) or val is None:
        return val
    if isinstance(val, np.generic):
        return val.item()
    if hasattr(val, "id"):
        return val.id
    try:
        return str(val)
    except:
        return None

# Graph & path dirs
graph_dir = "graphs"
path_dir = "paths"

for graph_file in sorted(os.listdir(graph_dir)):
    if not graph_file.endswith(".pkl"):
        continue

    level_name = graph_file.split("_")[-1].replace(".pkl", "")
    print(f"\n📘 Running FLS Check for Floor: {level_name}", flush=True)

    try:
        with open(os.path.join(graph_dir, graph_file), "rb") as f:
            G_floor = pickle.load(f)
    except Exception as e:
        print(f"❌ Failed to load graph: {e}", flush=True)
        continue

    try:
        with open(os.path.join(path_dir, f"paths_{level_name}.pkl"), "rb") as f:
            paths = pickle.load(f)
    except Exception as e:
        print(f"❌ Failed to load paths: {e}", flush=True)
        continue

    matched_floor = next(
        (f for f in elements_extracted["Floors"]
         if getattr(getattr(f, "level", None), "name", None) == level_name),
        None
    )
    rooms_on_level = [
        r for r in elements_extracted["Rooms"]
        if getattr(getattr(r, "level", None), "name", None) == level_name
    ]

    # Batch Classify + OLF once per floor
    from code_compliance import (
        run_llm_classify_batch, run_llm_olf_batch, run_llm_max_occupancy_batch,
        classification_results, olf_results, max_occupancy_results
    )
    classification_results.clear()
    olf_results.clear()

    room_names = [getattr(r, "name", "").strip() for r in rooms_on_level if getattr(r, "name", None)]

    # Run LLM batch classifiers
    classification_results.update(run_llm_classify_batch(room_names))
    olf_results.update(run_llm_olf_batch(room_names))
    classifications = list(set(classification_results.values()))
    max_occupancy_results.update(run_llm_max_occupancy_batch(classifications))


    # Patch buildingClassification directly into room objects
    for room in rooms_on_level:
        # name = getattr(room, "name", "").strip().upper()
        name = getattr(room, "name", "").strip()
        classification = classification_results.get(name)
        if classification:
            room["buildingClassification"] = classification
        olf = olf_results.get(name)
        if olf:
            room["occupancyLoadFactor"] = olf
        room_entry = max_occupancy_results.get(name)
        if isinstance(room_entry, dict):
            room["maximumOccupantLoad"] = room_entry.get("max_occupancy")

    # FLS metadata on floor
    fls_parameters = []
    if matched_floor:
        floor_fls = floor_fls_parameters(matched_floor, rooms_on_level, level_name=level_name)
        fls_parameters.append(floor_fls)

    # 🔥 Room-level FLS compliance
    compliance_results = compute_compliance_check(
        paths,
        graph=G_floor,
        all_rooms=rooms_on_level,
        all_floors=[matched_floor] if matched_floor else None, 
        max_occupancy_results=max_occupancy_results
    )

    # Collect enriched objects
    fls_parameters += [
        res["fls_parameters"] if isinstance(res, dict) else res
        for res in compliance_results
        if (isinstance(res, dict) and "fls_parameters" in res) or isinstance(res, Base)
    ]

    # Save JSON compliance report
    os.makedirs("compliance_reports", exist_ok=True)
    report_path = f"compliance_reports/compliance_report_{level_name}.json"
    report_data = []

    for res in compliance_results:
        if not (isinstance(res, dict) and "room" in res):
            continue
        room = res["room"]
        report_data.append({
            "room_id": safe_json_value(getattr(room, "id", None)),
            "room_name": safe_json_value(getattr(room, "name", None)),
            "travelDistance": safe_json_value(res.get("travel_distance")),
            "commonPath": safe_json_value(res.get("common_path")),
            "isCompliant": safe_json_value(res.get("is_compliant")),
            "fireSafetyNote": safe_json_value(getattr(room, "fireSafetyNote", None)),
            "complianceStatus": safe_json_value(getattr(room, "complianceStatus", None)),
            "buildingClassification": safe_json_value(getattr(room, "buildingClassification", None)),
            "occupancyLoadFactor": safe_json_value(getattr(room, "occupancyLoadFactor", None)),
            "occupancyLoad": safe_json_value(getattr(room, "occupancyLoad", None)),
            "maximumOccupantLoad": safe_json_value(getattr(room, "maximumOccupantLoad", None)),
            "area": safe_json_value(getattr(room, "area", None)),
        })

    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2)
    print(f"📝 Compliance report saved: {report_path}", flush=True)

    # 🚀 Push enriched objects to Speckle
    print("\n🚀 Sending the following rooms to Speckle:", flush=True)
    for obj in fls_parameters:
        name = getattr(obj, "name", "?")

        try:
            classification = obj["buildingClassification"]
        except:
            classification = "❌"
        print(f"🧱 {name} | buildingClassification = {classification}", flush=True)


    if fls_parameters:
        send_model_to_speckle_per_floor(
            fls_parameters,
            client,
            PROJECT_ID,
            level_name=level_name,
            message_prefix="Fire Safety Compliance – Check"
        )

print("\n✅ FLS Parameters + Compliance Review Complete.", flush=True)
