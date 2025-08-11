import glob
import os
import pickle
from collections import defaultdict, Counter

GRAPH_DIR = "../graphs"

def bbox_touch_or_overlap(b1, b2, margin=1.0):
    return not (
        b1[1] + margin < b2[0] or
        b2[1] + margin < b1[0] or
        b1[3] + margin < b2[2] or
        b2[3] + margin < b1[2]
    )

def list_adjacent_rooms_from_bbox(graph_path):
    with open(graph_path, "rb") as f:
        G = pickle.load(f)

    print(f"\n=== {os.path.basename(graph_path)} ===")

    # Step 1: Group all nodes by room_id
    room_nodes = defaultdict(list)
    for node, data in G.nodes(data=True):
        rid = data.get("room_id")
        if rid:
            room_nodes[rid].append((node, data))

    # Step 2: Compute bounding box and infer best room_name
    room_bboxes = {}
    room_names = {}
    unnamed_counter = 1

    for rid, node_data_list in room_nodes.items():
        xs = [pt[0][0] for pt in node_data_list]
        ys = [pt[0][1] for pt in node_data_list]
        if not xs or not ys:
            continue
        room_bboxes[rid] = (min(xs), max(xs), min(ys), max(ys))

        # Collect name candidates from inside nodes
        name_counts = Counter()
        for _, data in node_data_list:
            name = data.get("room_name")
            if name and name.strip() and name != "?":
                name_counts[name.strip()] += 1

        if name_counts:
            best_name = name_counts.most_common(1)[0][0]
        else:
            best_name = f"Unnamed Room {unnamed_counter}"
            unnamed_counter += 1

        room_names[rid] = best_name

    # Step 3: Compute adjacency using bbox overlap
    adjacency = defaultdict(set)
    room_ids = list(room_bboxes.keys())
    for i, r1 in enumerate(room_ids):
        for j in range(i + 1, len(room_ids)):
            r2 = room_ids[j]
            if bbox_touch_or_overlap(room_bboxes[r1], room_bboxes[r2]):
                adjacency[r1].add(r2)
                adjacency[r2].add(r1)

    # Step 4: Print adjacency list
    for rid, neighbors in sorted(adjacency.items(), key=lambda x: room_names.get(x[0], "")):
        rname = room_names.get(rid, "Unknown")
        neighbor_names = sorted({room_names.get(nid, "?") for nid in neighbors})
        print(f"Room: {rname}")
        print(f"  Adjacent rooms: {neighbor_names}")

def main():
    graph_files = glob.glob(os.path.join(GRAPH_DIR, "G_*.pkl"))
    if not graph_files:
        print("❌ No graph pickle files found.")
        return

    for graph_file in graph_files:
        list_adjacent_rooms_from_bbox(graph_file)

if __name__ == "__main__":
    main()
