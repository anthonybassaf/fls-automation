import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

log_file = open("wall_correction_log.txt", "w", encoding="utf-8")
sys.stdout = log_file

from specklepy.api.client import SpeckleClient
from specklepy.objects.base import Base
from specklepy.transports.server import ServerTransport
from specklepy.api import operations
import numpy as np
from speckle_credentials import (
    SPECKLE_SERVER_URL,
    PROJECT_ID,
    VERSION_ID,
    BRANCH_NAME, 
    SPECKLE_TOKEN_CORRECTION
)
from specklepy.objects.units import get_units_from_string

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

original_units = Base.__dict__.get("units")

Base.units = property(
    fget=original_units.fget if original_units else None,
    fset=safe_units_setter,
    fdel=original_units.fdel if original_units else None
)


# === 1. Set up client ===
client = SpeckleClient(host=SPECKLE_SERVER_URL)
client.authenticate_with_token(SPECKLE_TOKEN_CORRECTION)

# === 2. Load stream and commit ===
stream_id = PROJECT_ID
commit_id = VERSION_ID
branch_name = BRANCH_NAME  # or create 'enriched'
enrichment_branch_name = "enriched walls"

# Step 1: Receive base object
commit = client.commit.get(stream_id, commit_id)
print(f"[DEBUG] Commit: {commit}")
transport = ServerTransport(client=client, stream_id=stream_id)
base = operations.receive(commit.referencedObject, transport)

# Step 2: Extract walls and rooms
def receive_and_extract_walls_recursive(obj, transport, path="base"):
    wall_instances = []
    def extract_walls(obj, path="base"):
        results = []
        stack = [(obj, path)]
        while stack:
            current, current_path = stack.pop()
            if isinstance(current, Base):
                speckle_type = getattr(current, "speckle_type", "")
                if "RevitWall" in speckle_type or speckle_type.endswith("BuiltElements.Wall"):
                    results.append(current)
                for key in current.get_dynamic_member_names():
                    value = getattr(current, key, None)
                    if isinstance(value, (Base, list)):
                        stack.append((value, f"{current_path}.{key}"))
            elif isinstance(current, list):
                for i, item in enumerate(current):
                    stack.append((item, f"{current_path}[{i}]"))
        return results

    if isinstance(obj, Base):
        speckle_type = getattr(obj, "speckle_type", "")
        if speckle_type == "Speckle.Core.Models.Collection":
            elements = getattr(obj, "elements", [])
            for i, el in enumerate(elements):
                if isinstance(el, dict) and "referencedId" in el:
                    ref_id = el["referencedId"]
                    child = operations.receive(ref_id, transport)
                    wall_instances += receive_and_extract_walls_recursive(child, transport, path=f"{path}.elements[{i}]")
                elif isinstance(el, Base):
                    wall_instances += receive_and_extract_walls_recursive(el, transport, path=f"{path}.elements[{i}]")
        else:
            wall_instances += extract_walls(obj, path=path)

    return wall_instances

def receive_and_extract_rooms_recursive(obj, transport, path="base"):
    room_instances = []
    def extract_rooms(obj, path="base"):
        results = []
        stack = [(obj, path)]
        while stack:
            current, current_path = stack.pop()
            if isinstance(current, Base):
                speckle_type = getattr(current, "speckle_type", "")
                if "RevitRoom" in speckle_type or speckle_type.endswith("BuiltElements.Room"):
                    results.append(current)
                for key in current.get_dynamic_member_names():
                    value = getattr(current, key, None)
                    if isinstance(value, (Base, list)):
                        stack.append((value, f"{current_path}.{key}"))
            elif isinstance(current, list):
                for i, item in enumerate(current):
                    stack.append((item, f"{current_path}[{i}]"))
        return results

    if isinstance(obj, Base):
        speckle_type = getattr(obj, "speckle_type", "")
        if speckle_type == "Speckle.Core.Models.Collection":
            elements = getattr(obj, "elements", [])
            for i, el in enumerate(elements):
                if isinstance(el, dict) and "referencedId" in el:
                    ref_id = el["referencedId"]
                    child = operations.receive(ref_id, transport)
                    room_instances += receive_and_extract_rooms_recursive(child, transport, path=f"{path}.elements[{i}]")
                elif isinstance(el, Base):
                    room_instances += receive_and_extract_rooms_recursive(el, transport, path=f"{path}.elements[{i}]")
        else:
            room_instances += extract_rooms(obj, path=path)

    return room_instances

