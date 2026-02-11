# # run_fls_main.py — ProgramData-aware (graphs/paths/cache/logs/reports) + commit ALL rooms (graph-based) and overlay compliance
# import os, sys, json, pickle, re
# from pathlib import Path
# from collections import defaultdict, Counter
# import numpy as np

# from specklepy.api.client import SpeckleClient
# from specklepy.transports.server import ServerTransport
# from specklepy.objects.base import Base
# from specklepy.api import operations
# from specklepy.objects.units import get_units_from_string

# from extract_elements import extract_elements_by_type
# from code_compliance import compute_compliance_check, floor_fls_parameters
# import code_compliance as cc
# from send_utils_225 import send_model_to_speckle_per_floor
# from speckle_credentials import SPECKLE_SERVER_URL, PROJECT_ID as PROJECT_ID_DEFAULT, MODEL_ID as MODEL_ID_DEFAULT


# # ========= Env / IDs =========
# PROJECT_ID = (os.getenv("PROJECT_ID") or PROJECT_ID_DEFAULT).strip()
# MODEL_ID   = ((os.getenv("MODEL_ID") or MODEL_ID_DEFAULT).strip()).split("@", 1)[0]
# SPECKLE_USER_TOKEN = os.getenv("SPECKLE_USER_TOKEN")

# if not PROJECT_ID:
#     print("❌ PROJECT_ID is not set", flush=True); sys.exit(1)
# if not MODEL_ID:
#     print("❌ MODEL_ID is not set", flush=True); sys.exit(1)
# if not SPECKLE_USER_TOKEN:
#     print("❌ Missing SPECKLE_USER_TOKEN in environment (user must authenticate)", flush=True); sys.exit(1)


# # ========= ProgramData layout =========
# def _sanitize(s: str) -> str:
#     return re.sub(r"[^A-Za-z0-9_.-]+", "_", s or "")

# PROJECT_KEY = f"{_sanitize(PROJECT_ID)}_{_sanitize(MODEL_ID)}"
# BASE_PD = Path(r"C:\ProgramData\VeriFire") / PROJECT_KEY

# GRAPHS_DIR  = BASE_PD / "graphs"
# PATHS_DIR   = BASE_PD / "paths"
# CACHE_DIR   = BASE_PD / "cache"
# LOGS_DIR    = BASE_PD / "invalid_units"
# REPORTS_DIR = BASE_PD / "reports"

# for p in (BASE_PD, GRAPHS_DIR, PATHS_DIR, CACHE_DIR, LOGS_DIR, REPORTS_DIR):
#     p.mkdir(parents=True, exist_ok=True)

# print(f"[PD] BASE_PD={BASE_PD}")
# print(f"[PD] GRAPHS_DIR={GRAPHS_DIR}")
# print(f"[PD] PATHS_DIR={PATHS_DIR}")
# print(f"[PD] CACHE_DIR={CACHE_DIR}")
# print(f"[PD] LOGS_DIR={LOGS_DIR}")
# print(f"[PD] REPORTS_DIR={REPORTS_DIR}")


# # ========= Patch Base.units (tolerant) =========
# _invalid_units = set()
# _INVALID_UNITS_LOG = LOGS_DIR / "invalid_units_log.txt"

# def _safe_units_set(self, value):
#     try:
#         self.__dict__["units"] = get_units_from_string(value)
#     except Exception:
#         try:
#             if value not in _invalid_units:
#                 with _INVALID_UNITS_LOG.open("a", encoding="utf-8") as f:
#                     f.write(f"{value}\n")
#                 _invalid_units.add(value)
#         except Exception:
#             pass
#         self.__dict__["units"] = None

# def _safe_units_get(self):
#     return self.__dict__.get("units", None)

# Base.units = property(fget=_safe_units_get, fset=_safe_units_set)


# # ========= Speckle auth & receive (✅ newest commit on branch) =========
# client = SpeckleClient(host=SPECKLE_SERVER_URL)
# client.authenticate_with_token(SPECKLE_USER_TOKEN)

# branch = client.branch.get(PROJECT_ID, MODEL_ID)
# if not branch:
#     print(f"❌ Branch (model) not found: {MODEL_ID}", flush=True); sys.exit(1)

# # Prefer the branch page listing (often newest-first)
# default_commit = None
# try:
#     if branch.commits and branch.commits.items:
#         # Many servers return newest-first already
#         default_commit = branch.commits.items[0]
# except Exception:
#     default_commit = None

# # Fallback to full commit list (ensure newest-first by createdAt)
# if not default_commit:
#     commits = client.commit.list(PROJECT_ID, MODEL_ID)
#     if not commits:
#         print("❌ No commits found on the specified branch.", flush=True); sys.exit(1)
#     commits_sorted = sorted(
#         commits,
#         key=lambda c: getattr(c, "createdAt", "") or "",
#         reverse=True
#     )
#     default_commit = commits_sorted[0]

# print(f"🧭 Using commit: {default_commit.id} | message: {getattr(default_commit,'message','').strip() or '(no message)'}", flush=True)

# transport = ServerTransport(client=client, stream_id=PROJECT_ID)
# speckle_data = operations.receive(default_commit.referencedObject, transport)

# # Extract categorized elements
# elements_extracted = extract_elements_by_type(speckle_data.elements)

# def build_level_alias_map_from(elements_lists) -> dict:
#     """
#     Build a mapping that normalizes any '*-CL' (or variants) to the corresponding '*-FFL'.
#     Examples handled:
#       "P-03-3RD-CL"    -> "P-03-3RD-FFL"
#       "Level 3 - CL"   -> "Level 3 - FFL"
#       "LEVEL 5 (CL)"   -> "LEVEL 5 (FFL)"
#       "L2_CL"          -> "L2_FFL"
#     If an FFL twin doesn't exist, we still map to the synthesized FFL key so that
#     grouping collapses under FFL.
#     """
#     level_names = set()

#     def _collect(obj):
#         lvl = getattr(obj, "level", None)
#         name = getattr(lvl, "name", None) if lvl else None
#         if isinstance(name, str) and name.strip():
#             level_names.add(name.strip())

#     # Collect level names from all categories
#     for lst in elements_lists:
#         for obj in lst:
#             _collect(obj)

#     alias = {}
#     for name in list(level_names):
#         n = name.strip()

#         # Normalize common CL suffix forms
#         # Case 1: "...-CL"
#         if n.endswith("-CL"):
#             alias[n] = n[:-3] + "-FFL"
#             continue

#         # Case 2: "... - CL"
#         if n.endswith(" - CL"):
#             alias[n] = n[:-5] + " - FFL"
#             continue

#         # Case 3: "... (CL)"
#         if n.endswith("(CL)"):
#             alias[n] = n[:-4] + "(FFL)"
#             continue

#         # Case 4: "..._CL"
#         if n.endswith("_CL"):
#             alias[n] = n[:-3] + "_FFL"
#             continue

#         # Conservative generic: lone trailing " CL"
#         if n.endswith(" CL"):
#             alias[n] = n[:-3] + " FFL"
#             continue

#     return alias


# # Build a CL→FFL alias map so per-floor commits include elements referenced to either level naming scheme.
# _level_lists = [lst for lst in (elements_extracted or {}).values() if isinstance(lst, list)]
# LEVEL_ALIAS_MAP = build_level_alias_map_from(_level_lists) if _level_lists else {}
# if LEVEL_ALIAS_MAP:
#     print(f"🔁 Level alias normalization enabled ({len(LEVEL_ALIAS_MAP)} mapping(s)).", flush=True)
# else:
#     print("ℹ️ No level alias mappings detected (continuing with strict level names).", flush=True)

# # ========= Selected code PDF =========
# selected_pdf = os.getenv("SELECTED_CODE_PDF")
# if not selected_pdf:
#     print("❌ No PDF selected. Please select a code document from the UI before running this script.")
#     sys.exit(1)
# print(f"📘 Using selected PDF knowledge base: {selected_pdf}", flush=True)


# # ========= Helpers =========
# def safe_json_value(val):
#     if isinstance(val, (str, int, float, bool)) or val is None:
#         return val
#     if isinstance(val, np.generic):
#         return val.item()
#     if hasattr(val, "id"):
#         return val.id
#     try:
#         return str(val)
#     except Exception:
#         return None

# def normalize_room_name(s: str) -> str:
#     return re.sub(r"[^A-Z0-9 ]+", "", (s or "").strip().upper())

# def canon_class(s: str) -> str:
#     if not s:
#         return ""
#     t = str(s).strip()
#     if t.lower().startswith("mixed"):
#         return "MIXED"
#     m = re.search(r"group\s+([A-Z](?:-[0-9])?)", t, flags=re.I)
#     if m:
#         return f"GROUP {m.group(1).upper()}"
#     m2 = re.search(r"\b(A|B|E|F|H|I(?:-[0-9])?|M|R|S|U)(?:\s*[-–]\s*\d)?\b", t, flags=re.I)
#     if m2:
#         return f"GROUP {m2.group(0).upper().replace('–','-').replace('  ',' ')}"
#     return t.upper()

# def parse_olf(val):
#     if val is None:
#         return None
#     if isinstance(val, (int, float)):
#         return float(val)
#     try:
#         s = str(val).replace(",", ".")
#         m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
#         return float(m.group(0)) if m else None
#     except Exception:
#         return None



# # ========= Level filtering helpers (for per-floor commits that include all elements on the same level) =========
# _LEVEL_KEYS = (
#     "level", "baseLevel", "referenceLevel", "startLevel", "endLevel",
#     "hostLevel", "constraintLevel", "scheduleLevel"
# )

# def _level_name_from_obj(obj):
#     """Best-effort: extract level name from a Speckle/Revit element."""
#     if obj is None:
#         return None

