import os
import pickle
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')  # Ensure UTF-8 encoding for stdout
from specklepy.api.client import SpeckleClient
from specklepy.api.wrapper import StreamWrapper
from specklepy.api import operations
from specklepy.transports.server import ServerTransport


from speckle_credentials import (
    SPECKLE_SERVER_URL,
    PROJECT_ID,
    MODEL_ID,
    SPECKLE_TOKEN_STG
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
    add_stairs_on_grid
)
from send_utils import graph_to_speckle_objects, send_graph_to_speckle_per_floor
# 🛠️ Patch Speckle units to ignore invalid unit strings like "฿"
from specklepy.objects.base import Base
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

# Construct wrapper
project_url = f"{SPECKLE_SERVER_URL}/streams/{PROJECT_ID}/branches/main"
wrapper = StreamWrapper(project_url)

# Initialize Speckle client
client = SpeckleClient(host=SPECKLE_SERVER_URL)
client.authenticate_with_token(SPECKLE_TOKEN_STG)

print("Speckle Client Authenticated.")

# Fetch Stream details
stream = client.stream.get(PROJECT_ID)

if stream:
    print(f"Stream Name: {stream.name}")
    print(f"Stream ID: {stream.id}")


branch = client.branch.get(PROJECT_ID, MODEL_ID)
print(f"Branch Name: {branch.name}")
print(f"Branch ID: {branch.id}")


# Fetch Latest Commit
commits = client.commit.list(PROJECT_ID, MODEL_ID)
default_commit = branch.commits.items[-1] if branch.commits.items else None


# Retrieve Data from Speckle
transport = ServerTransport(client=client, stream_id=PROJECT_ID)
speckle_data = operations.receive(default_commit.referencedObject, transport)

# Run extraction
elements_extracted = extract_elements_by_type(speckle_data.elements, save_to_path=True)
rooms = elements_extracted["Rooms"]

global_bounds = compute_global_bounds(
    elements_extracted["Rooms"],
    elements_extracted["Walls"],
    elements_extracted["Doors"]
)

# Print extraction summary
print(f"\n✅ Extraction Summary:")
for key, value in elements_extracted.items():
    print(f"  {key}: {len(value)} elements")

# Define level aliases to unify floors across ramps or offsets
level_alias_map = {
    "G00": "001",  # Unify G00 and 001 into logical floor "001"
    "B00": "001",  # Unify B00 and 001 into logical floor "001"
    "001": "001",
    "003": "003",
    "004": "004",
    # Add more aliases if needed
}

room_floors = group_rooms_by_level(elements_extracted["Rooms"], level_alias_map)
wall_floors = group_walls_by_level(elements_extracted["Walls"], level_alias_map)
door_floors = group_doors_by_level(elements_extracted["Doors"], level_alias_map)
stair_floors = group_stairs_by_level(elements_extracted["Stairs"], level_alias_map)

for level_name, rooms_on_level in room_floors.items():
    print(f"\n🔄 Processing Floor: {level_name} ({len(rooms_on_level)} rooms)")

    walls_on_level = wall_floors.get(level_name, [])
    doors_on_level = door_floors.get(level_name, [])
    stairs_on_level = stair_floors.get(level_name, [])

    # Generate full grid
    gridlines_raw, reference_z, grid_dict = generate_extended_gridlines_per_floor(
        rooms=rooms_on_level,
        walls=walls_on_level,
        doors=doors_on_level,
        spacing=0.5, # meters
        level_name=level_name, 
        global_bounds=global_bounds
    )

    # # Extract wall lines (in meters) for trimming
    _, wall_lines_2d, _ = create_graph(
        rooms=rooms_on_level,
        walls=walls_on_level,
        doors=doors_on_level,
        gridlines=[],  # no gridlines yet, just want wall_lines_2d
        level_name=level_name
    )

    final_gridlines = trim_gridlines(
        gridlines_raw, 
        wall_lines_2d, 
        doors_on_level,
        grid_dict,
        spacing=0.5, 
        gap_offset=0.3,
        reference_z=reference_z
    )


    # Use those edges directly (they still contain start, end, and optional room_id)
    G_floor, wall_lines_2d, _ = create_graph(
        rooms=rooms_on_level,
        walls=walls_on_level,
        doors=doors_on_level,
        gridlines=final_gridlines,
        level_name=level_name
    )

    # Add doors and continue with rest of processing...
    add_doors_on_grid(G_floor, doors_on_level, reference_z)
    add_stairs_on_grid(G_floor, stairs_on_level, reference_z)

    os.makedirs("graphs", exist_ok=True)
    with open(f"graphs/G_{level_name}.pkl", "wb") as f:
        pickle.dump(G_floor, f)

    graph_objects = graph_to_speckle_objects(
        G_floor, 
        level_name=level_name, 
        wall_lines=[],
        commit_edges=True
        )

    send_graph_to_speckle_per_floor(
        graph_objects,
        client,
        PROJECT_ID,
        level_name
    )