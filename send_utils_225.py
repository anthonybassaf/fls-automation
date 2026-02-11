# send_utils.py — geometry-only 2.25 viewer compat, with chunking for large commits

import math
import re
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

# ── Chunking parameters ─────────────────────────────────────────────────────
MAX_OBJECTS_PER_CHUNK = 500  # Adjust based on your object sizes
MAX_CHUNK_SIZE_MB = 50  # Target size per chunk (more conservative due to detached objects)
MAX_INDIVIDUAL_OBJECT_MB = 80  # Flag objects over this size

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

def _estimate_full_object_tree_size_mb(obj, visited=None):
    """
    CRITICAL FIX: Recursively estimate TOTAL size including ALL detached child objects.
    The original function only measured top-level, missing nested geometry that
    Speckle detaches and uploads separately - causing 299 MB objects to appear as 0.01 MB!
    """
    if visited is None:
        visited = set()
    
    # Avoid circular references
    obj_id = id(obj)
    if obj_id in visited:
        return 0.0
    visited.add(obj_id)
    
    total_size_bytes = 0
    
    try:
        # Get the serializer's view of this object (includes detachment info)
        serializer = BaseObjectSerializer()
        serialized_dict, detached_objs = serializer.traverse_base(obj)
        
        # 1. Size of the main object
        import json
        main_size = len(json.dumps(serialized_dict, default=str).encode('utf-8'))
        total_size_bytes += main_size
        
        # 2. Size of ALL detached children (THIS WAS MISSING!)
        for detached_obj in detached_objs.values():
            if isinstance(detached_obj, Base):
                # Recursively measure each detached object
                child_size_mb = _estimate_full_object_tree_size_mb(detached_obj, visited)
                total_size_bytes += int(child_size_mb * 1024 * 1024)
        
        return total_size_bytes / (1024 * 1024)  # Convert to MB
        
    except Exception as e:
        # Fallback: use a conservative estimate
        print(f"⚠️ Could not estimate object size: {e}. Using conservative estimate.")
        return 5.0  # Assume 5 MB if we can't measure (conservative)

def _estimate_object_size_mb(obj):
    """Wrapper for backward compatibility - uses the correct full tree estimation."""
    return _estimate_full_object_tree_size_mb(obj)

def _chunk_objects(objects_list, max_objects=MAX_OBJECTS_PER_CHUNK, max_size_mb=MAX_CHUNK_SIZE_MB):
    """
    Split objects into chunks that respect both count and size limits.
    Returns list of chunks (each chunk is a list of objects).
    """
    if not objects_list:
        return []
    
    chunks = []
    current_chunk = []
    current_size_mb = 0.0
    
    for obj in objects_list:
        obj_size = _estimate_object_size_mb(obj)
        
        # If single object exceeds limit, it needs special handling
        if obj_size > max_size_mb:
            print(f"⚠️ Warning: Single object exceeds {max_size_mb} MB ({obj_size:.2f} MB). "
                  "Consider simplifying geometry or reducing detail.")
            # Put it in its own chunk anyway
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_size_mb = 0.0
            chunks.append([obj])
            continue
        
        # Check if adding this object would exceed limits
        if (len(current_chunk) >= max_objects or 
            current_size_mb + obj_size > max_size_mb):
            # Start new chunk
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [obj]
            current_size_mb = obj_size
        else:
            # Add to current chunk
            current_chunk.append(obj)
            current_size_mb += obj_size
    
    # Don't forget the last chunk
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks

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

DEFAULT_GRAPH_BRANCH_LABEL = "Fire Safety Graphs"
DEFAULT_PATHS_BRANCH_LABEL = "Travel Distances Graphs"
DEFAULT_FLS_BRANCH_LABEL = "Compliance Check Results"

def _speckle_slug(name: str) -> str:
    import re
    s = name.strip().lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "branch"

DEFAULT_GRAPH_BRANCH = _speckle_slug(DEFAULT_GRAPH_BRANCH_LABEL)  # "fire-safety-graphs"
DEFAULT_PATHS_BRANCH = _speckle_slug(DEFAULT_PATHS_BRANCH_LABEL)  # "travel-distances-graphs"
DEFAULT_FLS_BRANCH = _speckle_slug(DEFAULT_FLS_BRANCH_LABEL)      # "compliance-check-results"