# Step 3: Bounding box utilities
def compute_bbox_from_vertices(vertices):
    if not vertices: return None
    minX = min(v[0] for v in vertices)
    maxX = max(v[0] for v in vertices)
    minY = min(v[1] for v in vertices)
    maxY = max(v[1] for v in vertices)
    minZ = min(v[2] for v in vertices)
    maxZ = max(v[2] for v in vertices)
    return {
        "minX": minX, "maxX": maxX,
        "minY": minY, "maxY": maxY,
        "minZ": minZ, "maxZ": maxZ
    }

def compute_bbox_from_display_value(obj):
    meshes = getattr(obj, "displayValue", None)
    if not meshes or not isinstance(meshes, list): return None
    all_vertices = []
    for mesh in meshes:
        vertices = getattr(mesh, "vertices", None)
        if vertices and isinstance(vertices, list):
            all_vertices.extend(zip(vertices[0::3], vertices[1::3], vertices[2::3]))
    return compute_bbox_from_vertices(all_vertices)

def compute_room_bbox(room):
    bbox = getattr(room, "bbox", None)
    if bbox:
        return {
            "minX": float(bbox.get("minX", 0)),
            "maxX": float(bbox.get("maxX", 0)),
            "minY": float(bbox.get("minY", 0)),
            "maxY": float(bbox.get("maxY", 0)),
            "minZ": float(bbox.get("minZ", 0)),
            "maxZ": float(bbox.get("maxZ", 0)),
        }
    return compute_bbox_from_display_value(room)


def compute_wall_bbox(start, end, margin=50, wall=None):
    min_x = min(start.x, end.x) - margin
    max_x = max(start.x, end.x) + margin
    min_y = min(start.y, end.y) - margin
    max_y = max(start.y, end.y) + margin

    base_z = min(start.z, end.z)
    top_z = max(start.z, end.z)

    if wall:
        raw_height = getattr(wall, "height", None)
        try:
            height = float(raw_height)
            if height > 0.01:  # sanity check
                top_z = base_z + height
        except Exception as e:
            print(f"[DEBUG] Could not extract wall height for {getattr(wall, 'id', '?')}: {raw_height} → {e}")

    return {
        "minX": min_x,
        "maxX": max_x,
        "minY": min_y,
        "maxY": max_y,
        "minZ": base_z,
        "maxZ": top_z
    }





def bbox_intersects(bbox1, bbox2, check_z=True):
    xy_overlap = (
        bbox1["minX"] <= bbox2["maxX"] and bbox1["maxX"] >= bbox2["minX"] and
        bbox1["minY"] <= bbox2["maxY"] and bbox1["maxY"] >= bbox2["minY"]
    )
    if not check_z:
        return xy_overlap
    return xy_overlap and (
        bbox1["minZ"] <= bbox2["maxZ"] and bbox1["maxZ"] >= bbox2["minZ"]
    )

def parse_transform_matrix(raw):
    if hasattr(raw, "value"): raw = raw.value
    if not isinstance(raw, list) or len(raw) != 16:
        return np.identity(4)
    return np.array(raw).reshape((4, 4))

def apply_transform(vertices, matrix):
    transformed = []
    for i in range(0, len(vertices), 3):
        vec = np.array([vertices[i], vertices[i+1], vertices[i+2], 1])
        tx, ty, tz, _ = np.dot(matrix, vec)
        transformed.append((tx, ty, tz))
    return transformed

# Step 4: Wall + Room analysis
# def log_wall_intersections(base, transport, margin=50):
#     walls = receive_and_extract_walls_recursive(base, transport)
#     rooms = receive_and_extract_rooms_recursive(base, transport)

#     room_bboxes = []
#     for room in rooms:
#         bbox = compute_room_bbox(room)
#         print(f"Room ID: {getattr(room, 'id', 'Unknown')}, BBox: {bbox}")
#         if bbox:
#             room_bboxes.append((room, bbox))

#     print(f"\n🧱 Total walls: {len(walls)} | 🏠 Total rooms: {len(room_bboxes)}")

#     modified_walls = []
#     unmatched_walls = []

#     for wall in walls:
#         wall_id = getattr(wall, "id", "Unknown")
#         baseline = getattr(wall, "baseLine", None)
#         start = getattr(baseline, "start", None) if baseline else None
#         end = getattr(baseline, "end", None) if baseline else None

