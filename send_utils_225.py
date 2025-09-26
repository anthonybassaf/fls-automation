# send_utils.py — geometry-only 2.25 viewer compat, logic unchanged

import math
from specklepy.objects import Base
from specklepy.objects.geometry import Point, Line, Mesh
from specklepy.objects.other import RenderMaterial
from specklepy.transports.server import ServerTransport
from specklepy.serialization.base_object_serializer import BaseObjectSerializer
from specklepy.api import operations
from speckle_credentials import BRANCH_NAME

# ── Compat switch (old viewer 2.25.* needs meshes) ─────────────────────────────
COMPAT_SPECKLE_225 = True  # set False to go back to Points/Lines

# ── Visual knobs (kept small & cyan/yellow/red like your original) ─────────────
NODE_RADIUS_M   = 0.06  # smaller spheres than before
EDGE_RADIUS_M   = 0.02  # tube radius for graph edges
WALL_RADIUS_M   = 0.025 # tube radius for wall segments
Z_LIFT_NODES    = 0.02  # minimal lifts to avoid z-fighting shimmer
Z_LIFT_EDGES    = 0.01
Z_LIFT_WALLS    = 0.005

# ── Materials (match your old colors) ─────────────────────────────────────────
MAT_CYAN   = RenderMaterial(diffuse=0xFF00FFFF, opacity=1.0)  # regular nodes
MAT_YELLOW = RenderMaterial(diffuse=0xFFFFFF00, opacity=1.0)  # doors
MAT_RED    = RenderMaterial(diffuse=0xFFFF0000, opacity=1.0)  # stairs
MAT_WALL   = RenderMaterial(diffuse=0xFFFF5555, opacity=1.0)  # walls

# ── utilities ─────────────────────────────────────────────────────────────────
def _finite3(a, b, c):
    try:
        return all(math.isfinite(float(v)) for v in (a, b, c))
    except Exception:
        return False

def _pt(x, y, z):
    p = Point(x=float(x), y=float(y), z=float(z))
    p.units = "m"
    return p

def _mk_sphere_mesh(x, y, z, r=NODE_RADIUS_M, rings=6, sectors=10, z_lift=0.0):
    z = float(z) + float(z_lift)
    if not _finite3(x, y, z) or r <= 0:
        return None
    verts, faces = [], []
    for i in range(rings + 1):
        phi = math.pi * i / rings
        sp, cp = math.sin(phi), math.cos(phi)
        for j in range(sectors):
            th = 2 * math.pi * j / sectors
            st, ct = math.sin(th), math.cos(th)
            vx = x + r * st * sp
            vy = y + r * ct * sp
            vz = z + r * cp
            verts.extend([vx, vy, vz])
    def idx(ii, jj): return ii * sectors + (jj % sectors)
    for i in range(rings):
        for j in range(sectors):
            a = idx(i, j); b = idx(i + 1, j); c = idx(i + 1, j + 1); d = idx(i, j + 1)
            faces.extend([a, b, c,  a, c, d])
    m = Mesh()
    m.vertices = [float(v) for v in verts]
    m.faces = [int(f) for f in faces]
    m.units = "m"
    return m

