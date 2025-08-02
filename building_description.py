from typing import Dict, Any
from specklepy.objects import Base
import math
from collections import defaultdict
from code_compliance import building_classification, fls_parameters

# Define hardcoded list of exit door IDs
exit_door_ids = [
    "bdedcc006865738b29e61b11791d4596",
    "3034fec6924391fc291b90be4e36e77f"
]

# Exit width lookup in mm for each door ID
door_width_lookup = {
    "bdedcc006865738b29e61b11791d4596": 2000,
    "3034fec6924391fc291b90be4e36e77f": 900
}

def get_required_exits(occupant_load: int) -> int:
    if occupant_load <= 500:
        return 2
    elif occupant_load <= 1000:
        return 3
    else:
        return 4

def get_building_description(elements_data: Dict[str, list]) -> Dict[str, Any]:
    floors = elements_data.get("Floors", [])
    rooms = elements_data.get("Rooms", [])

    floor_elevations = {}
    floor_areas = defaultdict(float)
    room_functions = defaultdict(list)
    occupancy_per_floor = defaultdict(list)
    total_classification_set = set()

    min_z = math.inf
    max_z = -math.inf
    total_occupant_load = 0

    for room in rooms:
        fls_parameters(room, all_rooms=rooms)  # populates classification and occupancyLoad

        classification = room["buildingClassification"] if "buildingClassification" in room.get_dynamic_member_names() else None
        if classification:
            total_classification_set.add(classification)

    for room in rooms:
        level = getattr(room, "level", None)
        bbox = getattr(room, "bbox", None)
        area = getattr(room, "area", 0.0)
        name = getattr(room, "name", "Unknown")
        classification = room["buildingClassification"] if "buildingClassification" in room.get_dynamic_member_names() else None
        occupant_load = room["occupancyLoad"] if "occupancyLoad" in room.get_dynamic_member_names() else 0
        total_occupant_load += occupant_load if occupant_load else 0

        if bbox and hasattr(bbox, "min") and hasattr(bbox, "max"):
            if bbox.min and bbox.max:
                z_min = bbox.min.z
                z_max = bbox.max.z
                if z_min is not None and z_max is not None:
                    min_z = min(min_z, z_min)
                    max_z = max(max_z, z_max)

        level_name = getattr(level, "name", "Unknown")
        floor_areas[level_name] += area
        room_functions[level_name].append(name)

        if classification:
            occupancy_per_floor[level_name].append(classification)

    for floor in floors:
        level = getattr(floor, "level", None)
        elevation = getattr(level, "elevation", None) if isinstance(level, Base) else None
        if elevation is not None:
            level_name = getattr(level, "name", "Unknown")
            floor_elevations[level_name] = elevation

    num_above_ground = sum(1 for elev in floor_elevations.values() if elev >= 0)
    num_basements = sum(1 for elev in floor_elevations.values() if elev < 0)
    total_basement_depth = abs(min((elev for elev in floor_elevations.values() if elev < 0), default=0))

    # Exit door width calculations
    total_door_width = sum(door_width_lookup.get(door_id, 0) for door_id in exit_door_ids)
    exit_unit_factor = 5.08  # in mm/person for doors
    exit_capacity_people = round(total_door_width / exit_unit_factor)

    return {
        "building_height": round(max_z - min_z, 2) if min_z != math.inf and max_z != -math.inf else 0,
        "total_habitable_height": round(
            sum(
                room.bbox.max.z - room.bbox.min.z
                for room in rooms
                if hasattr(room, "bbox") and hasattr(room.bbox, "min") and hasattr(room.bbox, "max")
            ),
            2
        ),
        "floors_above_ground": num_above_ground,
        "number_of_basements": num_basements,
        "total_basement_depth": round(total_basement_depth, 2),
        "floor_areas": dict(floor_areas),
        "room_functions": dict(room_functions),
        "occupancy_classification": {
            "per_floor": dict(occupancy_per_floor),
            "total": sorted(list(total_classification_set))
        },
        "egress_capacity": {
            "occupant_load": round(total_occupant_load),
            "required_exits": get_required_exits(round(total_occupant_load)),
            "provided_exits": len(exit_door_ids),
            "total_exit_doors_width_mm": total_door_width,
            "exit_unit_factor_mm_per_person": exit_unit_factor,
            "exit_capacity_people": exit_capacity_people
        }
    }