#         print(f"\n🔍 Wall {wall_id}:")

#         if not (start and end):
#             print("⚠️ Missing start or end point.")
#             continue

#         wall_bbox = compute_wall_bbox(start, end, margin, wall=wall)

#         wall_min_z = wall_bbox["minZ"]
#         wall_max_z = wall_bbox["maxZ"]
#         wall_z_center = (wall_min_z + wall_max_z) / 2

#         print(f"📦 Wall bbox (expanded): {wall_bbox} | Wall Z (center): {wall_z_center:.2f}")

#         closest_room = None
#         max_z_overlap = -1  # use overlap depth instead of distance

#         for room, room_bbox in room_bboxes:
#             if bbox_intersects(wall_bbox, room_bbox, check_z=False):
#                 room_id = getattr(room, "id", "Unknown")
#                 room_level = getattr(room, "level", None)
#                 wall_level = getattr(wall, "level", None)

#                 room_level_id = getattr(room_level, "name", None)
#                 wall_level_id = getattr(wall_level, "name", None)

#                 room_min_z = room_bbox["minZ"]
#                 room_max_z = room_bbox["maxZ"]

#                 # Compute Z overlap
#                 z_overlap = min(room_max_z, wall_max_z) - max(room_min_z, wall_min_z)

#                 if z_overlap <= 0:
#                     print(f"🔎 XY match but failed Z overlap: Room ID {room_id}, Wall Z = {wall_min_z:.2f}→{wall_max_z:.2f}, Room Z = {room_min_z:.2f}→{room_max_z:.2f}")
#                     continue

#                 print(f"✅ Wall intersects with Room ID: {room_id}")
#                 print(f"   - Z Overlap: {z_overlap:.2f}m | Room level: {room_level_id}, Wall level: {wall_level_id}")

#                 if z_overlap > max_z_overlap:
#                     max_z_overlap = z_overlap
#                     closest_room = room

#         if closest_room:
#             new_level = getattr(closest_room, "level", None)
#             if new_level:
#                 new_level_id = getattr(new_level, "name", None)
#                 current_level = getattr(wall, "level", None)
#                 current_level_id = getattr(current_level, "name", None) if current_level else None

#                 if new_level_id != current_level_id:
#                     print(f"🔁 Updating wall level from {current_level_id} to {new_level_id}")
#                     wall["level"] = new_level
#                     modified_walls.append(wall)
#                 else:
#                     print("✅ Wall already on correct level.")
#             else:
#                 print("⚠️ Closest room has no level assigned.")
#         else:
#             print("❌ No suitable room intersection with Z overlap.")
#             print(f"❌ Unmatched Wall → ID: {wall_id}")
#             print(f"   ↳ Z Center: {wall_z_center:.2f}")
#             print(f"   ↳ Bounding Box:")
#             print(f"     - X: {wall_bbox['minX']:.2f} → {wall_bbox['maxX']:.2f}")
#             print(f"     - Y: {wall_bbox['minY']:.2f} → {wall_bbox['maxY']:.2f}")
#             print(f"     - Z: {wall_bbox['minZ']:.2f} → {wall_bbox['maxZ']:.2f}")
#             unmatched_walls.append({
#                 "id": wall_id,
#                 "bbox": wall_bbox,
#                 "z_center": wall_z_center
#             })

#     return modified_walls