#     # Common case: obj.level.name
#     try:
#         lvl = getattr(obj, "level", None)
#         name = getattr(lvl, "name", None)
#         if name:
#             return str(name)
#     except Exception:
#         pass

#     # Try a few other known keys that show up in Revit conversions
#     for k in _LEVEL_KEYS:
#         try:
#             lvl = getattr(obj, k, None)
#         except Exception:
#             lvl = None
#         if lvl is None:
#             # Base sometimes stores members dict-style
#             try:
#                 lvl = obj[k]
#             except Exception:
#                 lvl = None
#         if lvl is None:
#             continue
#         try:
#             name = getattr(lvl, "name", None)
#             if name:
#                 return str(name)
#         except Exception:
#             pass
#         try:
#             # lvl might be a dict-like
#             name = lvl.get("name") if isinstance(lvl, dict) else None
#             if name:
#                 return str(name)
#         except Exception:
#             pass

#     return None

# def _normalize_level_name(name, level_alias_map=None):
#     """Normalize a level name (strip + optional CL→FFL aliasing)."""
#     if name is None:
#         return None
#     n = str(name).strip()
#     if not n:
#         return None
#     if level_alias_map:
#         try:
#             n = str(level_alias_map.get(n, n)).strip()
#         except Exception:
#             pass
#     return n

# def _matches_level(obj, level_name: str, level_alias_map=None) -> bool:
#     ln = _normalize_level_name(_level_name_from_obj(obj), level_alias_map)
#     tgt = _normalize_level_name(level_name, level_alias_map)
#     return bool(ln) and bool(tgt) and ln == tgt

# def collect_elements_for_level(elements_by_type: dict, level_name: str, level_alias_map=None) -> list:
#     """Collect ALL extracted elements that belong to a given level (best-effort).

#     If level_alias_map is provided, both element level names and the target level are normalized
#     (mirrors run_grid_main alias map: *-CL variants collapse under the corresponding *-FFL).
#     """
#     out = []
#     for _typ, lst in (elements_by_type or {}).items():
#         if not lst:
#             continue
#         for e in lst:
#             try:
#                 if _matches_level(e, level_name, level_alias_map):
#                     out.append(e)
#             except Exception:
#                 continue
#     return out
# # ========= GPT bridge (external scripts) =========
# def _langchain_python() -> str:
#     p = os.environ.get("LANGCHAIN_VENV_PYTHON")
#     if p and Path(p).exists():
#         return p
#     here = Path(__file__).resolve().parent
#     for cand in [
#         here / "langchain_venv" / "Scripts" / "python.exe",
#         here / "langchain_venv" / "bin" / "python",
#     ]:
#         if cand.exists():
#             return str(cand)
#     return sys.executable

# LANGCHAIN_PY = _langchain_python()

# def _run_json_script(python_exe: str, script: str, payload) -> dict:
#     import subprocess, json as _json
#     env = os.environ.copy()
#     env["SELECTED_CODE_PDF"] = selected_pdf
#     env.setdefault("VECTOR_DB_DIR", r"C:\ProgramData\VeriFire\vector_db")
#     if "DEPLOYMENT" not in env:
#         env["DEPLOYMENT"] = (
#             env.get("AZURE_OPENAI_CHAT_DEPLOYMENT")
#             or env.get("OPENAI_DEPLOYMENT")
#             or env.get("CHAT_DEPLOYMENT")
#             or ""
#         )
#     cmd = [python_exe, script, _json.dumps(payload)]
#     proc = subprocess.run(cmd, capture_output=True, text=True, env=env)

#     if proc.stderr.strip():
#         print(proc.stderr, flush=True)

#     if proc.returncode != 0:
#         print(f"[GPT-BRIDGE:ERROR] {script} failed rc={proc.returncode}", flush=True)
#         print(proc.stdout, flush=True)
#         return {}

#     out = {}
#     for line in reversed(proc.stdout.strip().splitlines()):
#         try:
#             out = _json.loads(line)
#             break
#         except _json.JSONDecodeError:
#             continue
#     if not out:
#         print(f"[GPT-BRIDGE:WARN] {script} produced no JSON", flush=True)
#         if proc.stdout.strip():
#             print(proc.stdout, flush=True)
#     return out

# def classify_rooms_via_gpt(room_names):
#     print(f"[GPT] classifying {len(room_names)} room(s) via subprocess...", flush=True)
#     res = _run_json_script(LANGCHAIN_PY, "llm_classify.py", room_names)
#     return {normalize_room_name(k): (str(v).strip() if v else "Unknown") for k, v in res.items()}

# def olf_rooms_via_gpt(room_names):
#     print(f"[GPT] OLF for {len(room_names)} room(s) via subprocess...", flush=True)
#     res = _run_json_script(LANGCHAIN_PY, "extract_olf.py", room_names)
#     out = {}
#     for k, v in res.items():
#         try:
#             val, unit = v
#             num = parse_olf(val)
#             if num is not None:
#                 out[normalize_room_name(k)] = [float(num), str(unit)]
#         except Exception:
#             num = parse_olf(v)
#             if num is not None:
#                 out[normalize_room_name(k)] = [float(num), ""]
#     return out

# def max_occ_by_class_via_gpt(classes):
#     classes = [c for c in (canon_class(c) for c in classes) if c and c != "MIXED"]
#     print(f"[GPT] max occupancy for {len(classes)} class(es) via subprocess...", flush=True)
#     res = _run_json_script(LANGCHAIN_PY, "extract_max_occupancy.py", classes)
#     out = {}
#     for k, v in res.items():
#         ck = canon_class(k)
#         if not ck:
#             continue
#         try:
#             out[ck] = int(v) if isinstance(v, (int, float, np.integer)) else int(float(v))
#         except Exception:
#             pass
#     return out


# # ========= Simple classification cache =========
# CACHE_PATH = CACHE_DIR / "classification_index.json"

# def _normalize_index(raw: dict) -> dict:
#     if not isinstance(raw, dict):
#         return {}
#     out = {}
#     for k, v in raw.items():
#         out[normalize_room_name(k)] = v
#     return out

# def _load_cache():
#     if not CACHE_PATH.exists():
#         return {}
#     try:
#         with CACHE_PATH.open("r", encoding="utf-8") as f:
#             raw = json.load(f)
#         idx = _normalize_index(raw)
#         sample = list(idx.keys())[:5]
#         print(f"[CACHE] loaded {len(idx)} keys (normalized). sample={sample}", flush=True)
#         return idx
#     except Exception:
#         print("[WARN] cache read failed — starting fresh", flush=True)
#         return {}

# def _save_cache(index: dict):
#     norm = _normalize_index(index or {})
#     CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
#     with CACHE_PATH.open("w", encoding="utf-8") as f:
#         json.dump(norm, f, indent=2)

# def resolve_all_parameters(room_names):
#     idx = _load_cache()
#     orig_to_norm = [(r, normalize_room_name(r)) for r in room_names if (r or "").strip()]
#     room_keys = [rk for _, rk in orig_to_norm]
#     print(f"[PRELOAD] rooms={len(room_keys)}", flush=True)

#     def _get_class(rk: str) -> str:
#         v = idx.get(rk, {}).get("classification")
#         return v if isinstance(v, str) else None

#     def _canon_good(cls: str) -> bool:
#         return isinstance(cls, str) and canon_class(cls) not in ("", "UNKNOWN")

#     def _extract_cls(val):
#         if isinstance(val, str):
#             return val.strip()
#         if isinstance(val, dict):
#             c = val.get("classification")
#             if isinstance(c, str):
#                 return c.strip()
#         return None

#     def _set_class(rk: str, cls_val):
#         cls_clean = _extract_cls(cls_val)
#         if not _canon_good(cls_clean):
#             return
#         cur = _get_class(rk)
#         if _canon_good(cur):
#             return
#         idx.setdefault(rk, {})["classification"] = cls_clean

#     def _set_olf(rk: str, olf_val):
#         idx.setdefault(rk, {})["olf"] = olf_val

#     have_class = sum(1 for rk in room_keys if _canon_good(_get_class(rk)))
#     have_olf   = sum(1 for rk in room_keys if "olf" in idx.get(rk, {}))
#     classes_seen = {canon_class(v.get("classification","")) for v in idx.values() if v.get("classification")}
#     classes_seen.discard(""); classes_seen.discard("UNKNOWN")
#     print(f"[PRELOAD] classifications fetched: {have_class}", flush=True)
#     print(f"[PRELOAD] OLF fetched: {have_olf}", flush=True)
#     print(f"[PRELOAD] max-occupancy fetched (by class): {len(classes_seen)}", flush=True)

#     miss_cls = []
#     for _, rk in orig_to_norm:
#         cur = _get_class(rk)
#         if not _canon_good(cur):
#             miss_cls.append(rk)
#     if miss_cls:
#         want_originals = [orig for (orig, rk) in orig_to_norm if rk in miss_cls]
#         got = classify_rooms_via_gpt(want_originals) or {}
#         got_norm = { normalize_room_name(k): v for (k, v) in got.items() if (k or "").strip() }
#         updated = 0
#         for (orig, rk) in orig_to_norm:
#             if rk in miss_cls:
#                 before = _get_class(rk)
#                 _set_class(rk, got_norm.get(rk))
#                 after = _get_class(rk)
#                 if _canon_good(after) and after != before:
#                     updated += 1
#         print(f"[MERGE] classifications updated: {updated}/{len(miss_cls)}", flush=True)
#         if updated == 0:
#             sample_vals = {k: got_norm[k] for k in list(got_norm.keys())[:3]}
#             print(f"[MERGE:WARN] No usable classifications. Sample payload: {sample_vals}", flush=True)

