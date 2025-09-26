import os, sys, json, pickle, re
from pathlib import Path
import numpy as np

from specklepy.api.client import SpeckleClient
from specklepy.api.wrapper import StreamWrapper
from specklepy.transports.server import ServerTransport
from specklepy.objects.base import Base
from specklepy.api import operations
from specklepy.objects.units import get_units_from_string

from extract_elements import extract_elements_by_type
from code_compliance import compute_compliance_check, floor_fls_parameters
import code_compliance as cc
from send_utils_225 import send_model_to_speckle_per_floor
from speckle_credentials import SPECKLE_SERVER_URL, PROJECT_ID, MODEL_ID, SPECKLE_TOKEN_FLS

print(f"[RUNNING] run_fls_main from: {Path(__file__).resolve()}", flush=True)
print(f"[CWD] {Path.cwd()}", flush=True)

# ---------- Patch Base.units to tolerate junk strings ----------
_invalid_units = set()
def _safe_units_set(self, value):
    try:
        self.__dict__["units"] = get_units_from_string(value)
    except Exception:
        if value not in _invalid_units:
            with open("invalid_units_log.txt", "a", encoding="utf-8") as f:
                f.write(f"{value}\n")
            _invalid_units.add(value)
        self.__dict__["units"] = None
def _safe_units_get(self):
    return self.__dict__.get("units", None)
Base.units = property(fget=_safe_units_get, fset=_safe_units_set)

# ---------- Speckle auth ----------
client = SpeckleClient(host=SPECKLE_SERVER_URL)
client.authenticate_with_token(SPECKLE_TOKEN_FLS)
wrapper = StreamWrapper(f"{SPECKLE_SERVER_URL}/streams/{PROJECT_ID}/branches/main")

branch = client.branch.get(PROJECT_ID, MODEL_ID)
default_commit = branch.commits.items[-1] if branch.commits.items else None
transport = ServerTransport(client=client, stream_id=PROJECT_ID)
speckle_data = operations.receive(default_commit.referencedObject, transport)

# ---------- Extract elements ----------
elements_extracted = extract_elements_by_type(speckle_data.elements)

# ---------- Selected code PDF ----------
selected_pdf = os.getenv("SELECTED_CODE_PDF")
if not selected_pdf:
    print("❌ No PDF selected. Please select a code document from the UI before running this script.")
    sys.exit(1)
print(f"📘 Using selected PDF knowledge base: {selected_pdf}", flush=True)

# ---------- helpers ----------
def safe_json_value(val):
    if isinstance(val, (str, int, float, bool)) or val is None:
        return val
    if isinstance(val, np.generic):
        return val.item()
    if hasattr(val, "id"):
        return val.id
    try:
        return str(val)
    except Exception:
        return None

def normalize(s: str) -> str:
    return (s or "").strip().upper()

def canon_class(s: str) -> str:
    if not s:
        return ""
    t = str(s).strip()
    if t.lower().startswith("mixed"):
        return "MIXED"
    m = re.search(r"group\s+([A-Z](?:-[0-9])?)", t, flags=re.I)
    if m:
        return f"GROUP {m.group(1).upper()}"
    m2 = re.search(r"\b(A|B|E|F|H|I(?:-[0-9])?|M|R|S|U)(?:\s*[-–]\s*\d)?\b", t, flags=re.I)
    if m2:
        return f"GROUP {m2.group(0).upper().replace('–','-').replace('  ',' ')}"
    return t.upper()

def parse_olf(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val).replace(",", ".")
        m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
        return float(m.group(0)) if m else None
    except Exception:
        return None

# ---------- LangChain venv bridge ----------
def _langchain_python() -> str:
    p = os.environ.get("LANGCHAIN_VENV_PYTHON")
    if p and Path(p).exists():
        return p
    here = Path(__file__).resolve().parent
    for cand in [
        here / "langchain_venv" / "Scripts" / "python.exe",
        here / "langchain_venv" / "bin" / "python",
    ]:
        if cand.exists():
            return str(cand)
    return sys.executable

LANGCHAIN_PY = _langchain_python()