def _mk_tube_mesh(x1, y1, z1, x2, y2, z2, r, segments=12, z_lift=0.0):
    """Cylinder along (p1->p2) with radius r, avoids strip diagonals."""
    z1 = float(z1) + float(z_lift)
    z2 = float(z2) + float(z_lift)
    if not (_finite3(x1, y1, z1) and _finite3(x2, y2, z2)) or r <= 0:
        return None
    dx, dy, dz = (x2 - x1), (y2 - y1), (z2 - z1)
    L = math.sqrt(dx*dx + dy*dy + dz*dz)
    if L <= 0:
        return None

    # Build local orthonormal frame {u, w} ⟂ v
    vx, vy, vz = dx / L, dy / L, dz / L
    # pick a helper that's not collinear with v
    hx, hy, hz = (0.0, 0.0, 1.0) if abs(vz) < 0.9 else (1.0, 0.0, 0.0)
    # u = normalize(h × v)
    ux, uy, uz = (hy*vz - hz*vy, hz*vx - hx*vz, hx*vy - hy*vx)
    ul = math.sqrt(ux*ux + uy*uy + uz*uz)
    if ul == 0:
        return None
    ux, uy, uz = ux/ul, uy/ul, uz/ul
    # w = v × u
    wx, wy, wz = (vy*uz - vz*uy, vz*ux - vx*uz, vx*uy - vy*ux)

    verts, faces = [], []
    # circles at both ends
    for end in [(x1, y1, z1), (x2, y2, z2)]:
        ex, ey, ez = end
        for i in range(segments):
            t = 2 * math.pi * i / segments
            cx = ex + r * (math.cos(t)*ux + math.sin(t)*wx)
            cy = ey + r * (math.cos(t)*uy + math.sin(t)*wy)
            cz = ez + r * (math.cos(t)*uz + math.sin(t)*wz)
            verts.extend([cx, cy, cz])

    # side faces
    for i in range(segments):
        a = i
        b = (i + 1) % segments
        c = segments + (i + 1) % segments
        d = segments + i
        faces.extend([a, b, c,  a, c, d])

    m = Mesh()
    m.vertices = [float(v) for v in verts]
    m.faces = [int(f) for f in faces]
    m.units = "m"
    return m

# ── your send functions (unchanged behavior) ──────────────────────────────────
def send_model_to_speckle_per_floor(objects_to_send, client, stream_id, level_name, message_prefix="Fire Safety Model"):
    output = Base()
    output["elements"] = objects_to_send
    output["name"] = f"{message_prefix} – Floor: {level_name}"

    compliance_color = RenderMaterial(diffuse=4294901760, opacity=0.4)
    for obj in objects_to_send:
        if hasattr(obj, "renderMaterial"):
            if (obj.get("complianceStatus") != "Compliant"):
                obj.renderMaterial = compliance_color

    serializer = BaseObjectSerializer()
    hash_id, obj = serializer.traverse_base(output)

    try:
        client.object.create(stream_id=stream_id, objects=[obj])
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
    # keep same shape you used before
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
    # unchanged except a visible yellow for paths (your code already did this)
    if not graph_objects and not path_lines:
        print(f"⚠️ No objects to commit for {level_name}.")
        return

    yellow = RenderMaterial(diffuse=0xFFFFFF00, opacity=1.0)
    if path_lines:
        for pl in path_lines:
            pl["renderMaterial"] = yellow
            pl["category"] = "path_edge"
            pl["displayStyle"] = {"lineWidth": 3}

    base_obj = Base()
    if graph_objects:
        base_obj["graph_edges"] = graph_objects
    if path_lines:
        base_obj["paths"] = path_lines

    base_obj["name"] = f"Travel Distances – {level_name}"
    base_obj["units"] = "m"
    base_obj["level"] = level_name

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