#     miss_olf = [rk for rk in room_keys if "olf" not in idx.get(rk, {})]
#     if miss_olf:
#         want_originals = [orig for (orig, rk) in orig_to_norm if rk in miss_olf]
#         got_olf = olf_rooms_via_gpt(want_originals) or {}
#         got_olf_norm = { normalize_room_name(k): v for (k, v) in got_olf.items() if (k or "").strip() }
#         updated = 0
#         for (orig, rk) in orig_to_norm:
#             if rk in miss_olf and rk in got_olf_norm:
#                 _set_olf(rk, got_olf_norm[rk])
#                 updated += 1
#         print(f"[MERGE] OLF updated: {updated}/{len(miss_olf)}", flush=True)

#     class_to_max = {}
#     for v in idx.values():
#         c = canon_class(v.get("classification",""))
#         if c and isinstance(v.get("max_occupancy"), (int, float)):
#             class_to_max[c] = int(v["max_occupancy"])

#     needed_classes = set()
#     for rk in room_keys:
#         c = canon_class(idx.get(rk, {}).get("classification", ""))
#         if c and c not in ("UNKNOWN", "MIXED") and c not in class_to_max:
#             needed_classes.add(c)

#     if needed_classes:
#         got_max = max_occ_by_class_via_gpt(sorted(needed_classes)) or {}
#         got_max_norm = { canon_class(k): int(v) for (k, v) in got_max.items() if isinstance(v, (int, float)) }
#         class_to_max.update(got_max_norm)
#         print(f"[MERGE] max-occupancy classes updated: {len(got_max_norm)}/{len(needed_classes)}", flush=True)

#     for rk in room_keys:
#         c = canon_class(idx.get(rk, {}).get("classification", ""))
#         if c in class_to_max:
#             idx.setdefault(rk, {})["max_occupancy"] = int(class_to_max[c])

#     _save_cache(idx)

#     out = {}
#     for rk in room_keys:
#         v = idx.get(rk, {})
#         out[rk] = {
#             "classification": v.get("classification", "Unknown"),
#             "max_occupancy": v.get("max_occupancy"),
#             "olf": v.get("olf") if "olf" in v else None
#         }

#     # refresh globals (for code_compliance)
#     global classification_results, olf_results, max_occupancy_results
#     classification_results = {rk: out[rk]["classification"] for rk in out}
#     olf_results = {rk: out[rk]["olf"] for rk in out if out[rk]["olf"] is not None}
#     max_occupancy_results = {rk: {"max_occupancy": out[rk]["max_occupancy"]}
#                              for rk in out if out[rk]["max_occupancy"] is not None}

#     have_class = sum(1 for rk in room_keys if out[rk]["classification"] and out[rk]["classification"] != "Unknown")
#     have_olf   = sum(1 for rk in room_keys if out[rk]["olf"])
#     have_max   = sum(1 for rk in room_keys if out[rk]["max_occupancy"] is not None)
#     print(f"[PRELOAD] loaded -> classifications:{have_class}/{len(room_keys)}, olf:{have_olf}, max-occ-rooms:{have_max}", flush=True)

#     sample_ok = [rk for rk in room_keys if out[rk]["classification"] and out[rk]["classification"] != "Unknown"][:5]
#     if sample_ok:
#         print(f"[DEBUG] sample classified rooms: {[(rk, out[rk]['classification']) for rk in sample_ok]}", flush=True)
#     else:
#         print("[DEBUG] no classified rooms after merge; check GPT bridge payload format.", flush=True)

#     return out

# def sync_cc_globals(room_names, resolved):
#     cc.classification_results = {
#         rn: (resolved.get(normalize_room_name(rn), {}).get("classification") or "Unknown")
#         for rn in room_names
#     }
#     cc.olf_results = {}
#     for rn in room_names:
#         entry = resolved.get(normalize_room_name(rn), {}).get("olf")
#         if entry:
#             try:
#                 val, unit = entry
#                 num = parse_olf(val)
#                 if num is not None:
#                     cc.olf_results[rn] = {"olf": float(num), "unit": str(unit)}
#             except Exception:
#                 num = parse_olf(entry)
#                 if num is not None:
#                     cc.olf_results[rn] = {"olf": float(num), "unit": ""}

#     cc.max_occupancy_results = {}
#     for rn in room_names:
#         mo = resolved.get(normalize_room_name(rn), {}).get("max_occupancy")
#         if isinstance(mo, (int, float, np.integer)):
#             cc.max_occupancy_results[rn] = {"max_occupancy": int(mo)}


# # ========= ID alias normalization helpers =========
# def _s(v): 
#     return str(v).strip() if v is not None else ""

# def _norm(s: str) -> str:
#     return re.sub(r"[^A-Z0-9 ]+", "", (s or "").strip().upper())

# def _nk(name, level=None):
#     name = _norm(name)
#     level = _norm(level) if level else ""
#     return f"{level}::{name}" if level else name

# def _build_alias_maps(G):
#     alias_to_primary = {}
#     if isinstance(G.graph.get("alias_to_primary"), dict):
#         for a, p in G.graph["alias_to_primary"].items():
#             alias_to_primary[_s(a)] = _s(p)
#     if isinstance(G.graph.get("room_id_aliases"), dict):
#         for primary, aliases in G.graph["room_id_aliases"].items():
#             p = _s(primary)
#             alias_to_primary[p] = p
#             try:
#                 for a in (aliases or []):
#                     alias_to_primary[_s(a)] = p
#             except Exception:
#                 pass
#     return alias_to_primary

# def _map_to_primary(x, alias_to_primary):
#     x = _s(x)
#     return alias_to_primary.get(x, x)


# # ========= Per-floor loop (pickle graph/paths) =========
# for graph_file in sorted(p.name for p in GRAPHS_DIR.glob("G_*.pkl")):
#     level_name = graph_file.split("_")[-1].replace(".pkl", "")
#     print(f"\n📘 Running FLS Check for Floor: {level_name}", flush=True)

#     with (GRAPHS_DIR / graph_file).open("rb") as f:
#         G_floor = pickle.load(f)
#     with (PATHS_DIR / f"paths_{level_name}.pkl").open("rb") as f:
#         paths = pickle.load(f)

#     # Build alias map from graph metadata (if any)
#     alias_to_primary = _build_alias_maps(G_floor)

#     # Collect graph room ids (hex32 Speckle ids; skip door_*), normalize
#     raw_graph_room_ids = sorted({
#         _s(d.get("room_id")) for _, d in G_floor.nodes(data=True)
#         if d.get("room_id") is not None and not _s(d.get("room_id")).startswith("door_")
#     })
#     graph_room_ids = sorted({_map_to_primary(rid, alias_to_primary) for rid in raw_graph_room_ids})

#     # Also normalize PATHS room_ids to primary
#     for pth in paths:
#         rid_raw = _s(pth.get("room_id"))
#         pth["room_id"] = _map_to_primary(rid_raw, alias_to_primary)

#     # Floor-scoped elements (still useful for floor object + doors)
#     matched_floor = next(
#         (f for f in elements_extracted["Floors"]
#          if _matches_level(f, level_name, LEVEL_ALIAS_MAP)),
#         None
#     )
#     all_rooms_this_branch = list(elements_extracted["Rooms"])
#     doors_on_level = [
#         d for d in elements_extracted.get("Doors", [])
#         if _matches_level(d, level_name, LEVEL_ALIAS_MAP)
#     ]

#     # ======== Key change: rooms_on_level_by_graph (robust) ========
#     id_set = set(graph_room_ids)
#     rooms_on_level_by_graph = [r for r in all_rooms_this_branch if _s(getattr(r, "id", None)) in id_set]

#     if not rooms_on_level_by_graph:
#         # Fallback: strict level match (rare)
#         rooms_on_level_by_graph = [
#             r for r in all_rooms_this_branch
#             if _matches_level(r, level_name, LEVEL_ALIAS_MAP)
#         ]

#     print(f"[DIAG] graph_room_ids={len(graph_room_ids)} | rooms_on_level_by_graph={len(rooms_on_level_by_graph)} | doors_on_level={len(doors_on_level)}", flush=True)

#     # ======== GPT Preload for classifications/OLF/MaxOcc ========
#     room_names = sorted({(getattr(r, "name", "") or "").strip()
#                          for r in rooms_on_level_by_graph if (getattr(r, "name", "") or "").strip()})
#     resolved = resolve_all_parameters(room_names)
#     sync_cc_globals(room_names, resolved)

#     # ======== Floor-level summary object ========
#     floor_objs = []
#     if matched_floor:
#         floor_objs.append(floor_fls_parameters(matched_floor, rooms_on_level_by_graph, level_name=level_name))

#     # ======== Compliance computation (using graph-following room set) ========
#     G_floor.graph["alias_to_primary"] = dict(alias_to_primary)  # help downstream if needed

#     compliance_results = compute_compliance_check(
#         paths,
#         graph=G_floor,
#         all_rooms=rooms_on_level_by_graph,     # << key: use graph-following rooms
#         all_floors=[matched_floor] if matched_floor else None,
#         all_doors=doors_on_level,
#         max_occupancy_results=cc.max_occupancy_results,
#     )

#     # Index compliance by Speckle id (fallback to room_id if room is None)
#     comp_by_id = {}
#     for res in compliance_results:
#         if not isinstance(res, dict):
#             continue
#         rid = None
#         room_obj = res.get("room")
#         if room_obj is not None:
#             rid = _s(getattr(room_obj, "id", None))
#         if not rid:
#             rid = _s(res.get("room_id"))
#         if rid:
#             comp_by_id[rid] = res

#     # ======== Report -> ProgramData/reports ========
#     REPORTS_DIR.mkdir(parents=True, exist_ok=True)
#     report_path = REPORTS_DIR / f"compliance_report_{level_name}.json"
#     report_data = []
#     for res in compliance_results:
#         if not (isinstance(res, dict) and "room" in res):
#             continue
#         room = res["room"]