def _ensure_branch(client, stream_id: str, branch_label: str, description: str = "") -> str:
    """Return the slug of a branch; create it if missing."""
    slug = _speckle_slug(branch_label)

    # 1) Ask Speckle for the branch; get() returns None if it doesn't exist.
    existing = client.branch.get(stream_id=stream_id, name=slug)
    if existing is None:
        # 2) Create it
        client.branch.create(
            stream_id=stream_id,
            name=slug,  # must be the slug
            description=description or branch_label,
        )
    return slug


# def send_model_to_speckle_per_floor(objects_to_send, client, stream_id, level_name, message_prefix="Fire Safety Model"):
#     """
#     Send model to Speckle with automatic chunking to handle large object sets.
#     Splits objects into multiple commits if necessary to stay under 100 MB limit.
#     """
#     if not objects_to_send:
#         print(f"⚠️ No objects to send for {level_name}")
#         return
    
#     # Color non-compliant elements
#     compliance_color = RenderMaterial(diffuse=4294901760, opacity=0.4)
#     for obj in objects_to_send:
#         try:
#             # If it's a Mesh/Line/etc. add material directly
#             if obj.get("complianceStatus") != "Compliant":
#                 obj["renderMaterial"] = compliance_color
#         except Exception:
#             pass
    
#     # DIAGNOSTIC: Analyze objects before chunking to identify problems
#     print(f"🔍 Analyzing {len(objects_to_send)} objects for {level_name}...")
#     large_objects = []
#     total_estimated = 0.0
    
#     for idx, obj in enumerate(objects_to_send):
#         try:
#             obj_size = _estimate_object_size_mb(obj)
#             total_estimated += obj_size
            
#             if obj_size > MAX_INDIVIDUAL_OBJECT_MB:
#                 obj_type = type(obj).__name__
#                 obj_name = getattr(obj, 'name', 'N/A')
#                 obj_id_str = str(getattr(obj, 'id', 'N/A'))
#                 obj_id_short = obj_id_str[:16] + '...' if len(obj_id_str) > 16 else obj_id_str
#                 print(f"⚠️  LARGE OBJECT #{idx}: {obj_type} '{obj_name}' (ID: {obj_id_short}) = {obj_size:.2f} MB")
#                 large_objects.append((idx, obj, obj_size))
                
#                 # Try to identify what's making it large
#                 if hasattr(obj, 'displayValue') and obj.displayValue:
#                     if isinstance(obj.displayValue, list):
#                         print(f"    ↳ Has displayValue with {len(obj.displayValue)} items")
#                     else:
#                         dv_size = _estimate_object_size_mb(obj.displayValue)
#                         if dv_size > 10:
#                             print(f"    ↳ displayValue is {dv_size:.2f} MB")
#                 if hasattr(obj, 'elements') and obj.elements and isinstance(obj.elements, list):
#                     print(f"    ↳ Has elements collection with {len(obj.elements)} items")
#         except Exception as e:
#             print(f"⚠️  Could not analyze object #{idx}: {e}")
    
#     print(f"📊 Total estimated size for {level_name}: {total_estimated:.2f} MB")
    
#     if large_objects:
#         print(f"❌ WARNING: Found {len(large_objects)} object(s) exceeding {MAX_INDIVIDUAL_OBJECT_MB} MB!")
#         print(f"   These may cause upload failures. Consider simplifying these objects.")
    
#     # Split objects into manageable chunks
#     chunks = _chunk_objects(objects_to_send, 
#                            max_objects=MAX_OBJECTS_PER_CHUNK, 
#                            max_size_mb=MAX_CHUNK_SIZE_MB)
    
#     print(f"📦 Splitting {len(objects_to_send)} objects into {len(chunks)} chunk(s) for {level_name}")
    
#     # Ensure branch exists
#     branch_slug = _ensure_branch(
#         client=client,
#         stream_id=stream_id,
#         branch_label=DEFAULT_FLS_BRANCH_LABEL,
#         description="Auto-generated branch for Fire & Life Safety outputs",
#     )
    
#     # Send each chunk as a separate commit
#     for chunk_idx, chunk in enumerate(chunks, 1):
#         output = Base()
#         output["elements"] = chunk
        