# ── the only function whose geometry we swap (logic & styling preserved) ──────
def graph_to_speckle_objects(G, level_name=None, wall_lines=None, commit_edges=False, stride=4):
    """
    Same logic & colors as your original function:
      - regular nodes = cyan, doors = yellow, stairs = red
      - stride thinning for regular nodes
      - same categories and metadata
    Only the GEOMETRY changes in compat mode:
      nodes -> small sphere meshes, edges/walls -> tube meshes.
    """
    edge_objs = []
    node_objs = []

    # NODES
    for (node, data) in G.nodes(data=True):
        node_type = data.get("type")
        x, y, z = node

        # keep your stride filter for non-doors/stairs
        if node_type not in {"door", "stair"} and (round(x / stride) % 1 != 0 or round(y / stride) % 1 != 0):
            continue
        if not _finite3(x, y, z):
            continue

        if COMPAT_SPECKLE_225:
            m = _mk_sphere_mesh(x, y, z, r=NODE_RADIUS_M, z_lift=Z_LIFT_NODES)
            if not m:
                continue
            if node_type == "door":
                m["category"] = "graph_node_door"
                m["renderMaterial"] = MAT_YELLOW
            elif node_type == "stair":
                m["category"] = "graph_node_stair"
                m["renderMaterial"] = MAT_RED
            else:
                m["category"] = "graph_node"
                m["renderMaterial"] = MAT_CYAN
            if level_name: m["floor"] = level_name
            if "source_id" in data: m["source_id"] = data["source_id"]
            node_objs.append(m)
        else:
            pt = _pt(x, y, z)
            pt["units"] = "m"
            if node_type == "door":
                pt["category"] = "graph_node_door"
                pt["renderMaterial"] = MAT_YELLOW
                pt["displayStyle"] = {"pointSize": 8}
            elif node_type == "stair":
                pt["category"] = "graph_node_stair"
                pt["renderMaterial"] = MAT_RED
                pt["displayStyle"] = {"pointSize": 8}
            else:
                pt["category"] = "graph_node"
                pt["renderMaterial"] = MAT_CYAN
                pt["displayStyle"] = {"pointSize": 3}
            if level_name: pt["floor"] = level_name
            if "source_id" in data: pt["source_id"] = data["source_id"]
            node_objs.append(pt)

    # EDGES
    if commit_edges:
        for u, v, data in G.edges(data=True):
            if u == v:
                continue
            x1, y1, z1 = u; x2, y2, z2 = v
            if not (_finite3(x1, y1, z1) and _finite3(x2, y2, z2)):
                continue
            if x1 == x2 and y1 == y2 and z1 == z2:
                continue

            if COMPAT_SPECKLE_225:
                m = _mk_tube_mesh(x1, y1, z1, x2, y2, z2, r=EDGE_RADIUS_M, z_lift=Z_LIFT_EDGES)
                if not m:
                    continue
                m["category"] = "graph_edge"
                # (no explicit color in your original edges; keep that behavior)
                if level_name: m["floor"] = level_name
                edge_objs.append(m)
            else:
                ln = Line(start=_pt(x1, y1, z1), end=_pt(x2, y2, z2), units="m")
                ln["weight"] = data.get("weight", 1.0)
                ln["category"] = "graph_edge"
                if level_name: ln["floor"] = level_name
                edge_objs.append(ln)

    # WALLS
    wall_objs = []
    if wall_lines:
        for w in wall_lines:
            try:
                sx = getattr(w.start, "x", w["start"]["x"])
                sy = getattr(w.start, "y", w["start"]["y"])
                sz = getattr(w.start, "z", w["start"].get("z", 0.0))
                ex = getattr(w.end,   "x", w["end"]["x"])
                ey = getattr(w.end,   "y", w["end"]["y"])
                ez = getattr(w.end,   "z", w["end"].get("z", 0.0))
            except Exception:
                continue
            if not (_finite3(sx, sy, sz) and _finite3(ex, ey, ez)):
                continue
            if sx == ex and sy == ey and sz == ez:
                continue

            if COMPAT_SPECKLE_225:
                m = _mk_tube_mesh(sx, sy, sz, ex, ey, ez, r=WALL_RADIUS_M, z_lift=Z_LIFT_WALLS)
                if not m:
                    continue
                m["category"] = "wall_segment"
                m["renderMaterial"] = MAT_WALL  # keep your wall color
                if level_name: m["floor"] = level_name
                wall_objs.append(m)
            else:
                ln = Line(start=_pt(sx, sy, sz), end=_pt(ex, ey, ez), units="m")
                ln["category"] = "wall_segment"
                ln["renderMaterial"] = MAT_WALL
                if level_name: ln["floor"] = level_name
                wall_objs.append(ln)

    # return in the same structure you already expect
    return node_objs + edge_objs + wall_objs