def log_wall_intersections(base, transport, margin=50):
    walls = receive_and_extract_walls_recursive(base, transport)
    rooms = receive_and_extract_rooms_recursive(base, transport)

    room_bboxes = []
    for room in rooms:
        bbox = compute_room_bbox(room)
        print(f"Room ID: {getattr(room, 'id', 'Unknown')}, BBox: {bbox}")
        if bbox:
            room_bboxes.append((room, bbox))

    print(f"\n🧱 Total walls: {len(walls)} | 🏠 Total rooms: {len(room_bboxes)}")

    modified_walls = []
    unmatched_walls = []

    for wall in walls:
        wall_id = getattr(wall, "id", "Unknown")
        baseline = getattr(wall, "baseLine", None)
        start = getattr(baseline, "start", None) if baseline else None
        end = getattr(baseline, "end", None) if baseline else None

        print(f"\n🔍 Wall {wall_id}:")

        if not (start and end):
            print("⚠️ Missing start or end point.")
            continue

        wall_bbox = compute_wall_bbox(start, end, margin, wall=wall)
        wall_min_z = wall_bbox["minZ"]
        wall_max_z = wall_bbox["maxZ"]
        wall_base_z = min(start.z, end.z)
        wall_z_center = (wall_min_z + wall_max_z) / 2

        print(f"📦 Wall bbox: {wall_bbox} | Z Center: {wall_z_center:.2f} | Base Z: {wall_base_z:.2f}")

        # 1️⃣ PRIMARY METHOD — XY baseline intersection
        baseline_box = {
            "minX": min(start.x, end.x),
            "maxX": max(start.x, end.x),
            "minY": min(start.y, end.y),
            "maxY": max(start.y, end.y),
        }

        baseline_intersections = []

        for room, room_bbox in room_bboxes:
            try:
                xy_overlap = (
                    baseline_box["minX"] <= room_bbox["maxX"] and
                    baseline_box["maxX"] >= room_bbox["minX"] and
                    baseline_box["minY"] <= room_bbox["maxY"] and
                    baseline_box["maxY"] >= room_bbox["minY"]
                )
            except Exception as e:
                print(f"⚠️ Room bbox malformed for room ID {getattr(room, 'id', 'Unknown')}: {e}")
                continue

            if xy_overlap:
                room_min_z = room_bbox["minZ"]
                room_max_z = room_bbox["maxZ"]
                room_level = getattr(room, "level", None)
                room_level_id = getattr(room_level, "name", None)

                if room_min_z <= wall_base_z <= room_max_z:
                    z_dist = 0
                else:
                    z_dist = min(abs(wall_base_z - room_min_z), abs(wall_base_z - room_max_z))

                print(f"✅ Baseline intersects Room ID: {getattr(room, 'id', 'Unknown')} | Level: {room_level_id} | Z Dist: {z_dist:.2f}")
                baseline_intersections.append((room, z_dist))
            else:
                print(f"🔎 No XY baseline overlap: Room ID {getattr(room, 'id', 'Unknown')}")


        if baseline_intersections:
            # Pick room with closest Z distance
            baseline_intersections.sort(key=lambda x: x[1])
            chosen_room = baseline_intersections[0][0]
            print("🏷️ Assigned via XY Baseline Intersection")
        else:
            # 2️⃣ FALLBACK — Z Overlap logic
            chosen_room = None
            max_z_overlap = -1
            for room, room_bbox in room_bboxes:
                if bbox_intersects(wall_bbox, room_bbox, check_z=False):
                    room_min_z = room_bbox["minZ"]
                    room_max_z = room_bbox["maxZ"]
                    z_overlap = min(room_max_z, wall_max_z) - max(room_min_z, wall_min_z)

                    if z_overlap > 0:
                        print(f"✅ Fallback Z-Overlap match: Room ID {getattr(room, 'id', 'Unknown')} | Overlap: {z_overlap:.2f}")
                        if z_overlap > max_z_overlap:
                            max_z_overlap = z_overlap
                            chosen_room = room

            if chosen_room:
                print("🏷️ Assigned via Z Overlap Fallback")

        if chosen_room:
            new_level = getattr(chosen_room, "level", None)
            if new_level:
                new_level_id = getattr(new_level, "name", None)
                current_level = getattr(wall, "level", None)
                current_level_id = getattr(current_level, "name", None) if current_level else None

                if new_level_id != current_level_id:
                    print(f"🔁 Updating wall level from {current_level_id} to {new_level_id}")
                    wall["level"] = new_level
                    modified_walls.append(wall)
                else:
                    print("✅ Wall already on correct level.")
            else:
                print("⚠️ Chosen room has no level assigned.")
        else:
            print("❌ No suitable room found.")
            unmatched_walls.append({
                "id": wall_id,
                "bbox": wall_bbox,
                "z_center": wall_z_center
            })

    return modified_walls











# 👉 Call the analysis and patching function
modified_walls = log_wall_intersections(base, transport, margin=1000)

# ✅ Final commit: send the full model with updated wall levels
if modified_walls:
    new_commit_id = operations.send(base, transport)
    new_branch_name = "walls-level-corrected"

    client.branch.create(stream_id, new_branch_name)
    client.commit.create(
        stream_id=stream_id,
        branch_name=new_branch_name,
        object_id=new_commit_id,
        message="🔧 Full model with wall levels corrected"
    )
    print(f"✅ Full model committed to branch '{new_branch_name}'")
else:
    print("✅ No wall levels needed correction — model not re-sent.")

log_file.close()