#         # best-effort exits count (if compute_compliance_check provided fls_parameters)
#         num_exits = None
#         try:
#             fp = res.get("fls_parameters")
#             if fp is not None:
#                 if hasattr(fp, "numOfExits"):
#                     num_exits = getattr(fp, "numOfExits", None)
#                 elif isinstance(fp, dict):
#                     num_exits = fp.get("numOfExits")
#         except Exception:
#             num_exits = None

#         report_data.append({
#             # "level_name": safe_json_value(level_name),
#             "room_level": safe_json_value(getattr(getattr(room, "level", None), "name", None)),
#             "room_number": safe_json_value(getattr(room, "number", None)),
#             "numOfExits": safe_json_value(num_exits),
#             "room_id": safe_json_value(getattr(room, "id", None)),
#             "room_applicationId": safe_json_value(getattr(room, "applicationId", None)),
#             "room_elementId": safe_json_value(getattr(room, "elementId", None)),
#             "room_name": safe_json_value(getattr(room, "name", None)),
#             "travelDistance": safe_json_value(res.get("travel_distance")),
#             # "commonPath": safe_json_value(res.get("common_path")),
#             "isCompliant": safe_json_value(res.get("is_compliant")),
#             "fireSafetyNote": safe_json_value(getattr(room, "fireSafetyNote", None)),
#             "complianceStatus": safe_json_value(getattr(room, "complianceStatus", None)),
#             "buildingClassification": safe_json_value(getattr(room, "buildingClassification", None)),
#             "occupantLoadFactor": safe_json_value(getattr(room, "occupantLoadFactor", None)),
#             "occupancyLoad": safe_json_value(getattr(room, "occupancyLoad", None)),
#             "maximumOccupantLoad": safe_json_value(getattr(room, "maximumOccupantLoad", None)),
#             "area": safe_json_value(getattr(room, "area", None)),
#         })
#     with report_path.open("w", encoding="utf-8") as f:
#         json.dump(report_data, f, indent=2)
#     print(f"📝 Compliance report saved: {report_path}", flush=True)

#         # ======== Build payload: commit ALL elements on this level (not just rooms) ========
#     # Strategy:
#     # - compute FLS-enriched Rooms (and Floor summary object)
#     # - collect all extracted elements that belong to this level
#     # - replace any matching Rooms/Floor by id with the enriched versions
#     # - append any enriched Rooms that don't have level metadata (graph-only) so they're not lost

#     # Collect per-level elements (strict level match, best-effort)
#     level_elements = collect_elements_for_level(elements_extracted, level_name, LEVEL_ALIAS_MAP)

#     # Track updated objects we want to "overlay" onto that level payload
#     updates_by_id = {}

#     # Add floor object (if produced) to updates_by_id (so it replaces the original floor element)
#     if floor_objs:
#         for fo in floor_objs:
#             if isinstance(fo, Base):
#                 fid = _s(getattr(fo, "id", None))
#                 if fid:
#                     updates_by_id[fid] = fo

#     sent = 0
#     unresolved_in_compliance = 0

#     def _extract_num_exits(cres) -> int:
#         # try to take the door count from compute_compliance_check's fls_parameters
#         ne = 0
#         fp = cres.get("fls_parameters") if isinstance(cres, dict) else None
#         if isinstance(fp, Base):
#             try:
#                 ne = int(getattr(fp, "numOfExits", 0) or 0)
#             except Exception:
#                 ne = 0
#         elif isinstance(fp, dict):
#             try:
#                 ne = int(fp.get("numOfExits", 0) or 0)
#             except Exception:
#                 ne = 0
#         # (optional) also accept top-level field if you later hoist it there
#         if not ne:
#             try:
#                 ne = int(cres.get("num_of_exits", 0) or 0)
#             except Exception:
#                 pass
#         return max(ne, 0)

#     # ---- Build enriched rooms for THIS level (graph-following room set) ----
#     for room in rooms_on_level_by_graph:
#         rid = _s(getattr(room, "id", None))
#         cres = comp_by_id.get(rid)

#         if cres is None:
#             obj = cc.fls_parameters(
#                 room,
#                 comment="No valid egress path computed.",
#                 status="Non-Compliant",
#                 travel_distance=None,
#                 common_path=None,
#                 sprinklers=getattr(room, "sprinklers", False),
#                 num_of_exits=0,
#                 max_occupancy_results=cc.max_occupancy_results,
#             )
#         else:
#             td = cres.get("travel_distance")
#             cp = cres.get("common_path")
#             is_ok = cres.get("is_compliant")
#             num_exits = _extract_num_exits(cres)

#             obj = cc.fls_parameters(
#                 room,
#                 comment=("No valid egress path computed." if td in (None, [], ()) else "Travel distance records present."),
#                 status=("Compliant" if is_ok else "Non-Compliant") if is_ok is not None else "Non-Compliant",
#                 travel_distance=td,
#                 common_path=cp,
#                 sprinklers=getattr(room, "sprinklers", False),
#                 num_of_exits=num_exits,
#                 max_occupancy_results=cc.max_occupancy_results,
#             )

#             if cres.get("room") is None:
#                 unresolved_in_compliance += 1

#         # overlay replacement by id
#         if rid:
#             updates_by_id[rid] = obj

#         sent += 1

#     print(f"[SEND] rooms prepared: {sent} | unresolved in compliance (room=None): {unresolved_in_compliance}", flush=True)

#     # ---- Merge: replace any element on this level whose id matches an updated object ----
#     payload = []
#     seen_ids = set()

#     for e in level_elements:
#         eid = _s(getattr(e, "id", None))
#         if eid and eid in updates_by_id:
#             payload.append(updates_by_id[eid])
#             seen_ids.add(eid)
#         else:
#             payload.append(e)
#             if eid:
#                 seen_ids.add(eid)

#     # Add any updated objects not already present in level_elements (common for graph-only Rooms)
#     for oid, obj in updates_by_id.items():
#         if oid and oid not in seen_ids:
#             payload.append(obj)
#             seen_ids.add(oid)

#     print(f"[PAYLOAD] level_elements={len(level_elements)} | updates={len(updates_by_id)} | final_payload={len(payload)}", flush=True)

#     # Pretty print preview (rooms only)
#     print("\n🚀 Level commit preview (rooms):", flush=True)
#     for rid, obj in list(updates_by_id.items())[:50]:
#         # show up to 50 rooms to avoid spamming logs
#         if not isinstance(obj, Base):
#             continue
#         name = getattr(obj, "name", "?")
#         try:
#             classification = obj["buildingClassification"]
#         except Exception:
#             classification = "❌"
#         print(f"🧱 {name} | buildingClassification = {classification}", flush=True)
#     if payload:
#         send_model_to_speckle_per_floor(
#             payload, client, PROJECT_ID,
#             level_name=level_name,
#             message_prefix="Fire Safety Compliance – Check",
#         )

# print("\n✅ FLS Parameters + Compliance Review Complete.", flush=True)

# # ========= Final report generation (Markdown + PDF) =========
# # After all floors are processed + committed, generate the consolidated report
# # using generate_report.py (reads compliance_report_*.json in REPORTS_DIR).

# def _try_generate_final_report(reports_dir: Path) -> Path | None:
#     """Best-effort: generate a consolidated Markdown + PDF report.

#     Returns:
#         Path to PDF if created/found, else None.
#     """
#     try:
#         import subprocess

#         gen_script = Path(__file__).resolve().parent / "generate_report.py"
#         if not gen_script.exists():
#             print(f"[REPORT] WARN: generate_report.py not found at {gen_script}", flush=True)
#             return None

#         env = os.environ.copy()
#         # Keep context for the report header
#         if selected_pdf:
#             env["SELECTED_CODE_PDF"] = selected_pdf
#         env.setdefault("PROJECT_ID", PROJECT_ID)
#         env.setdefault("MODEL_ID", MODEL_ID)

#         # Prefer the current interpreter (same env as run_fls_main). If it fails,
#         # fallback to the langchain venv interpreter.
#         candidates = [sys.executable]
#         if LANGCHAIN_PY and LANGCHAIN_PY not in candidates:
#             candidates.append(LANGCHAIN_PY)

#         last_stdout = ""
#         last_stderr = ""
#         for py in candidates:
#             cmd = [py, "-u", str(gen_script), "--reports-dir", str(reports_dir)]
#             print(f"[REPORT] Running: {' '.join(cmd)}", flush=True)
#             proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
#             last_stdout, last_stderr = proc.stdout or "", proc.stderr or ""

#             if last_stdout.strip():
#                 print(last_stdout, flush=True)
#             if last_stderr.strip():
#                 print(last_stderr, flush=True)

#             if proc.returncode == 0:
#                 # Prefer parsing the explicit [ok] PDF written line
#                 pdf_path = None
#                 for line in last_stdout.splitlines():
#                     m = re.search(r"\[ok\]\s+PDF written:\s+(.+)$", line.strip())
#                     if m:
#                         pdf_path = m.group(1).strip()
#                         break

#                 # Fallback: expected filename in reports_dir
#                 if not pdf_path:
#                     expected = reports_dir / f"fls_compliance_report_{PROJECT_ID}_{MODEL_ID}.pdf"
#                     if expected.exists():
#                         pdf_path = str(expected)

#                 if pdf_path and Path(pdf_path).exists():
#                     print(f"[REPORT] ✅ Final report ready: {pdf_path}", flush=True)
#                     # Marker line that the backend/UI can parse if needed
#                     print(f"REPORT_PDF_PATH={pdf_path}", flush=True)
#                     return Path(pdf_path)

#                 print("[REPORT] WARN: Report ran but PDF not found.", flush=True)
#                 return None

#             print(f"[REPORT] WARN: generate_report failed using {py} (rc={proc.returncode}); trying next interpreter...", flush=True)

#         print("[REPORT] ❌ Failed to generate report with available Python interpreters.", flush=True)
#         return None

#     except Exception as ex:
#         print(f"[REPORT] ERROR: {ex}", flush=True)
#         return None