def _run_json_script(python_exe: str, script: str, payload) -> dict:
    import subprocess, json as _json
    env = os.environ.copy()
    env["SELECTED_CODE_PDF"] = selected_pdf
    cmd = [python_exe, script, _json.dumps(payload)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        print(f"[GPT-BRIDGE:ERROR] {script} failed rc={proc.returncode}", flush=True)
        print(proc.stdout, flush=True); print(proc.stderr, flush=True)
        return {}
    out = {}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            out = _json.loads(line)
            break
        except _json.JSONDecodeError:
            continue
    if not out:
        print(f"[GPT-BRIDGE:WARN] {script} produced no JSON", flush=True)
        print(proc.stdout, flush=True)
    return out

def classify_rooms_via_gpt(room_names):
    print(f"[GPT] classifying {len(room_names)} room(s) via subprocess...", flush=True)
    res = _run_json_script(LANGCHAIN_PY, "llm_classify.py", room_names)
    # keep raw text; we canonicalize only when needed
    return {normalize(k): (str(v).strip() if v else "Unknown") for k, v in res.items()}

def olf_rooms_via_gpt(room_names):
    print(f"[GPT] OLF for {len(room_names)} room(s) via subprocess...", flush=True)
    res = _run_json_script(LANGCHAIN_PY, "extract_olf.py", room_names)
    out = {}
    for k, v in res.items():
        try:
            val, unit = v
            num = parse_olf(val)
            if num is not None:
                out[normalize(k)] = [float(num), str(unit)]
        except Exception:
            num = parse_olf(v)
            if num is not None:
                out[normalize(k)] = [float(num), ""]
    return out

def max_occ_by_class_via_gpt(classes):
    classes = [c for c in (canon_class(c) for c in classes) if c and c != "MIXED"]
    print(f"[GPT] max occupancy for {len(classes)} class(es) via subprocess...", flush=True)
    res = _run_json_script(LANGCHAIN_PY, "extract_max_occupancy.py", classes)
    out = {}
    for k, v in res.items():
        ck = canon_class(k)
        if not ck:
            continue
        try:
            out[ck] = int(v) if isinstance(v, (int, float, np.integer)) else int(float(v))
        except Exception:
            pass
    return out

# ---------- simple cache (NORMALIZED KEYS) ----------
CACHE_PATH = Path("cached_data") / "classification_index.json"

def _normalize_index(raw: dict) -> dict:
    """Return a new dict with UPPERCASE keys; keep values as-is."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        out[normalize(k)] = v
    return out

def _load_cache():
    if not CACHE_PATH.exists():
        return {}
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        idx = _normalize_index(raw)
        # Debug: show a tiny sample of normalized keys
        sample = list(idx.keys())[:5]
        print(f"[CACHE] loaded {len(idx)} keys (normalized). sample={sample}", flush=True)
        return idx
    except Exception:
        print("[WARN] cache read failed — starting fresh", flush=True)
        return {}

def _save_cache(index: dict):
    # Always save normalized keys for consistency across runs/tools
    norm = _normalize_index(index or {})
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(norm, f, indent=2)

def resolve_all_parameters(room_names):
    """Return { ROOM_UPPER: {classification, max_occupancy, olf} } with cache+GPT, robust key handling."""
    idx = _load_cache()  # cache keys expected normalized (UPPER/trim)
    orig_to_norm = [(r, normalize(r)) for r in room_names if (r or "").strip()]
    room_keys = [rk for _, rk in orig_to_norm]
    print(f"[PRELOAD] rooms={len(room_keys)}", flush=True)

    def _get_class(rk: str) -> str:
        v = idx.get(rk, {}).get("classification")
        return v if isinstance(v, str) else None

    def _canon_good(cls: str) -> bool:
        return isinstance(cls, str) and canon_class(cls) not in ("", "UNKNOWN")

    def _extract_cls(val):
        """Accept either a string or a dict with 'classification'."""
        if isinstance(val, str):
            return val.strip()
        if isinstance(val, dict):
            c = val.get("classification")
            if isinstance(c, str):
                return c.strip()
        return None

    def _set_class(rk: str, cls_val):
        # flatten possibly-dict payload
        cls_clean = _extract_cls(cls_val)
        if not _canon_good(cls_clean):
            return
        cur = _get_class(rk)
        if _canon_good(cur):
            return  # don't overwrite a good one
        idx.setdefault(rk, {})["classification"] = cls_clean

    def _set_olf(rk: str, olf_val):
        idx.setdefault(rk, {})["olf"] = olf_val

    have_class = sum(1 for rk in room_keys if _canon_good(_get_class(rk)))
    have_olf   = sum(1 for rk in room_keys if "olf" in idx.get(rk, {}))
    classes_seen = {canon_class(v.get("classification","")) for v in idx.values() if v.get("classification")}
    classes_seen.discard(""); classes_seen.discard("UNKNOWN")
    print(f"[PRELOAD] classifications fetched: {have_class}", flush=True)
    print(f"[PRELOAD] OLF fetched: {have_olf}", flush=True)
    print(f"[PRELOAD] max-occupancy fetched (by class): {len(classes_seen)}", flush=True)

    # 1) Missing classifications
    miss_cls = []
    for _, rk in orig_to_norm:
        cur = _get_class(rk)
        if not _canon_good(cur):
            miss_cls.append(rk)
    if miss_cls:
        want_originals = [orig for (orig, rk) in orig_to_norm if rk in miss_cls]
        got = classify_rooms_via_gpt(want_originals) or {}
        got_norm = { normalize(k): v for (k, v) in got.items() if (k or "").strip() }
        updated = 0
        for (orig, rk) in orig_to_norm:
            if rk in miss_cls:
                before = _get_class(rk)
                _set_class(rk, got_norm.get(rk))
                after = _get_class(rk)
                if _canon_good(after) and after != before:
                    updated += 1
        print(f"[MERGE] classifications updated: {updated}/{len(miss_cls)}", flush=True)
        if updated == 0:
            # Show a sample of what came back to confirm shape
            sample_vals = {k: got_norm[k] for k in list(got_norm.keys())[:3]}
            print(f"[MERGE:WARN] No usable classifications. Sample payload: {sample_vals}", flush=True)

    # 2) Missing OLF
    miss_olf = [rk for rk in room_keys if "olf" not in idx.get(rk, {})]
    if miss_olf:
        want_originals = [orig for (orig, rk) in orig_to_norm if rk in miss_olf]
        got_olf = olf_rooms_via_gpt(want_originals) or {}
        got_olf_norm = { normalize(k): v for (k, v) in got_olf.items() if (k or "").strip() }
        updated = 0
        for (orig, rk) in orig_to_norm:
            if rk in miss_olf and rk in got_olf_norm:
                _set_olf(rk, got_olf_norm[rk])
                updated += 1
        print(f"[MERGE] OLF updated: {updated}/{len(miss_olf)}", flush=True)

    # 3) Per-class max occupancy (same as before)
    class_to_max = {}
    for v in idx.values():
        c = canon_class(v.get("classification",""))
        if c and isinstance(v.get("max_occupancy"), (int, float)):
            class_to_max[c] = int(v["max_occupancy"])

    needed_classes = set()
    for rk in room_keys:
        c = canon_class(idx.get(rk, {}).get("classification", ""))
        if c and c not in ("UNKNOWN", "MIXED") and c not in class_to_max:
            needed_classes.add(c)

    if needed_classes:
        got_max = max_occ_by_class_via_gpt(sorted(needed_classes)) or {}
        got_max_norm = { canon_class(k): int(v) for (k, v) in got_max.items() if isinstance(v, (int, float)) }
        class_to_max.update(got_max_norm)
        print(f"[MERGE] max-occupancy classes updated: {len(got_max_norm)}/{len(needed_classes)}", flush=True)

    for rk in room_keys:
        c = canon_class(idx.get(rk, {}).get("classification", ""))
        if c in class_to_max:
            idx.setdefault(rk, {})["max_occupancy"] = int(class_to_max[c])

    _save_cache(idx)

    out = {}
    for rk in room_keys:
        v = idx.get(rk, {})
        out[rk] = {
            "classification": v.get("classification", "Unknown"),
            "max_occupancy": v.get("max_occupancy"),
            "olf": v.get("olf") if "olf" in v else None
        }

    # refresh globals
    global classification_results, olf_results, max_occupancy_results
    classification_results = {rk: out[rk]["classification"] for rk in out}
    olf_results = {rk: out[rk]["olf"] for rk in out if out[rk]["olf"] is not None}
    max_occupancy_results = {rk: {"max_occupancy": out[rk]["max_occupancy"]}
                             for rk in out if out[rk]["max_occupancy"] is not None}

    have_class = sum(1 for rk in room_keys if out[rk]["classification"] and out[rk]["classification"] != "Unknown")
    have_olf   = sum(1 for rk in room_keys if out[rk]["olf"])
    have_max   = sum(1 for rk in room_keys if out[rk]["max_occupancy"] is not None)
    print(f"[PRELOAD] loaded -> classifications:{have_class}/{len(room_keys)}, olf:{have_olf}, max-occ-rooms:{have_max}", flush=True)

    sample_ok = [rk for rk in room_keys if out[rk]["classification"] and out[rk]["classification"] != "Unknown"][:5]
    if sample_ok:
        print(f"[DEBUG] sample classified rooms: {[(rk, out[rk]['classification']) for rk in sample_ok]}", flush=True)
    else:
        print("[DEBUG] no classified rooms after merge; check GPT bridge payload format.", flush=True)

    return out




def sync_cc_globals(room_names, resolved):
    """Keep code_compliance module-level mappings in sync."""
    cc.classification_results = {
        rn: (resolved.get(normalize(rn), {}).get("classification") or "Unknown")
        for rn in room_names
    }
    cc.olf_results = {}
    for rn in room_names:
        entry = resolved.get(normalize(rn), {}).get("olf")
        if entry:
            try:
                val, unit = entry
                num = parse_olf(val)
                if num is not None:
                    cc.olf_results[rn] = {"olf": float(num), "unit": str(unit)}
            except Exception:
                num = parse_olf(entry)
                if num is not None:
                    cc.olf_results[rn] = {"olf": float(num), "unit": ""}

    cc.max_occupancy_results = {}
    for rn in room_names:
        mo = resolved.get(normalize(rn), {}).get("max_occupancy")
        if isinstance(mo, (int, float, np.integer)):
            cc.max_occupancy_results[rn] = {"max_occupancy": int(mo)}

# ---------- per-floor loop ----------
graph_dir = "graphs"; path_dir = "paths"

for graph_file in sorted(os.listdir(graph_dir)):
    if not graph_file.endswith(".pkl"):
        continue

    level_name = graph_file.split("_")[-1].replace(".pkl", "")
    print(f"\n📘 Running FLS Check for Floor: {level_name}", flush=True)

    with open(Path(graph_dir, graph_file), "rb") as f:
        G_floor = pickle.load(f)
    with open(Path(path_dir, f"paths_{level_name}.pkl"), "rb") as f:
        paths = pickle.load(f)

    matched_floor = next(
        (f for f in elements_extracted["Floors"]
         if getattr(getattr(f, "level", None), "name", None) == level_name),
        None
    )
    rooms_on_level = [
        r for r in elements_extracted["Rooms"]
        if getattr(getattr(r, "level", None), "name", None) == level_name
    ]

    room_names = sorted({(getattr(r, "name", "") or "").strip()
                         for r in rooms_on_level if (getattr(r, "name", "") or "").strip()})
    resolved = resolve_all_parameters(room_names)

    # feed globals
    sync_cc_globals(room_names, resolved)

    # patch objects (for Speckle display)
    for room in rooms_on_level:
        nm = (getattr(room, "name", "") or "").strip()
        info = resolved.get(normalize(nm), {})
        cls = info.get("classification") or "Unknown"
        room["buildingClassification"] = cls

        olf_entry = info.get("olf")
        if olf_entry:
            try:
                val, unit = olf_entry
            except Exception:
                val, unit = parse_olf(olf_entry), ""
            if val is not None:
                room["occupancyLoadFactor"] = {"olf": float(val), "unit": unit}

        mo = info.get("max_occupancy")
        if isinstance(mo, (int, float, np.integer)):
            room["maximumOccupantLoad"] = int(mo)

    fls_parameters = []
    if matched_floor:
        fls_parameters.append(floor_fls_parameters(matched_floor, rooms_on_level, level_name=level_name))

    compliance_results = compute_compliance_check(
        paths,
        graph=G_floor,
        all_rooms=rooms_on_level,
        all_floors=[matched_floor] if matched_floor else None,
        max_occupancy_results=cc.max_occupancy_results,
    )

    fls_parameters += [
        res["fls_parameters"] if isinstance(res, dict) else res
        for res in compliance_results
        if (isinstance(res, dict) and "fls_parameters" in res) or isinstance(res, Base)
    ]

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
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    print(f"📝 Compliance report saved: {report_path}", flush=True)

    print("\n🚀 Sending the following rooms to Speckle:", flush=True)
    for obj in fls_parameters:
        name = getattr(obj, "name", "?")
        try:
            classification = obj["buildingClassification"]
        except Exception:
            classification = "❌"
        print(f"🧱 {name} | buildingClassification = {classification}", flush=True)

    if fls_parameters:
        send_model_to_speckle_per_floor(
            fls_parameters, client, PROJECT_ID,
            level_name=level_name,
            message_prefix="Fire Safety Compliance – Check",
        )

print("\n✅ FLS Parameters + Compliance Review Complete.", flush=True)