#         if len(chunks) > 1:
#             output["name"] = f"{message_prefix} – Floor: {level_name} (Part {chunk_idx}/{len(chunks)})"
#             commit_message = f"{message_prefix} – Floor: {level_name} (Part {chunk_idx}/{len(chunks)})"
#         else:
#             output["name"] = f"{message_prefix} – Floor: {level_name}"
#             commit_message = f"{message_prefix} – Floor: {level_name}"
        
#         output["units"] = "m"
        
#         # Upload via transport
#         try:
#             transport = ServerTransport(client=client, stream_id=stream_id)
#             object_id = operations.send(base=output, transports=[transport])
            
#             estimated_size = sum(_estimate_object_size_mb(obj) for obj in chunk)
#             print(f"📦 Uploaded chunk {chunk_idx}/{len(chunks)} for {level_name}. "
#                   f"Object ID: {object_id} (~{estimated_size:.2f} MB, {len(chunk)} objects)")
#         except Exception as e:
#             print(f"❌ Failed to upload chunk {chunk_idx}/{len(chunks)} for {level_name}: {e}")
#             continue
        
#         # Commit to branch
#         try:
#             commit_id = client.commit.create(
#                 stream_id=stream_id,
#                 object_id=object_id,
#                 branch_name=branch_slug,
#                 message=commit_message,
#             )
#             print(f"✅ Commit {chunk_idx}/{len(chunks)} created for {level_name} on '{branch_slug}'. Commit ID: {commit_id}")
#         except Exception as e:
#             print(f"❌ Failed to create commit {chunk_idx}/{len(chunks)} for {level_name}: {e}")
def send_model_to_speckle_per_floor(
    objects_to_send,
    client,
    stream_id,
    level_name,
    message_prefix="Fire Safety Model",
    enable_chunking=True,
    seed_max_objects=160,          # initial “probe” chunk size by count
    max_split_depth=16,
    oversized_policy="skip",       # "skip" OR "strip"
):
    """
    Goal:
      - keep chunking/oversize detection
      - BUT create ONLY ONE commit per floor.

    How:
      1) Probe-upload chunks (NO commits) to detect which subsets fit.
      2) If a single element is too large, apply policy (skip or strip display geometry).
      3) Build ONE final root Base with a detachable '@elements' containing ALL accepted elements.
      4) Send + commit ONCE.

    Notes:
      - Speckle's 100MB limit is per stored object. If a single element exceeds it,
        the only options are strip geometry or skip.
      - '@elements' is important: it makes the element list detachable. :contentReference[oaicite:1]{index=1}
    """
    if not objects_to_send:
        print(f"⚠️ No objects to commit for floor: {level_name}. Skipping.")
        return

    # ----------------------------
    # Imports (local)
    # ----------------------------
    from specklepy.objects import Base
    from specklepy.objects.other import RenderMaterial
    from specklepy.transports.server import ServerTransport
    from specklepy.api import operations

    # One transport reused for all sends
    transport = ServerTransport(client=client, stream_id=stream_id)

    # ----------------------------
    # Visual: color non-compliant
    # ----------------------------
    compliance_color = RenderMaterial(diffuse=4294901760, opacity=0.4)
    for obj in objects_to_send:
        try:
            if obj.get("complianceStatus") != "Compliant":
                obj["renderMaterial"] = compliance_color
        except Exception:
            pass

    # ----------------------------
    # Helpers
    # ----------------------------
    def _is_object_too_large(exc: Exception) -> bool:
        msg = str(exc) or ""
        return ("Object too large" in msg) or ("status code 400" in msg and "too large" in msg.lower())

    def _safe_get(obj, key, default=None):
        try:
            if hasattr(obj, "get"):
                v = obj.get(key)
                return default if v is None else v
        except Exception:
            pass
        try:
            v = getattr(obj, key)
            return default if v is None else v
        except Exception:
            return default

    def _describe_obj(obj) -> str:
        speckle_type = _safe_get(obj, "speckle_type", "")
        app_id = _safe_get(obj, "applicationId", "") or _safe_get(obj, "id", "")
        cat = _safe_get(obj, "category", "")
        fam = _safe_get(obj, "family", "")
        typ = _safe_get(obj, "type", "")
        return f"speckle_type={speckle_type}, applicationId={app_id}, category={cat}, family={fam}, type={typ}"

    def _strip_display_geometry(obj):
        """
        Metadata-only fallback: remove common heavy geometry fields.
        This is the only practical way to pass the 100MB *per-object* limit
        when one element is huge.
        """
        import copy
        try:
            o = copy.copy(obj)
        except Exception:
            o = obj  # last resort (mutates original)

        # common keys in Revit->Speckle conversions
        for k in ("displayValue", "displayMesh", "displayStyle", "@displayValue"):
            # dict-like
            try:
                if hasattr(o, "__contains__") and k in o:
                    o[k] = []
            except Exception:
                pass
            # attribute-like
            try:
                if hasattr(o, k):
                    setattr(o, k, [])
            except Exception:
                pass
        return o

    def _probe_send(chunk_list, tag) -> None:
        """
        Probe upload: send a container holding chunk_list using '@elements'.
        NO commit here.
        If this fails with "Object too large", caller will split.
        """
        probe = Base()
        probe["@elements"] = chunk_list
        probe["name"] = f"{message_prefix} – Floor: {level_name} (probe {tag})"
        probe["units"] = "m"
        _ = operations.send(base=probe, transports=[transport])

    def _probe_split(chunk_list, tag, depth) -> list:
        """
        Returns a list of ACCEPTED elements (may be smaller than input if we skip/strip).
        Uses server feedback to find which subsets are uploadable.
        """
        if depth > max_split_depth:
            print(f"❌ Max split depth reached for {level_name} {tag}. Skipping this subset.")
            return []

        try:
            _probe_send(chunk_list, tag)
            # success => accept all
            return chunk_list
        except Exception as e:
            if not (enable_chunking and _is_object_too_large(e)):
                print(f"❌ Probe failed for {level_name} {tag}: {e}")
                return []

            n = len(chunk_list)
            if n <= 1:
                obj = chunk_list[0] if chunk_list else None
                print(f"🚨 Single element still too large for {level_name} {tag}: {_describe_obj(obj) if obj else ''}")

                if obj is None:
                    return []

                if oversized_policy.lower() == "strip":
                    lite = _strip_display_geometry(obj)
                    try:
                        _probe_send([lite], f"{tag}-lite")
                        print(f"✅ Using metadata-only fallback for {level_name} {tag}")
                        return [lite]
                    except Exception as e2:
                        print(f"❌ Even metadata-only fallback failed for {level_name} {tag}: {e2}")
                        return []
                else:
                    print(f"⏭️ Skipping oversized element for {level_name} {tag} (policy='skip')")
                    return []

            # split into halves and recurse
            mid = n // 2
            left = _probe_split(chunk_list[:mid], f"{tag}.A", depth + 1)
            right = _probe_split(chunk_list[mid:], f"{tag}.B", depth + 1)
            return left + right

    # ----------------------------
    # 1) Probe + build final accepted list
    # ----------------------------
    accepted = []
    if enable_chunking:
        # seed split by count to reduce probe depth
        seeds = [objects_to_send[i:i + seed_max_objects] for i in range(0, len(objects_to_send), seed_max_objects)]
        print(f"📦 Seeding {len(objects_to_send)} objects into {len(seeds)} part(s) for {level_name} (seed_max_objects={seed_max_objects})")
        for i, seed in enumerate(seeds, 1):
            accepted.extend(_probe_split(seed, f"{i}/{len(seeds)}", 0))
    else:
        accepted = list(objects_to_send)

    if not accepted:
        print(f"❌ Nothing acceptable to commit for {level_name} after probing.")
        return

    skipped = len(objects_to_send) - len(accepted)
    if skipped > 0:
        print(f"⚠️ {skipped} element(s) were skipped/stripped for {level_name} due to the 100MB per-object limit.")

    # ----------------------------
    # 2) ONE final send + ONE commit
    # ----------------------------
    final_root = Base()
    final_root["@elements"] = accepted  # critical: detachable
    final_root["name"] = f"{message_prefix} – Floor: {level_name}"
    final_root["units"] = "m"

    try:
        final_object_id = operations.send(base=final_root, transports=[transport])
        print(f"📦 Uploaded FINAL floor object for {level_name}. Object ID: {final_object_id} ({len(accepted)} objects)")
    except Exception as e:
        print(f"❌ Failed to upload FINAL floor object for {level_name}: {e}")
        return

    try:
        branch_slug = _ensure_branch(
            client=client,
            stream_id=stream_id,
            branch_label=DEFAULT_FLS_BRANCH_LABEL,
            description="Auto-generated branch for Fire & Life Safety outputs",
        )

        msg = f"{message_prefix} – Floor: {level_name}"
        if skipped > 0:
            msg += f" (skipped/stripped {skipped} oversized)"

        commit_id = client.commit.create(
            stream_id=stream_id,
            object_id=final_object_id,
            branch_name=branch_slug,
            message=msg,
        )
        print(f"✅ SINGLE commit created for {level_name} on '{branch_slug}'. Commit ID: {commit_id}")
    except Exception as e:
        print(f"❌ Failed to create FINAL commit for {level_name}: {e}")