# # Attempt final report generation (best-effort)
# _try_generate_final_report(REPORTS_DIR)

# run_fls_main.py — ProgramData-aware (graphs/paths/cache/logs/reports) + commit ALL rooms (graph-based) and overlay compliance
import os, sys, json, pickle, re
from pathlib import Path
from collections import defaultdict, Counter
import numpy as np

from specklepy.api.client import SpeckleClient
from specklepy.transports.server import ServerTransport
from specklepy.objects.base import Base
from specklepy.api import operations
from specklepy.objects.units import get_units_from_string

from extract_elements import extract_elements_by_type
from code_compliance import compute_compliance_check, floor_fls_parameters
import code_compliance as cc
from send_utils_225 import send_model_to_speckle_per_floor
from speckle_credentials import SPECKLE_SERVER_URL, PROJECT_ID as PROJECT_ID_DEFAULT, MODEL_ID as MODEL_ID_DEFAULT


# ========= Env / IDs =========
PROJECT_ID = (os.getenv("PROJECT_ID") or PROJECT_ID_DEFAULT).strip()
MODEL_ID   = ((os.getenv("MODEL_ID") or MODEL_ID_DEFAULT).strip()).split("@", 1)[0]
SPECKLE_USER_TOKEN = os.getenv("SPECKLE_USER_TOKEN")

if not PROJECT_ID:
    print("❌ PROJECT_ID is not set", flush=True); sys.exit(1)
if not MODEL_ID:
    print("❌ MODEL_ID is not set", flush=True); sys.exit(1)
if not SPECKLE_USER_TOKEN:
    print("❌ Missing SPECKLE_USER_TOKEN in environment (user must authenticate)", flush=True); sys.exit(1)


# ========= ProgramData layout =========
def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s or "")

PROJECT_KEY = f"{_sanitize(PROJECT_ID)}_{_sanitize(MODEL_ID)}"
BASE_PD = Path(r"C:\ProgramData\VeriFire") / PROJECT_KEY

GRAPHS_DIR  = BASE_PD / "graphs"
PATHS_DIR   = BASE_PD / "paths"
LOGS_DIR    = BASE_PD / "invalid_units"
REPORTS_DIR = BASE_PD / "reports"

for p in (BASE_PD, GRAPHS_DIR, PATHS_DIR, LOGS_DIR, REPORTS_DIR):
    p.mkdir(parents=True, exist_ok=True)

print(f"[PD] BASE_PD={BASE_PD}")
print(f"[PD] GRAPHS_DIR={GRAPHS_DIR}")
print(f"[PD] PATHS_DIR={PATHS_DIR}")
print(f"[PD] LOGS_DIR={LOGS_DIR}")
print(f"[PD] REPORTS_DIR={REPORTS_DIR}")


# ========= Patch Base.units (tolerant) =========
_invalid_units = set()
_INVALID_UNITS_LOG = LOGS_DIR / "invalid_units_log.txt"

def _safe_units_set(self, value):
    try:
        self.__dict__["units"] = get_units_from_string(value)
    except Exception:
        try:
            if value not in _invalid_units:
                with _INVALID_UNITS_LOG.open("a", encoding="utf-8") as f:
                    f.write(f"{value}\n")
                _invalid_units.add(value)
        except Exception:
            pass
        self.__dict__["units"] = None

def _safe_units_get(self):
    return self.__dict__.get("units", None)

Base.units = property(fget=_safe_units_get, fset=_safe_units_set)


# ========= Speckle auth & receive (✅ newest commit on branch) =========
client = SpeckleClient(host=SPECKLE_SERVER_URL)
client.authenticate_with_token(SPECKLE_USER_TOKEN)

branch = client.branch.get(PROJECT_ID, MODEL_ID)
if not branch:
    print(f"❌ Branch (model) not found: {MODEL_ID}", flush=True); sys.exit(1)

# Prefer the branch page listing (often newest-first)
default_commit = None
try:
    if branch.commits and branch.commits.items:
        # Many servers return newest-first already
        default_commit = branch.commits.items[0]
except Exception:
    default_commit = None

# Fallback to full commit list (ensure newest-first by createdAt)
if not default_commit:
    commits = client.commit.list(PROJECT_ID, MODEL_ID)
    if not commits:
        print("❌ No commits found on the specified branch.", flush=True); sys.exit(1)
    commits_sorted = sorted(
        commits,
        key=lambda c: getattr(c, "createdAt", "") or "",
        reverse=True
    )
    default_commit = commits_sorted[0]

print(f"🧭 Using commit: {default_commit.id} | message: {getattr(default_commit,'message','').strip() or '(no message)'}", flush=True)

transport = ServerTransport(client=client, stream_id=PROJECT_ID)
speckle_data = operations.receive(default_commit.referencedObject, transport)

# Extract categorized elements
elements_extracted = extract_elements_by_type(speckle_data.elements)

def build_level_alias_map_from(elements_lists) -> dict:
    """
    Build a mapping that normalizes any '*-CL' (or variants) to the corresponding '*-FFL'.
    Examples handled:
      "P-03-3RD-CL"    -> "P-03-3RD-FFL"
      "Level 3 - CL"   -> "Level 3 - FFL"
      "LEVEL 5 (CL)"   -> "LEVEL 5 (FFL)"
      "L2_CL"          -> "L2_FFL"
    If an FFL twin doesn't exist, we still map to the synthesized FFL key so that
    grouping collapses under FFL.
    """
    level_names = set()

    def _collect(obj):
        lvl = getattr(obj, "level", None)
        name = getattr(lvl, "name", None) if lvl else None
        if isinstance(name, str) and name.strip():
            level_names.add(name.strip())

    # Collect level names from all categories
    for lst in elements_lists:
        for obj in lst:
            _collect(obj)

    alias = {}
    for name in list(level_names):
        n = name.strip()

        # Normalize common CL suffix forms
        # Case 1: "...-CL"
        if n.endswith("-CL"):
            alias[n] = n[:-3] + "-FFL"
            continue

        # Case 2: "... - CL"
        if n.endswith(" - CL"):
            alias[n] = n[:-5] + " - FFL"
            continue

        # Case 3: "... (CL)"
        if n.endswith("(CL)"):
            alias[n] = n[:-4] + "(FFL)"
            continue

        # Case 4: "..._CL"
        if n.endswith("_CL"):
            alias[n] = n[:-3] + "_FFL"
            continue

        # Conservative generic: lone trailing " CL"
        if n.endswith(" CL"):
            alias[n] = n[:-3] + " FFL"
            continue

    return alias


# Build a CL→FFL alias map so per-floor commits include elements referenced to either level naming scheme.
_level_lists = [lst for lst in (elements_extracted or {}).values() if isinstance(lst, list)]
LEVEL_ALIAS_MAP = build_level_alias_map_from(_level_lists) if _level_lists else {}
if LEVEL_ALIAS_MAP:
    print(f"🔁 Level alias normalization enabled ({len(LEVEL_ALIAS_MAP)} mapping(s)).", flush=True)
else:
    print("ℹ️ No level alias mappings detected (continuing with strict level names).", flush=True)

# ========= Selected code PDF =========
selected_pdf = os.getenv("SELECTED_CODE_PDF")
if not selected_pdf:
    print("❌ No PDF selected. Please select a code document from the UI before running this script.")
    sys.exit(1)
print(f"📘 Using selected PDF knowledge base: {selected_pdf}", flush=True)


# ========= Helpers =========
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

def normalize_room_name(s: str) -> str:
    return re.sub(r"[^A-Z0-9 ]+", "", (s or "").strip().upper())

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

import re

_CODELIKE_RE = re.compile(r"^[A-Z]{1,4}-?\d{1,4}[A-Z]?$", re.IGNORECASE)

def _looks_codelike(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return True
    if s.lower() in ("(unnamed room)", "unnamed", "room"):
        return True
    return bool(_CODELIKE_RE.match(s))

def get_room_label_for_llm(room) -> str:
    """
    Try to extract a semantic name (function) for LLM.
    Falls back to room.name if that's all we have.
    """
    # Try common Speckle/Revit-ish fields
    candidates = []

    # direct attributes
    for attr in ("roomName", "RoomName", "name", "Name", "function", "Function"):
        v = getattr(room, attr, None)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    # try parameters dict-like
    params = getattr(room, "parameters", None)
    if isinstance(params, dict):
        for k in ("Room Name", "Name", "ROOM_NAME", "ROOMNAME", "Function", "Department"):
            v = params.get(k)
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())

    # choose the most semantic-looking (prefer non-codelike, longer)
    candidates = [c for c in candidates if c]
    if not candidates:
        return (getattr(room, "name", "") or "").strip()

    # rank: non-codelike first, then longer strings
    candidates.sort(key=lambda x: (_looks_codelike(x), -len(x)))
    return candidates[0]

# ========= Level filtering helpers (for per-floor commits that include all elements on the same level) =========
_LEVEL_KEYS = (
    "level", "baseLevel", "referenceLevel", "startLevel", "endLevel",
    "hostLevel", "constraintLevel", "scheduleLevel"
)

def _level_name_from_obj(obj):
    """Best-effort: extract level name from a Speckle/Revit element."""
    if obj is None:
        return None

    # Common case: obj.level.name
    try:
        lvl = getattr(obj, "level", None)
        name = getattr(lvl, "name", None)
        if name:
            return str(name)
    except Exception:
        pass

    # Try a few other known keys that show up in Revit conversions
    for k in _LEVEL_KEYS:
        try:
            lvl = getattr(obj, k, None)
        except Exception:
            lvl = None
        if lvl is None:
            # Base sometimes stores members dict-style
            try:
                lvl = obj[k]
            except Exception:
                lvl = None
        if lvl is None:
            continue
        try:
            name = getattr(lvl, "name", None)
            if name:
                return str(name)
        except Exception:
            pass
        try:
            # lvl might be a dict-like
            name = lvl.get("name") if isinstance(lvl, dict) else None
            if name:
                return str(name)
        except Exception:
            pass

    return None

