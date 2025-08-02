# run_paths_main.py

import os
import glob
import pickle
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')  # Ensure UTF-8 encoding for stdout

from speckle_credentials import SPECKLE_SERVER_URL, PROJECT_ID, MODEL_ID, SPECKLE_TOKEN_PATHS
from specklepy.api.client import SpeckleClient
from specklepy.api.wrapper import StreamWrapper
import networkx as nx
from path_of_travel import (
    stitch_subgraphs,
    map_room_center_to_start_nodes,
    map_farthest_point_from_door,
    find_shortest_paths,
    visualize_shortest_paths
)
from debug_utils import (
    report_unreachable_start_nodes,
    inspect_exit_node_connectivity,
    inspect_graph_z_levels,
    check_graph_connectivity, 
    clean_speckle_objects, 
    debug_door_connections
)
from send_utils import send_paths_to_speckle, graph_to_speckle_objects

def main():
    print("🔥 Starting Fire Safety Compliance Check...")

    wrapper = StreamWrapper(f"{SPECKLE_SERVER_URL}/streams/{PROJECT_ID}/branches/main")
    client = SpeckleClient(host=wrapper.host)
    client.authenticate_with_token(SPECKLE_TOKEN_PATHS)

    graph_files = sorted(glob.glob("graphs/G_*.pkl"))
    if not graph_files:
        print("❌ No graph pickle files found in 'graphs/' directory.")
        return

    for graph_path in graph_files:
        level_name = os.path.splitext(os.path.basename(graph_path))[0].split("_")[-1]
        print(f"\n🏗️ Processing Floor: {level_name}")
        try:
            with open(graph_path, "rb") as f:
                G = pickle.load(f)
        except Exception as e:
            print(f"❌ Failed to load graph from {graph_path}: {e}")
            continue

        stitched = False
        if nx.number_connected_components(G) > 1:
            print("🔗 Found disconnected subgraphs → stitching required.")
            try:
                stitch_subgraphs(G)
                stitched = True
                print("✅ Subgraphs stitched.")
            except Exception as e:
                print(f"❌ Stitching failed: {e}")
                continue
        else:
            print("✅ Graph is already fully connected.")

        try:
            # map_room_center_to_start_nodes(G)
            map_farthest_point_from_door(G)
            debug_door_connections(G)
            with open(graph_path, "wb") as f_out:
                pickle.dump(G, f_out)
            print("💾 Updated graph saved with start/exit nodes.")
        except Exception as e:
            print(f"❌ Failed to update graph with start/exit metadata: {e}")
            continue

        else:
            print("✅ Graph is already fully connected.")
    
        try:
            paths = find_shortest_paths(G, algorithm="theta_star", max_jump_distance=2.0)
        except Exception as e:
            print(f"❌ Pathfinding failed for floor {level_name}: {e}")
            paths = []
        
        os.makedirs("paths", exist_ok=True)
        path_file = os.path.join("paths", f"paths_{level_name}.pkl")
        
        # Explicitly overwrite existing paths file
        if os.path.exists(path_file):
            print(f"♻️ Removing existing paths file: {path_file}")
            os.remove(path_file)

        try:
            with open(path_file, "wb") as f_out:
                pickle.dump(paths, f_out)
            print(f"✅ Paths saved to {path_file}")
        except Exception as e:
            print(f"❌ Failed to save paths: {e}")
            continue

        try:
            inspect_graph_z_levels(G)
            report_unreachable_start_nodes(G, paths)
            inspect_exit_node_connectivity(G)
            check_graph_connectivity(G)
        except Exception as e:
            print(f"⚠️ Debugging failed for floor {level_name}: {e}")

        path_lines = visualize_shortest_paths(paths, level_name=level_name)
        raw_graph_objects = graph_to_speckle_objects(G, level_name=level_name, wall_lines=[], commit_edges=True)
        graph_objects = clean_speckle_objects(raw_graph_objects)

        try:
            send_paths_to_speckle(graph_objects, path_lines, client, PROJECT_ID, level_name)
        except Exception as e:
            print(f"❌ Failed to upload results for {level_name}: {e}")

    print("\n✅ Fire Safety Compliance Check Complete.")

if __name__ == "__main__":
    main()