def send_graph_to_speckle_per_floor(graph_objects, client, stream_id, level_name):
    if not graph_objects:
        print(f"⚠️ No graph objects to commit for floor: {level_name}. Skipping.")
        return

    print(f"📦 Preparing to commit {len(graph_objects)} objects for {level_name}...")

    # 1) Upload the object
    output = Base()
    output["graph_edges"] = graph_objects
    output["name"] = f"Graph for {level_name}"
    output["units"] = "m"

    transport = ServerTransport(client=client, stream_id=stream_id)
    try:
        object_id = operations.send(base=output, transports=[transport])
        if not object_id:
            print("❌ Upload failed (empty object ID).")
            return
        print(f"✅ Uploaded object for {level_name}. Object ID: {object_id}")
    except Exception as e:
        print(f"❌ Failed to upload object for {level_name}: {e}")
        return

    # 2) Ensure branch exists (create if needed), then commit to the SLUG
    branch_slug = _ensure_branch(
        client=client,
        stream_id=stream_id,
        branch_label=DEFAULT_GRAPH_BRANCH_LABEL,
        description="Auto-generated branch for Fire & Life Safety graph outputs",
    )
    print(f"🔀 Using branch: '{branch_slug}' (from label '{DEFAULT_GRAPH_BRANCH_LABEL}')")

    try:
        commit_id = client.commit.create(
            stream_id=stream_id,
            object_id=object_id,
            branch_name=branch_slug,  # <-- must be slug, not the pretty label
            message=f"Fire Safety Graph – Floor: {level_name}",
        )
        print(f"✅ Commit created for {level_name} on '{branch_slug}'. Commit ID: {commit_id}")
    except Exception as e:
        print(f"❌ Failed to create commit for {level_name} on '{branch_slug}': {e}")