def _normalize_level_name(name, level_alias_map=None):
    """Normalize a level name (strip + optional CL→FFL aliasing)."""
    if name is None:
        return None
    n = str(name).strip()
    if not n:
        return None
    if level_alias_map:
        try:
            n = str(level_alias_map.get(n, n)).strip()
        except Exception:
            pass
    return n

def _matches_level(obj, level_name: str, level_alias_map=None) -> bool:
    ln = _normalize_level_name(_level_name_from_obj(obj), level_alias_map)
    tgt = _normalize_level_name(level_name, level_alias_map)
    return bool(ln) and bool(tgt) and ln == tgt

def collect_elements_for_level(elements_by_type: dict, level_name: str, level_alias_map=None) -> list:
    """Collect ALL extracted elements that belong to a given level (best-effort).

    If level_alias_map is provided, both element level names and the target level are normalized
    (mirrors run_grid_main alias map: *-CL variants collapse under the corresponding *-FFL).
    """
    out = []
    for _typ, lst in (elements_by_type or {}).items():
        if not lst:
            continue
        for e in lst:
            try:
                if _matches_level(e, level_name, level_alias_map):
                    out.append(e)
            except Exception:
                continue
    return out
# ========= GPT bridge (external scripts) =========
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
    env.setdefault("VECTOR_DB_DIR", r"C:\ProgramData\VeriFire\vector_db")
    if "DEPLOYMENT" not in env:
        env["DEPLOYMENT"] = (
            env.get("AZURE_OPENAI_CHAT_DEPLOYMENT")
            or env.get("OPENAI_DEPLOYMENT")
            or env.get("CHAT_DEPLOYMENT")
            or ""
        )
    cmd = [python_exe, script, _json.dumps(payload)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if proc.stderr.strip():
        print(proc.stderr, flush=True)

    if proc.returncode != 0:
        print(f"[GPT-BRIDGE:ERROR] {script} failed rc={proc.returncode}", flush=True)
        print(proc.stdout, flush=True)
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
        if proc.stdout.strip():
            print(proc.stdout, flush=True)
    return out

def classify_rooms_via_gpt(room_names):
    print(f"[GPT] classifying {len(room_names)} room(s) via subprocess...", flush=True)
    res = _run_json_script(LANGCHAIN_PY, "llm_classify.py", room_names)
    return {normalize_room_name(k): (str(v).strip() if v else "Unknown") for k, v in res.items()}

def olf_rooms_via_gpt(room_names, classifications=None):
    """
    Extract OLF values via extract_olf.py.
    
    Args:
        room_names: List of original room name strings
        classifications: Optional dict mapping normalized room names to classifications
    
    Returns:
        Dict mapping normalized room names to [olf_value, unit] pairs
    """
    print(f"[GPT] OLF for {len(room_names)} room(s) via subprocess...", flush=True)
    
    # Build payload: if classifications provided, send {room, class} objects
    if classifications:
        payload = []
        for room_name in room_names:
            norm_name = normalize_room_name(room_name)
            classification = classifications.get(norm_name)
            if classification:
                payload.append({"room": room_name, "class": classification})
            else:
                # No classification available - send just the name
                payload.append(room_name)
    else:
        # Backward compatible: just room names
        payload = room_names
    
    res = _run_json_script(LANGCHAIN_PY, "extract_olf.py", payload)
    out = {}
    for k, v in res.items():
        try:
            val, unit = v
            num = parse_olf(val)
            if num is not None:
                out[normalize_room_name(k)] = [float(num), str(unit)]
        except Exception:
            num = parse_olf(v)
            if num is not None:
                out[normalize_room_name(k)] = [float(num), ""]
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


# ========= Simple classification cache =========
# Use GLOBAL classification cache (read-only from extract_classification.py's flat cache)
# Plus a separate extended cache for OLF and max_occupancy data
GLOBAL_CACHE_DIR = Path(r"C:\ProgramData\VeriFire\cache")
GLOBAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Read classifications from the same cache as extract_classification.py (authoritative source)
CLASSIFICATION_CACHE_PATH = GLOBAL_CACHE_DIR / "classification_flat_cache.json"

# Store extended data (olf, max_occupancy) separately to avoid format conflicts
EXTENDED_CACHE_PATH = GLOBAL_CACHE_DIR / "fls_extended_cache.json"

def _normalize_index(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        out[normalize_room_name(k)] = v
    return out

def _load_cache():
    """
    Load cache from TWO sources:
    1. classification_flat_cache.json (authoritative for classifications - flat format)
    2. fls_extended_cache.json (OLF and max_occupancy - nested format)
    
    Merges them into unified nested format for processing.
    """
    idx = {}
    
    # Load classifications from authoritative flat cache (extract_classification.py)
    if CLASSIFICATION_CACHE_PATH.exists():
        try:
            with CLASSIFICATION_CACHE_PATH.open("r", encoding="utf-8") as f:
                class_data = json.load(f)
            if isinstance(class_data, dict):
                for k, v in class_data.items():
                    nk = normalize_room_name(k)
                    if isinstance(v, str) and v.strip():
                        idx[nk] = {"classification": v.strip()}
                print(f"[CACHE] loaded {len(idx)} classifications from flat cache", flush=True)
        except Exception as e:
            print(f"[WARN] classification cache read failed: {e}", flush=True)
    
    # Load extended data (olf, max_occupancy) from separate cache
    if EXTENDED_CACHE_PATH.exists():
        try:
            with EXTENDED_CACHE_PATH.open("r", encoding="utf-8") as f:
                ext_data = json.load(f)
            if isinstance(ext_data, dict):
                for k, v in ext_data.items():
                    nk = normalize_room_name(k)
                    if isinstance(v, dict):
                        # Merge extended data with existing entry
                        if nk in idx:
                            idx[nk].update(v)
                        else:
                            idx[nk] = v
                print(f"[CACHE] merged extended data for {len(ext_data)} rooms", flush=True)
        except Exception as e:
            print(f"[WARN] extended cache read failed: {e}", flush=True)
    
    sample = list(idx.keys())[:5]
    print(f"[CACHE] total: {len(idx)} rooms. sample={sample}", flush=True)
    return idx

def _save_cache(index: dict):
    """
    Save cache to TWO separate files:
    1. classification_flat_cache.json - ONLY classifications in flat format
    2. fls_extended_cache.json - OLF and max_occupancy in nested format
    
    This prevents format conflicts between scripts.
    """
    GLOBAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Build flat classifications for extract_classification.py compatibility
    flat_classifications = {}
    extended_data = {}
    
    for k, v in (index or {}).items():
        nk = normalize_room_name(k)
        
        if isinstance(v, str):
            # Flat format - just classification
            flat_classifications[nk] = v
        elif isinstance(v, dict):
            # Nested format
            if "classification" in v and isinstance(v["classification"], str):
                flat_classifications[nk] = v["classification"]
            
            # Store extended fields separately
            ext = {}
            if "olf" in v:
                ext["olf"] = v["olf"]
            if "max_occupancy" in v:
                ext["max_occupancy"] = v["max_occupancy"]
            if ext:
                extended_data[nk] = ext
    
    # Save flat classifications (read by extract_classification.py)
    try:
        with CLASSIFICATION_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(flat_classifications, f, indent=2, ensure_ascii=False)
        print(f"[CACHE] saved {len(flat_classifications)} classifications to flat cache", flush=True)
    except Exception as e:
        print(f"[WARN] failed to save classification cache: {e}", flush=True)
    
    # Save extended data
    try:
        with EXTENDED_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(extended_data, f, indent=2, ensure_ascii=False)
        print(f"[CACHE] saved extended data for {len(extended_data)} rooms", flush=True)
    except Exception as e:
        print(f"[WARN] failed to save extended cache: {e}", flush=True)

def resolve_all_parameters(room_names):
    idx = _load_cache()
    orig_to_norm = [(r, normalize_room_name(r)) for r in room_names if (r or "").strip()]
    room_keys = [rk for _, rk in orig_to_norm]
    print(f"[PRELOAD] rooms={len(room_keys)}", flush=True)

    def _get_class(rk: str) -> str:
        """Get classification from cache (supports both flat and nested formats)."""
        v = idx.get(rk)
        if isinstance(v, str):  # Flat format from extract_classification.py
            return v.strip() if v.strip() else None
        if isinstance(v, dict):  # Nested format with classification/olf/max_occupancy
            c = v.get("classification")
            return c if isinstance(c, str) else None
        return None

    def _canon_good(cls: str) -> bool:
        return isinstance(cls, str) and canon_class(cls) not in ("", "UNKNOWN")

    def _extract_cls(val):
        if isinstance(val, str):
            return val.strip()
        if isinstance(val, dict):
            c = val.get("classification")
            if isinstance(c, str):
                return c.strip()
        return None

    def _set_class(rk: str, cls_val):
        """Set classification in cache (ensures nested dict structure)."""
        cls_clean = _extract_cls(cls_val)
        if not _canon_good(cls_clean):
            return
        cur = _get_class(rk)
        if _canon_good(cur):
            return
        # Convert flat to nested if needed
        if isinstance(idx.get(rk), str):
            idx[rk] = {"classification": idx[rk]}
        idx.setdefault(rk, {})["classification"] = cls_clean

    def _set_olf(rk: str, olf_val):
        """Set OLF in cache (ensures nested dict structure)."""
        if isinstance(idx.get(rk), str):  # Convert flat to nested if needed
            idx[rk] = {"classification": idx[rk]}
        idx.setdefault(rk, {})["olf"] = olf_val

    have_class = sum(1 for rk in room_keys if _canon_good(_get_class(rk)))
    have_olf   = sum(1 for rk in room_keys if isinstance(idx.get(rk), dict) and "olf" in idx.get(rk, {}))
    
    # Extract classes (handle both flat and nested formats)
    classes_seen = set()
    for v in idx.values():
        if isinstance(v, str):
            classes_seen.add(canon_class(v))
        elif isinstance(v, dict) and v.get("classification"):
            classes_seen.add(canon_class(v.get("classification", "")))
    classes_seen.discard(""); classes_seen.discard("UNKNOWN")
    print(f"[PRELOAD] classifications fetched: {have_class}", flush=True)
    print(f"[PRELOAD] OLF fetched: {have_olf}", flush=True)
    print(f"[PRELOAD] max-occupancy fetched (by class): {len(classes_seen)}", flush=True)

    miss_cls = []
    for _, rk in orig_to_norm:
        cur = _get_class(rk)
        if not _canon_good(cur):
            miss_cls.append(rk)
    if miss_cls:
        want_originals = [orig for (orig, rk) in orig_to_norm if rk in miss_cls]
        got = classify_rooms_via_gpt(want_originals) or {}
        got_norm = { normalize_room_name(k): v for (k, v) in got.items() if (k or "").strip() }
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
            sample_vals = {k: got_norm[k] for k in list(got_norm.keys())[:3]}
            print(f"[MERGE:WARN] No usable classifications. Sample payload: {sample_vals}", flush=True)

    miss_olf = [rk for rk in room_keys if not (isinstance(idx.get(rk), dict) and "olf" in idx.get(rk, {}))]
    if miss_olf:
        want_originals = [orig for (orig, rk) in orig_to_norm if rk in miss_olf]
        
        # Build classifications dict for rooms needing OLF
        room_classifications = {}
        for (orig, rk) in orig_to_norm:
            if rk in miss_olf:
                classification = _get_class(rk)
                if classification and _canon_good(classification):
                    room_classifications[rk] = classification
        
        got_olf = olf_rooms_via_gpt(want_originals, classifications=room_classifications) or {}
        got_olf_norm = { normalize_room_name(k): v for (k, v) in got_olf.items() if (k or "").strip() }
        updated = 0
        for (orig, rk) in orig_to_norm:
            if rk in miss_olf and rk in got_olf_norm:
                _set_olf(rk, got_olf_norm[rk])
                updated += 1
        print(f"[MERGE] OLF updated: {updated}/{len(miss_olf)}", flush=True)

    class_to_max = {}
    for v in idx.values():
        if isinstance(v, dict):
            c = canon_class(v.get("classification",""))
            if c and isinstance(v.get("max_occupancy"), (int, float)):
                class_to_max[c] = int(v["max_occupancy"])

    needed_classes = set()
    for rk in room_keys:
        c = canon_class(_get_class(rk) or "")
        if c and c not in ("UNKNOWN", "MIXED") and c not in class_to_max:
            needed_classes.add(c)

    if needed_classes:
        got_max = max_occ_by_class_via_gpt(sorted(needed_classes)) or {}
        got_max_norm = { canon_class(k): int(v) for (k, v) in got_max.items() if isinstance(v, (int, float)) }
        class_to_max.update(got_max_norm)
        print(f"[MERGE] max-occupancy classes updated: {len(got_max_norm)}/{len(needed_classes)}", flush=True)

    for rk in room_keys:
        c = canon_class(_get_class(rk) or "")
        if c in class_to_max:
            # Convert flat to nested if needed
            if isinstance(idx.get(rk), str):
                idx[rk] = {"classification": idx[rk]}
            idx.setdefault(rk, {})["max_occupancy"] = int(class_to_max[c])

    _save_cache(idx)

    out = {}
    for rk in room_keys:
        v = idx.get(rk)
        if isinstance(v, str):  # Flat format - just classification
            out[rk] = {
                "classification": v,
                "max_occupancy": None,
                "olf": None
            }
        elif isinstance(v, dict):  # Nested format
            out[rk] = {
                "classification": v.get("classification", "Unknown"),
                "max_occupancy": v.get("max_occupancy"),
                "olf": v.get("olf") if "olf" in v else None
            }
        else:  # No data for this room
            out[rk] = {
                "classification": "Unknown",
                "max_occupancy": None,
                "olf": None
            }

    # refresh globals (for code_compliance)
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
    # populate code_compliance globals for the current floor run
    cc.classification_results = {
        rn: (resolved.get(normalize_room_name(rn), {}).get("classification") or "Unknown")
        for rn in room_names
    }

    cc.olf_results = {}
    for rn in room_names:
        entry = resolved.get(normalize_room_name(rn), {}).get("olf")
        if entry is None:
            continue
        # normalize to {"olf": float, "unit": str}
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            val, unit = entry[0], entry[1]
            num = parse_olf(val)
            if num is not None:
                cc.olf_results[rn] = {"olf": float(num), "unit": str(unit)}
        else:
            num = parse_olf(entry)
            if num is not None:
                cc.olf_results[rn] = {"olf": float(num), "unit": ""}

    cc.max_occupancy_results = {}
    for rn in room_names:
        mo = resolved.get(normalize_room_name(rn), {}).get("max_occupancy")
        if isinstance(mo, (int, float, np.integer)):
            cc.max_occupancy_results[rn] = {"max_occupancy": int(mo)}



# ========= ID alias normalization helpers =========
def _s(v): 
    return str(v).strip() if v is not None else ""

def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9 ]+", "", (s or "").strip().upper())

def _nk(name, level=None):
    name = _norm(name)
    level = _norm(level) if level else ""
    return f"{level}::{name}" if level else name

def _build_alias_maps(G):
    alias_to_primary = {}
    if isinstance(G.graph.get("alias_to_primary"), dict):
        for a, p in G.graph["alias_to_primary"].items():
            alias_to_primary[_s(a)] = _s(p)
    if isinstance(G.graph.get("room_id_aliases"), dict):
        for primary, aliases in G.graph["room_id_aliases"].items():
            p = _s(primary)
            alias_to_primary[p] = p
            try:
                for a in (aliases or []):
                    alias_to_primary[_s(a)] = p
            except Exception:
                pass
    return alias_to_primary

def _map_to_primary(x, alias_to_primary):
    x = _s(x)
    return alias_to_primary.get(x, x)


# ========= Per-floor loop (pickle graph/paths) =========
for graph_file in sorted(p.name for p in GRAPHS_DIR.glob("G_*.pkl")):
    level_name = graph_file.split("_")[-1].replace(".pkl", "")
    print(f"\n📘 Running FLS Check for Floor: {level_name}", flush=True)

    with (GRAPHS_DIR / graph_file).open("rb") as f:
        G_floor = pickle.load(f)
    with (PATHS_DIR / f"paths_{level_name}.pkl").open("rb") as f:
        paths = pickle.load(f)

    # Build alias map from graph metadata (if any)
    alias_to_primary = _build_alias_maps(G_floor)

    # Collect graph room ids (hex32 Speckle ids; skip door_*), normalize
    raw_graph_room_ids = sorted({
        _s(d.get("room_id")) for _, d in G_floor.nodes(data=True)
        if d.get("room_id") is not None and not _s(d.get("room_id")).startswith("door_")
    })
    graph_room_ids = sorted({_map_to_primary(rid, alias_to_primary) for rid in raw_graph_room_ids})

    # Also normalize PATHS room_ids to primary
    for pth in paths:
        rid_raw = _s(pth.get("room_id"))
        pth["room_id"] = _map_to_primary(rid_raw, alias_to_primary)

    # Floor-scoped elements (still useful for floor object + doors)
    matched_floor = next(
        (f for f in elements_extracted["Floors"]
         if _matches_level(f, level_name, LEVEL_ALIAS_MAP)),
        None
    )
    all_rooms_this_branch = list(elements_extracted["Rooms"])
    doors_on_level = [
        d for d in elements_extracted.get("Doors", [])
        if _matches_level(d, level_name, LEVEL_ALIAS_MAP)
    ]

    # ======== Key change: rooms_on_level_by_graph (robust) ========
    id_set = set(graph_room_ids)
    rooms_on_level_by_graph = [r for r in all_rooms_this_branch if _s(getattr(r, "id", None)) in id_set]

    if not rooms_on_level_by_graph:
        # Fallback: strict level match (rare)
        rooms_on_level_by_graph = [
            r for r in all_rooms_this_branch
            if _matches_level(r, level_name, LEVEL_ALIAS_MAP)
        ]

    print(f"[DIAG] graph_room_ids={len(graph_room_ids)} | rooms_on_level_by_graph={len(rooms_on_level_by_graph)} | doors_on_level={len(doors_on_level)}", flush=True)

    # ======== GPT Preload for classifications/OLF/MaxOcc ========
    room_names = sorted({
        get_room_label_for_llm(r)
        for r in rooms_on_level_by_graph
        if get_room_label_for_llm(r)
    })

    resolved = resolve_all_parameters(room_names)
    sync_cc_globals(room_names, resolved)

    # ======== Floor-level summary object ========
    floor_objs = []
    if matched_floor:
        floor_objs.append(floor_fls_parameters(matched_floor, rooms_on_level_by_graph, level_name=level_name))

    # ======== Compliance computation (using graph-following room set) ========
    G_floor.graph["alias_to_primary"] = dict(alias_to_primary)  # help downstream if needed

    compliance_results = compute_compliance_check(
        paths,
        graph=G_floor,
        all_rooms=rooms_on_level_by_graph,     # << key: use graph-following rooms
        all_floors=[matched_floor] if matched_floor else None,
        all_doors=doors_on_level,
        max_occupancy_results=cc.max_occupancy_results,
    )

    # Index compliance by Speckle id (fallback to room_id if room is None)
    comp_by_id = {}
    for res in compliance_results:
        if not isinstance(res, dict):
            continue
        rid = None
        room_obj = res.get("room")
        if room_obj is not None:
            rid = _s(getattr(room_obj, "id", None))
        if not rid:
            rid = _s(res.get("room_id"))
        if rid:
            comp_by_id[rid] = res

    # ======== Report -> ProgramData/reports ========
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"compliance_report_{level_name}.json"
    report_data = []
    for res in compliance_results:
        if not (isinstance(res, dict) and "room" in res):
            continue
        room = res["room"]

        # best-effort exits count (if compute_compliance_check provided fls_parameters)
        num_exits = None
        try:
            fp = res.get("fls_parameters")
            if fp is not None:
                if hasattr(fp, "numOfExits"):
                    num_exits = getattr(fp, "numOfExits", None)
                elif isinstance(fp, dict):
                    num_exits = fp.get("numOfExits")
        except Exception:
            num_exits = None

        report_data.append({
            # "level_name": safe_json_value(level_name),
            "room_level": safe_json_value(getattr(getattr(room, "level", None), "name", None)),
            "room_number": safe_json_value(getattr(room, "number", None)),
            "numOfExits": safe_json_value(num_exits),
            "room_id": safe_json_value(getattr(room, "id", None)),
            "room_applicationId": safe_json_value(getattr(room, "applicationId", None)),
            "room_elementId": safe_json_value(getattr(room, "elementId", None)),
            "room_name": safe_json_value(getattr(room, "name", None)),
            "travelDistance": safe_json_value(res.get("travel_distance")),
            # "commonPath": safe_json_value(res.get("common_path")),
            "isCompliant": safe_json_value(res.get("is_compliant")),
            "fireSafetyNote": safe_json_value(getattr(room, "fireSafetyNote", None)),
            "complianceStatus": safe_json_value(getattr(room, "complianceStatus", None)),
            "buildingClassification": safe_json_value(getattr(room, "buildingClassification", None)),
            "occupantLoadFactor": safe_json_value(getattr(room, "occupantLoadFactor", None)),
            "occupancyLoad": safe_json_value(getattr(room, "occupancyLoad", None)),
            "maximumOccupantLoad": safe_json_value(getattr(room, "maximumOccupantLoad", None)),
            "area": safe_json_value(getattr(room, "area", None)),
        })
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    print(f"📝 Compliance report saved: {report_path}", flush=True)

        # ======== Build payload: commit ALL elements on this level (not just rooms) ========
    # Strategy:
    # - compute FLS-enriched Rooms (and Floor summary object)
    # - collect all extracted elements that belong to this level
    # - replace any matching Rooms/Floor by id with the enriched versions
    # - append any enriched Rooms that don't have level metadata (graph-only) so they're not lost

    # Collect per-level elements (strict level match, best-effort)
    level_elements = collect_elements_for_level(elements_extracted, level_name, LEVEL_ALIAS_MAP)

    # Track updated objects we want to "overlay" onto that level payload
    updates_by_id = {}

    # Add floor object (if produced) to updates_by_id (so it replaces the original floor element)
    if floor_objs:
        for fo in floor_objs:
            if isinstance(fo, Base):
                fid = _s(getattr(fo, "id", None))
                if fid:
                    updates_by_id[fid] = fo

    sent = 0
    unresolved_in_compliance = 0

    def _extract_num_exits(cres) -> int:
        # try to take the door count from compute_compliance_check's fls_parameters
        ne = 0
        fp = cres.get("fls_parameters") if isinstance(cres, dict) else None
        if isinstance(fp, Base):
            try:
                ne = int(getattr(fp, "numOfExits", 0) or 0)
            except Exception:
                ne = 0
        elif isinstance(fp, dict):
            try:
                ne = int(fp.get("numOfExits", 0) or 0)
            except Exception:
                ne = 0
        # (optional) also accept top-level field if you later hoist it there
        if not ne:
            try:
                ne = int(cres.get("num_of_exits", 0) or 0)
            except Exception:
                pass
        return max(ne, 0)

    # ---- Build enriched rooms for THIS level (graph-following room set) ----
    for room in rooms_on_level_by_graph:
        rid = _s(getattr(room, "id", None))
        cres = comp_by_id.get(rid)

        if cres is None:
            obj = cc.fls_parameters(
                room,
                comment="No valid egress path computed.",
                status="Non-Compliant",
                travel_distance=None,
                common_path=None,
                sprinklers=getattr(room, "sprinklers", False),
                num_of_exits=0,
                max_occupancy_results=cc.max_occupancy_results,
            )
        else:
            td = cres.get("travel_distance")
            cp = cres.get("common_path")
            is_ok = cres.get("is_compliant")
            num_exits = _extract_num_exits(cres)

            obj = cc.fls_parameters(
                room,
                comment=("No valid egress path computed." if td in (None, [], ()) else "Travel distance records present."),
                status=("Compliant" if is_ok else "Non-Compliant") if is_ok is not None else "Non-Compliant",
                travel_distance=td,
                common_path=cp,
                sprinklers=getattr(room, "sprinklers", False),
                num_of_exits=num_exits,
                max_occupancy_results=cc.max_occupancy_results,
            )

            if cres.get("room") is None:
                unresolved_in_compliance += 1

        # overlay replacement by id
        if rid:
            updates_by_id[rid] = obj

        sent += 1

    print(f"[SEND] rooms prepared: {sent} | unresolved in compliance (room=None): {unresolved_in_compliance}", flush=True)

    # ---- Merge: replace any element on this level whose id matches an updated object ----
    payload = []
    seen_ids = set()

    for e in level_elements:
        eid = _s(getattr(e, "id", None))
        if eid and eid in updates_by_id:
            payload.append(updates_by_id[eid])
            seen_ids.add(eid)
        else:
            payload.append(e)
            if eid:
                seen_ids.add(eid)

    # Add any updated objects not already present in level_elements (common for graph-only Rooms)
    for oid, obj in updates_by_id.items():
        if oid and oid not in seen_ids:
            payload.append(obj)
            seen_ids.add(oid)

    print(f"[PAYLOAD] level_elements={len(level_elements)} | updates={len(updates_by_id)} | final_payload={len(payload)}", flush=True)

    # Pretty print preview (rooms only)
    print("\n🚀 Level commit preview (rooms):", flush=True)
    for rid, obj in list(updates_by_id.items())[:50]:
        # show up to 50 rooms to avoid spamming logs
        if not isinstance(obj, Base):
            continue
        name = getattr(obj, "name", "?")
        try:
            classification = obj["buildingClassification"]
        except Exception:
            classification = "❌"
        print(f"🧱 {name} | buildingClassification = {classification}", flush=True)
    if payload:
        send_model_to_speckle_per_floor(
            payload, client, PROJECT_ID,
            level_name=level_name,
            message_prefix="Fire Safety Compliance – Check",
        )

print("\n✅ FLS Parameters + Compliance Review Complete.", flush=True)

# ========= Final report generation (Markdown + PDF) =========
# After all floors are processed + committed, generate the consolidated report
# using generate_report.py (reads compliance_report_*.json in REPORTS_DIR).

def _try_generate_final_report(reports_dir: Path) -> Path | None:
    """Best-effort: generate a consolidated Markdown + PDF report.

    Returns:
        Path to PDF if created/found, else None.
    """
    try:
        import subprocess

        gen_script = Path(__file__).resolve().parent / "generate_report.py"
        if not gen_script.exists():
            print(f"[REPORT] WARN: generate_report.py not found at {gen_script}", flush=True)
            return None

        env = os.environ.copy()
        # Keep context for the report header
        if selected_pdf:
            env["SELECTED_CODE_PDF"] = selected_pdf
        env.setdefault("PROJECT_ID", PROJECT_ID)
        env.setdefault("MODEL_ID", MODEL_ID)

        # Prefer the current interpreter (same env as run_fls_main). If it fails,
        # fallback to the langchain venv interpreter.
        candidates = [sys.executable]
        if LANGCHAIN_PY and LANGCHAIN_PY not in candidates:
            candidates.append(LANGCHAIN_PY)

        last_stdout = ""
        last_stderr = ""
        for py in candidates:
            cmd = [py, "-u", str(gen_script), "--reports-dir", str(reports_dir)]
            print(f"[REPORT] Running: {' '.join(cmd)}", flush=True)
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
            last_stdout, last_stderr = proc.stdout or "", proc.stderr or ""

            if last_stdout.strip():
                print(last_stdout, flush=True)
            if last_stderr.strip():
                print(last_stderr, flush=True)

            if proc.returncode == 0:
                # Prefer parsing the explicit [ok] PDF written line
                pdf_path = None
                for line in last_stdout.splitlines():
                    m = re.search(r"\[ok\]\s+PDF written:\s+(.+)$", line.strip())
                    if m:
                        pdf_path = m.group(1).strip()
                        break

                # Fallback: expected filename in reports_dir
                if not pdf_path:
                    expected = reports_dir / f"fls_compliance_report_{PROJECT_ID}_{MODEL_ID}.pdf"
                    if expected.exists():
                        pdf_path = str(expected)

                if pdf_path and Path(pdf_path).exists():
                    print(f"[REPORT] ✅ Final report ready: {pdf_path}", flush=True)
                    # Marker line that the backend/UI can parse if needed
                    print(f"REPORT_PDF_PATH={pdf_path}", flush=True)
                    return Path(pdf_path)

                print("[REPORT] WARN: Report ran but PDF not found.", flush=True)
                return None

            print(f"[REPORT] WARN: generate_report failed using {py} (rc={proc.returncode}); trying next interpreter...", flush=True)

        print("[REPORT] ❌ Failed to generate report with available Python interpreters.", flush=True)
        return None

    except Exception as ex:
        print(f"[REPORT] ERROR: {ex}", flush=True)
        return None


# Attempt final report generation (best-effort)
_try_generate_final_report(REPORTS_DIR)