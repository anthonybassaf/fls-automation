import glob
import os
import pickle
from collections import defaultdict

GRAPH_DIR = "graphs"

def list_adjacent_rooms(graph_path):
    with open(graph_path, "rb") as f:
        G = pickle.load(f)

    print(f"\n=== {os.path.basename(graph_path)} ===")
    room_neighbors = defaultdict(set)

    # Iterate through all nodes
    for node, data in G.nodes(data=True):
        room_id = data.get("room_id")
        room_name = data.get("room_name")

        # Skip unnamed nodes (connectors, doors, etc.)
        if not room_name:
            continue

        # Check all neighbors of this node
        for neighbor in G.neighbors(node):
            neighbor_data = G.nodes[neighbor]
            neighbor_room_id = neighbor_data.get("room_id")
            neighbor_room_name = neighbor_data.get("room_name")

            # Skip unnamed neighbors
            if not neighbor_room_name:
                continue

            # If neighbor belongs to a different room, record adjacency
            if neighbor_room_id != room_id:
                room_neighbors[(room_id, room_name)].add((neighbor_room_id, neighbor_room_name))

    # Print adjacency list
    for (rid, rname), neighbors in room_neighbors.items():
        neighbor_names = sorted({n[1] for n in neighbors})
        print(f"Room: {rname}")
        print(f"  Adjacent rooms: {neighbor_names}")

def main():
    graph_files = glob.glob(os.path.join(GRAPH_DIR, "G_*.pkl"))
    if not graph_files:
        print("No graph pickle files found.")
        return

    for graph_file in graph_files:
        list_adjacent_rooms(graph_file)

if __name__ == "__main__":
    main()