def send_paths_to_speckle(graph_objects, path_lines, client, stream_id, level_name):
    """
    Render paths exactly like graph edges in 2.25:
    - Convert each path segment to a tube mesh with EDGE_RADIUS_M and Z_LIFT_EDGES
    - Color red, category 'path_edge'
    - Robustly accept multiple input shapes (Line/Base, dicts, tuples, polylines)
    """
    if not graph_objects and not path_lines:
        print(f"⚠️ No objects to commit for {level_name}.")
        return

    def _as_point_tuple(p):
        """Return (x,y,z) from a Speckle Point/Base/dict/tuple."""
        if p is None:
            return None
        # Speckle Base with x,y,(z)
        try:
            x = getattr(p, "x", None); y = getattr(p, "y", None); z = getattr(p, "z", None)
            if x is not None and y is not None:
                return (float(x), float(y), float(z or 0.0))
        except Exception:
            pass
        # dict-like {"x":..,"y":..,"z":..}
        try:
            x = p.get("x"); y = p.get("y"); z = p.get("z", 0.0)
            if x is not None and y is not None:
                return (float(x), float(y), float(z or 0.0))
        except Exception:
            pass
        # tuple/list [x,y,(z)]
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = p[0], p[1]
            z = p[2] if len(p) > 2 else 0.0
            return (float(x), float(y), float(z or 0.0))
        return None

    def _iter_segments(item):
        """
        Yield (x1,y1,z1,x2,y2,z2) segments from a path definition that might be:
        - Speckle Line/Base with .start/.end
        - dict with 'start'/'end'
        - list/tuple of points (polyline)
        - dict with 'points' or 'vertices' list
        """
        # 1) Speckle Line/Base style
        try:
            s = getattr(item, "start", None)
            e = getattr(item, "end",   None)
            if s is not None and e is not None:
                p1 = _as_point_tuple(s); p2 = _as_point_tuple(e)
                if p1 and p2:
                    yield (*p1, *p2)
                    return
        except Exception:
            pass

        # 2) dict with 'start'/'end'
        if isinstance(item, dict):
            s = item.get("start"); e = item.get("end")
            if s is not None and e is not None:
                p1 = _as_point_tuple(s); p2 = _as_point_tuple(e)
                if p1 and p2:
                    yield (*p1, *p2)
                    return
            # dict polyline variants
            for key in ("points", "vertices", "coords"):
                pts = item.get(key)
                if isinstance(pts, (list, tuple)) and len(pts) >= 2:
                    prev = _as_point_tuple(pts[0])
                    for nxt in pts[1:]:
                        p2 = _as_point_tuple(nxt)
                        if prev and p2:
                            yield (*prev, *p2)
                        prev = p2
                    return

        # 3) list/tuple polyline
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            # list of point-like entries
            prev = _as_point_tuple(item[0])
            for nxt in item[1:]:
                p2 = _as_point_tuple(nxt)
                if prev and p2:
                    yield (*prev, *p2)
                prev = p2
            return

        # nothing recognized → no segments
        return

    # --- Build path meshes exactly like edges ---
    path_objs = []
    if path_lines:
        if COMPAT_SPECKLE_225:
            for pl in path_lines:
                had_segment = False
                for x1, y1, z1, x2, y2, z2 in _iter_segments(pl):
                    if not (_finite3(x1, y1, z1) and _finite3(x2, y2, z2)):
                        continue
                    if x1 == x2 and y1 == y2 and z1 == z2:
                        continue
                    m = _mk_tube_mesh(x1, y1, z1, x2, y2, z2,
                                      r=EDGE_RADIUS_M, z_lift=Z_LIFT_EDGES)
                    if not m:
                        continue
                    m["category"] = "path_edge"
                    m["renderMaterial"] = MAT_RED
                    if level_name:
                        m["floor"] = level_name
                    path_objs.append(m)
                    had_segment = True
                if not had_segment:
                    # optional: fallback try to color a raw line if present
                    try:
                        pl["category"] = "path_edge"
                        pl["renderMaterial"] = MAT_RED
                        pl["displayStyle"] = {"lineWidth": 5}
                        if level_name:
                            pl["floor"] = level_name
                        path_objs.append(pl)
                    except Exception:
                        pass
        else:
            # modern viewer: lines with width are fine
            for pl in path_lines:
                try:
                    pl["category"] = "path_edge"
                    pl["renderMaterial"] = MAT_RED
                    pl["displayStyle"] = {"lineWidth": 5}
                    if level_name:
                        pl["floor"] = level_name
                    path_objs.append(pl)
                except Exception:
                    # try expanding polyline to multiple Line objects
                    for x1, y1, z1, x2, y2, z2 in _iter_segments(pl):
                        ln = Line(start=_pt(x1, y1, z1), end=_pt(x2, y2, z2), units="m")
                        ln["category"] = "path_edge"
                        ln["renderMaterial"] = MAT_RED
                        ln["displayStyle"] = {"lineWidth": 5}
                        if level_name:
                            ln["floor"] = level_name
                        path_objs.append(ln)

    # assemble and send
    base_obj = Base()
    if graph_objects:
        base_obj["graph_edges"] = graph_objects
    if path_objs:
        base_obj["paths"] = path_objs
    base_obj["name"] = f"Travel Distances – {level_name}"
    base_obj["units"] = "m"
    base_obj["level"] = level_name

    try:
        transport = ServerTransport(client=client, stream_id=stream_id)
        object_id = operations.send(base=base_obj, transports=[transport])

        # 🔀 ensure (or create) target branch, then commit to its slug
        branch_slug = _ensure_branch(
            client=client,
            stream_id=stream_id,
            branch_label=DEFAULT_PATHS_BRANCH_LABEL,
            description="Auto-generated branch for Fire & Life Safety graph outputs",
        )

        commit_id = client.commit.create(
            stream_id=stream_id,
            object_id=object_id,
            branch_name=branch_slug,  # ← use slug, not BRANCH_NAME
            message=f"Travel Distance Results – Floor: {level_name}"
        )
        print(f"✅ Commit created for {level_name} on '{branch_slug}'. Commit ID: {commit_id}")
    except Exception as e:
        print(f"❌ Failed to send object for {level_name}: {e}")
        return


# Keep the original graph_to_speckle_objects function unchanged
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