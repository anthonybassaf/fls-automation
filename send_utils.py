from specklepy.objects import Base
from specklepy.objects.other import RenderMaterial
from specklepy.transports.server import ServerTransport
from specklepy.serialization.base_object_serializer import BaseObjectSerializer
from specklepy.api import operations
from speckle_credentials import  BRANCH_NAME

def send_model_to_speckle_per_floor(objects_to_send, client, stream_id, level_name, message_prefix="Fire Safety Model"):
    """
    Uploads and commits any model objects (e.g., color-coded rooms) for a specific floor.
    Each call creates a new commit tagged by the level name.
    """
    output = Base()
    output["elements"] = objects_to_send
    output["name"] = f"{message_prefix} – Floor: {level_name}"


    compliance_color = RenderMaterial(diffuse= 4294901760,opacity= 0.4)

    for obj in objects_to_send:
        # Ensure the object has a unique ID
        if hasattr(obj, "renderMaterial"):
            if(obj["complianceStatus"] != "Compliant"):
                obj.renderMaterial = compliance_color

    serializer = BaseObjectSerializer()
    hash_id, obj = serializer.traverse_base(output)

    try:
        obj_create = client.object.create(stream_id=stream_id, objects=[obj])
        print(f"📦 Uploaded model for {level_name}. Hash: {hash_id}")
    except Exception as e:
        print(f"❌ Failed to upload model for {level_name}: {e}")
        return

    try:
        commit_id = client.commit.create(
            stream_id=stream_id,
            object_id=hash_id,
            branch_name="main",
            message=f"{message_prefix} – Floor: {level_name}"
        )
        print(f"✅ Commit created for {level_name}. Commit ID: {commit_id}")

    except Exception as e:
        print(f"❌ Failed to create commit for {level_name}: {e}")


def send_graph_to_speckle_per_floor(graph_objects, client, stream_id, level_name):
    if not graph_objects:
        print(f"⚠️ No graph objects to commit for floor: {level_name}. Skipping.")
        return

    print(f"📦 Preparing to commit {len(graph_objects)} objects for {level_name}...")

    output = Base()
    output["graph_edges"] = graph_objects
    output["name"] = f"Graph for {level_name}"
    output["units"] = "m"

    transport = ServerTransport(client=client, stream_id=stream_id)

    try:
        object_id = operations.send(base=output, transports=[transport])
        print(f"📦 Uploaded object for {level_name}. Object ID: {object_id}")
        if not object_id:
            print("❌ Upload failed or returned empty object ID. Skipping commit.")
            return
    except Exception as e:
        print(f"❌ Failed to upload object for {level_name}: {e}")
        return

    try:
        commit_id = client.commit.create(
            stream_id=stream_id,
            object_id=object_id,
            branch_name="main",
            message=f"Fire Safety Graph – Floor: {level_name}"
        )
        print(f"✅ Commit created for {level_name}. Commit ID: {commit_id}")
    except Exception as e:
        print(f"❌ Failed to create commit for {level_name}: {e}")


def send_paths_to_speckle(graph_objects, path_lines, client, stream_id, level_name):
    from specklepy.objects.other import RenderMaterial

    if not graph_objects and not path_lines:
        print(f"⚠️ No objects to commit for {level_name}.")
        return

    print(f"📦 Preparing to commit results for {level_name}...")

    # Create base object
    def ensure_base_list(items):
        return [item if isinstance(item, Base) else Base() for item in items]
    
    # Assign a yellow material to path lines so they are visible in the viewer 
    if path_lines:
        yellow = RenderMaterial(diffuse=0xFFFFFF00, opacity=1.0)
        for pline in path_lines:
            pline["renderMaterial"] = yellow
            pline["category"] = "path_edge"
            pline["displayStyle"] = {"lineWidth": 3}  # makes lines thicker

    base_obj = Base()
    if graph_objects:
        base_obj["graph_edges"] = ensure_base_list(graph_objects)
    if path_lines:
        base_obj["paths"] = ensure_base_list(path_lines)

    base_obj["name"] = f"Travel Distances – {level_name}"
    base_obj["units"] = "m"
    base_obj["level"] = level_name
    base_obj["description"] = f"Fire safety compliance analysis for floor {level_name}"

    try:
        transport = ServerTransport(client=client, stream_id=stream_id)
        object_id = operations.send(base=base_obj, transports=[transport])

        commit_id = client.commit.create(
            stream_id=stream_id,
            object_id=object_id,
            branch_name=BRANCH_NAME,
            message=f"Travel Distance Results – Floor: {level_name}"
        )
        print(f"✅ Commit created for {level_name}. Commit ID: {commit_id}")
    except Exception as e:
        print(f"❌ Failed to send object for {level_name}: {e}")
        return


def graph_to_speckle_objects(G, level_name=None, wall_lines=None, commit_edges=False, stride=4):
    from specklepy.objects.geometry import Point, Line
    from specklepy.objects.other import RenderMaterial

    edge_lines = []
    node_points = []

    # Materials
    cyan = RenderMaterial(diffuse=0xFF00FFFF, opacity=1.0)     # Regular nodes
    yellow = RenderMaterial(diffuse=0xFFFFFF00, opacity=1.0)   # Doors
    red = RenderMaterial(diffuse=0xFFFF0000, opacity=1.0)      # Stairs
    wall_color = RenderMaterial(diffuse=0xFFFF5555, opacity=1.0)

    for i, (node, data) in enumerate(G.nodes(data=True)):
        node_type = data.get("type")

        x, y, _ = node
        if node_type not in {"door", "stair"} and (round(x / stride) % 1 != 0 or round(y / stride) % 1 != 0):
            continue

        pt = Point(x=node[0], y=node[1], z=node[2])
        pt["units"] = "m"

        if node_type == "door":
            pt["category"] = "graph_node_door"
            pt["renderMaterial"] = yellow
            pt["displayStyle"] = {"pointSize": 8}
        elif node_type == "stair":
            pt["category"] = "graph_node_stair"
            pt["renderMaterial"] = red
            pt["displayStyle"] = {"pointSize": 8}
        else:
            pt["category"] = "graph_node"
            pt["renderMaterial"] = cyan
            pt["displayStyle"] = {"pointSize": 3}

        if level_name:
            pt["floor"] = level_name
        if "source_id" in data:
            pt["source_id"] = data["source_id"]

        node_points.append(pt)

    if commit_edges:
        for u, v, data in G.edges(data=True):
            if u == v:
                continue
            line = Line(
                start=Point(x=u[0], y=u[1], z=u[2]),
                end=Point(x=v[0], y=v[1], z=v[2]),
                units="m"
            )
            line["weight"] = data.get("weight", 1.0)
            line["category"] = "graph_edge"
            if level_name:
                line["floor"] = level_name
            edge_lines.append(line)

    if wall_lines:
        for wall in wall_lines:
            wall["category"] = "wall_segment"
            wall["renderMaterial"] = wall_color
            if level_name:
                wall["floor"] = level_name

    return node_points + edge_lines + (wall_lines or [])

