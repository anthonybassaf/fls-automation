#!/usr/bin/env python
"""generate_report.py

Generate a Fire & Life Safety (FLS) compliance report (Markdown + JSON) from the
`compliance_report_<LEVEL>.json` files produced by `run_fls_main.py`.

Outputs (by default) are written next to the input JSONs:
  - fls_compliance_report.md
  - fls_compliance_report.json

Key features:
- Groups results by level, then by room.
- Uses Qwen3 (via Ollama) to generate a short narrative (issues + recommendations)
  per room.
- Optional enrichment of missing fields using your existing Qwen3-backed scripts:
  extract_classification.py, extract_olf.py, extract_max_occupancy.py

Environment (same as your other scripts):
- PROJECT_ID, MODEL_ID (optional; used to locate ProgramData folder)
- SELECTED_CODE_PDF (optional; displayed in report header)
- OLLAMA_URL (default http://172.21.3.6:11434)
- OLLAMA_MODEL (default qwen3-vl:32b)

Usage:
  python generate_report.py
  python generate_report.py --reports-dir "C:\\ProgramData\\VeriFire\\<PROJECT_KEY>\\reports"
  python generate_report.py --no-llm
  python generate_report.py --enrich-missing

"""

from __future__ import annotations

import argparse
from html import parser
import json
import os
import re
import sys
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# -----------------------------
# Ollama / Qwen config
# -----------------------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://172.21.3.6:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "Qwen/Qwen3-VL-32B-Instruct")

HTTP_TIMEOUT_CONNECT = int(os.getenv("REPORT_TIMEOUT_CONNECT", "30"))
HTTP_TIMEOUT_READ = int(os.getenv("REPORT_TIMEOUT_READ", "300"))
HTTP_TIMEOUT_S = (HTTP_TIMEOUT_CONNECT, HTTP_TIMEOUT_READ)

# -----------------------------
# Helpers
# -----------------------------

def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s or "")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _canon_level_from_filename(path: Path) -> str:
    # compliance_report_<LEVEL>.json
    stem = path.stem
    if stem.lower().startswith("compliance_report_"):
        return stem[len("compliance_report_") :]
    return stem


def _extract_first_json(txt: str) -> Any:
    """Robustly extract first JSON object/array from a string."""
    if not txt:
        return {}
    s = txt.strip()

    # strip ```json fences
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE).strip()

    # direct
    try:
        return json.loads(s)
    except Exception:
        pass

    # double-encoded
    try:
        first = json.loads(s)
        if isinstance(first, str):
            try:
                return json.loads(first)
            except Exception:
                pass
        return first
    except Exception:
        pass

    # find first {...} or [...] block
    m_obj = re.search(r"\{[\s\S]*\}", s)
    m_arr = re.search(r"\[[\s\S]*\]", s)
    block = None
    if m_obj and m_arr:
        block = m_obj.group(0) if m_obj.start() < m_arr.start() else m_arr.group(0)
    elif m_obj:
        block = m_obj.group(0)
    elif m_arr:
        block = m_arr.group(0)

    if block:
        try:
            return json.loads(block)
        except Exception:
            try:
                return json.loads(block.encode("utf-8").decode("unicode_escape"))
            except Exception:
                pass

    return {}


def _ollama_generate_json(prompt: str, temperature: float = 0.2, num_ctx: int = 4096, num_predict: int = 512) -> dict:
    """
    NOTE: Despite the name, this talks to an OpenAI-compatible server (vLLM) at OLLAMA_URL.
    Your server exposes /v1/* routes (FastAPI+uvicorn), NOT Ollama /api/* routes.

    Returns: parsed JSON dict (or {} on failure).
    """
    base = OLLAMA_URL.rstrip("/")

    # If your vLLM server requires a key, set one of these env vars:
    # OPENAI_API_KEY / VLLM_API_KEY / API_KEY
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("VLLM_API_KEY") or os.getenv("API_KEY") or ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # IMPORTANT: vLLM model id must match /v1/models
    # From your curl output: "Qwen/Qwen3-VL-32B-Instruct"
    model_id = OLLAMA_MODEL

    # Build OpenAI-style Chat Completions payload
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "Return ONLY a valid JSON object. No prose, no markdown."},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(temperature),
        "max_tokens": int(num_predict),
        "stream": False,
    }

    # Some OpenAI-compatible servers support JSON mode; try it first, then fallback.
    payload_with_json_mode = dict(payload)
    payload_with_json_mode["response_format"] = {"type": "json_object"}

    try:
        url = f"{base}/v1/chat/completions"

        # 1) Try with JSON mode
        resp = requests.post(url, json=payload_with_json_mode, headers=headers, timeout=HTTP_TIMEOUT_S)
        body_text = resp.text or ""

        # If server doesn't support response_format, it may return 400; retry without it.
        if resp.status_code == 400:
            resp = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT_S)
            body_text = resp.text or ""

        resp.raise_for_status()
        rj = resp.json()

        # Extract assistant content
        content = ""
        try:
            content = (rj["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            content = body_text.strip()

        data = _extract_first_json(content)
        return data if isinstance(data, dict) else {}

    except Exception:
        print("[qwen] WARN: failed to get JSON from OpenAI-compatible server", file=sys.stderr)
        traceback.print_exc()
        return {}


def _parse_json_from_subprocess(stdout_text: str) -> dict:
    if not stdout_text:
        return {}
    for line in reversed(stdout_text.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    # fallback: try extract
    data = _extract_first_json(stdout_text)
    return data if isinstance(data, dict) else {}


def _run_script_json(script_path: Path, payload: Any) -> dict:
    """Run a helper script that prints one JSON object to stdout."""
    import subprocess

    env = os.environ.copy()
    # keep parity with run_fls_main subprocess bridge
    env.setdefault("VECTOR_DB_DIR", r"C:\ProgramData\VeriFire\vector_db")

    cmd = [sys.executable, str(script_path), json.dumps(payload, ensure_ascii=False)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if proc.stderr and proc.stderr.strip():
        # don't spam stdout; keep as stderr
        print(proc.stderr, file=sys.stderr)

    if proc.returncode != 0:
        print(f"[enrich] WARN: {script_path.name} failed rc={proc.returncode}", file=sys.stderr)
        return {}

    return _parse_json_from_subprocess(proc.stdout)


def _canon_group(s: str) -> str:
    """Normalize classification string for max-occupancy lookup."""
    if not s:
        return ""
    t = str(s).strip()
    m = re.search(r"group\s+([A-Z](?:-[0-9])?)", t, flags=re.I)
    if m:
        return f"Group {m.group(1).upper()}"
    # already like "B" or "A-3"
    m2 = re.search(r"\b([A-Z](?:-[0-9])?)\b", t)
    if m2:
        return f"Group {m2.group(1).upper()}"
    return t


@dataclass
class ReportPaths:
    reports_dir: Path
    out_md: Path
    out_json: Path
    llm_cache_path: Path


def _resolve_reports_dir(cli_reports_dir: str = "") -> Path:
    """Find ProgramData-based reports folder (preferred) or fallback to local ./reports."""
    if cli_reports_dir:
        return Path(cli_reports_dir)

    # Prefer ProgramData\VeriFire\<PROJECT_KEY>\reports (same as run_fls_main)
    programdata_root = Path(r"C:\ProgramData\VeriFire")
    project_id = (os.getenv("PROJECT_ID") or "").strip()
    model_id = (os.getenv("MODEL_ID") or "").strip().split("@", 1)[0]

    if project_id and model_id:
        project_key = f"{_sanitize(project_id)}_{_sanitize(model_id)}"
        cand = programdata_root / project_key / "reports"
        if cand.is_dir():
            return cand

    # If not provided, pick the newest VeriFire project folder that has /reports
    if programdata_root.is_dir():
        candidates = []
        for d in programdata_root.iterdir():
            if not d.is_dir():
                continue
            rep = d / "reports"
            if rep.is_dir():
                try:
                    candidates.append((rep.stat().st_mtime, rep))
                except Exception:
                    pass
        if candidates:
            candidates.sort(reverse=True, key=lambda x: x[0])
            return candidates[0][1]

    # Fallback
    return Path("reports")


def _load_reports(reports_dir: Path) -> List[dict]:
    if not reports_dir.exists():
        raise FileNotFoundError(f"reports_dir not found: {reports_dir}")

    all_entries: List[dict] = []
    for fp in sorted(reports_dir.glob("compliance_report_*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
            inferred_level = _canon_level_from_filename(fp)
            for row in data:
                if not isinstance(row, dict):
                    continue
                row = dict(row)
                row.setdefault("level_name", row.get("room_level") or inferred_level)
                # ensure stable key
                row["level_name"] = str(row.get("level_name") or inferred_level)
                all_entries.append(row)
        except Exception:
            print(f"[load] WARN: failed to read {fp}", file=sys.stderr)
            traceback.print_exc()

    return all_entries


def _load_llm_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def _save_llm_cache(path: Path, cache: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        print(f"[cache] WARN: failed to save {path}", file=sys.stderr)


def _room_key(row: dict) -> str:
    rid = str(row.get("room_id") or "").strip()
    lvl = str(row.get("level_name") or "").strip()
    nm = str(row.get("room_name") or "").strip()
    return f"{lvl}::{rid or nm}"


def _travel_best(td: Any) -> Optional[float]:
    if td is None:
        return None
    if isinstance(td, (int, float)):
        return float(td)
    if isinstance(td, list):
        vals = [float(x) for x in td if isinstance(x, (int, float))]
        return min(vals) if vals else None
    return None


def _summarize_note(note: str) -> List[str]:
    if not note:
        return []
    # split by the red-cross prefix you used
    parts = [p.strip(" .") for p in note.split("❌")]
    parts = [p.strip() for p in parts if p.strip()]
    return parts or [note.strip()]


def _enrich_missing(entries: List[dict], scripts_dir: Path) -> None:
    """Fill missing buildingClassification / occupantLoadFactor / maximumOccupantLoad if possible."""
    class_script = scripts_dir / "extract_classification.py"
    olf_script = scripts_dir / "extract_olf.py"
    maxocc_script = scripts_dir / "extract_max_occupancy.py"

    if not class_script.exists() or not olf_script.exists() or not maxocc_script.exists():
        print(
            f"[enrich] WARN: helper scripts not found in {scripts_dir}. Skipping enrichment.",
            file=sys.stderr,
        )
        return

    # 1) classification
    need_cls = []
    for r in entries:
        cls = (r.get("buildingClassification") or "").strip() if isinstance(r.get("buildingClassification"), str) else ""
        if not cls or cls.lower() == "unknown":
            nm = (r.get("room_name") or "").strip()
            if nm:
                need_cls.append(nm)

    need_cls = sorted(set(need_cls))
    if need_cls:
        print(f"[enrich] classifications: querying {len(need_cls)} room(s)...", file=sys.stderr)
        cls_map = _run_script_json(class_script, need_cls)
        # merge
        for r in entries:
            cls = (r.get("buildingClassification") or "")
            if isinstance(cls, str) and cls.strip() and cls.strip().lower() != "unknown":
                continue
            nm = (r.get("room_name") or "").strip()
            if nm and nm in cls_map and isinstance(cls_map[nm], str) and cls_map[nm].strip():
                r["buildingClassification"] = cls_map[nm].strip()

    # 2) OLF
    need_olf = []
    for r in entries:
        olf = r.get("occupantLoadFactor")
        if not olf:
            nm = (r.get("room_name") or "").strip()
            if nm:
                need_olf.append(nm)

    need_olf = sorted(set(need_olf))
    if need_olf:
        print(f"[enrich] OLF: querying {len(need_olf)} room(s)...", file=sys.stderr)
        olf_map = _run_script_json(olf_script, need_olf)
        for r in entries:
            if r.get("occupantLoadFactor"):
                continue
            nm = (r.get("room_name") or "").strip()
            if nm and nm in olf_map:
                v = olf_map.get(nm)
                if isinstance(v, list) and len(v) == 2:
                    try:
                        val = float(v[0]) if v[0] is not None else None
                        unit = str(v[1]) if v[1] is not None else ""
                        if val is not None:
                            r["occupantLoadFactor"] = {"olf": val, "unit": unit}
                    except Exception:
                        pass

    # 3) max occupancy by class (only if maximumOccupantLoad missing)
    need_max_rooms = [r for r in entries if r.get("maximumOccupantLoad") in (None, "", 0)]
    classes = []
    for r in need_max_rooms:
        cls = r.get("buildingClassification")
        if isinstance(cls, str) and cls.strip():
            classes.append(_canon_group(cls))
    classes = sorted(set([c for c in classes if c]))

    if classes:
        print(f"[enrich] max occupancy: querying {len(classes)} class(es)...", file=sys.stderr)
        max_map = _run_script_json(maxocc_script, classes)
        # max_map is {"Group B": 49, ...}
        for r in need_max_rooms:
            cls = r.get("buildingClassification")
            if not isinstance(cls, str) or not cls.strip():
                continue
            key = _canon_group(cls)
            v = max_map.get(key)
            if isinstance(v, (int, float)):
                r["maximumOccupantLoad"] = int(v)


def _llm_room_narrative(room_payload: dict) -> dict:
    """Ask Qwen for issues/recommendations based strictly on the provided room payload."""
    max_tokens = int(os.getenv("REPORT_LLM_MAX_TOKENS", "180"))  # was 512
    temperature = float(os.getenv("REPORT_LLM_TEMPERATURE", "0.1"))

    # keep prompt compact (no indent=2)
    payload_txt = json.dumps(room_payload, ensure_ascii=False)

    prompt = (
        "You are a Fire & Life Safety (FLS) compliance reviewer.\n"
        "Use ONLY the JSON provided. Do not invent values.\n\n"
        f"ROOM_JSON={payload_txt}\n\n"
        "Return ONLY this JSON shape:\n"
        "{"
        "\"status_summary\": \"...\", "
        "\"issues\": [\"...\"], "
        "\"recommendations\": [\"...\"], "
        "\"missing_data\": [\"...\"]"
        "}\n"
        "Rules:\n"
        "- issues must be supported by ROOM_JSON fields.\n"
        "- If travelDistance is null -> issue is 'No egress path computed'.\n"
        "- If numOfExits is 0/null -> issue mentions missing exits.\n"
        "- Keep each string short.\n"
    )

    data = _ollama_generate_json(prompt, temperature=temperature, num_ctx=4096, num_predict=max_tokens)
    if not isinstance(data, dict):
        return {}

    out = {
        "status_summary": str(data.get("status_summary") or "").strip(),
        "issues": data.get("issues") if isinstance(data.get("issues"), list) else [],
        "recommendations": data.get("recommendations") if isinstance(data.get("recommendations"), list) else [],
        "missing_data": data.get("missing_data") if isinstance(data.get("missing_data"), list) else [],
    }

    out["issues"] = [str(x).strip() for x in out["issues"] if str(x).strip()]
    out["recommendations"] = [str(x).strip() for x in out["recommendations"] if str(x).strip()]
    out["missing_data"] = [str(x).strip() for x in out["missing_data"] if str(x).strip()]
    return out


def generate_report(paths: ReportPaths, use_llm: bool, enrich_missing: bool) -> Tuple[Path, Path]:
    entries = _load_reports(paths.reports_dir)
    if not entries:
        raise RuntimeError(f"No compliance_report_*.json found in {paths.reports_dir}")

    scripts_dir = Path(__file__).resolve().parent
    if enrich_missing:
        _enrich_missing(entries, scripts_dir=scripts_dir)

    # -------------------------
    # helpers (local)
    # -------------------------
    def _is_ok(rr: dict) -> bool:
        return (rr.get("isCompliant") is True) or (str(rr.get("complianceStatus") or "").strip().lower() == "compliant")

    def _escape_cell(v: Any) -> str:
        """Make a value safe inside a markdown table cell."""
        if v is None:
            return ""
        s = str(v)
        s = s.replace("\n", " ").replace("\r", " ")
        s = re.sub(r"\s+", " ", s).strip()
        # escape pipes so we don't break the table
        s = s.replace("|", r"\|")
        return s

    def _fmt_olf(v: Any) -> str:
        if v is None or v == "":
            return ""
        try:
            if isinstance(v, dict):
                if "olf" in v:
                    unit = v.get("unit") or v.get("units") or ""
                    if unit:
                        return f"{v.get('olf')} {unit}".strip()
                    return str(v.get("olf"))
                return json.dumps(v, ensure_ascii=False)
            if isinstance(v, list) and len(v) == 2:
                return f"{v[0]} {v[1]}".strip()
            if isinstance(v, (int, float)):
                return str(v)
            return str(v)
        except Exception:
            return _escape_cell(v)

    def _fmt_td(td: Any) -> str:
        best = _travel_best(td)
        if best is None:
            return "N/A"
        try:
            return f"{best:.2f}"
        except Exception:
            return str(best)

    # -------------------------
    # group by level
    # -------------------------
    by_level: Dict[str, List[dict]] = defaultdict(list)
    for row in entries:
        lvl = str(row.get("level_name") or "Unknown").strip() or "Unknown"
        by_level[lvl].append(row)

    levels = sorted(by_level.keys())

    # overall stats
    total_rooms = len(entries)
    compliant_rooms = sum(1 for r in entries if _is_ok(r))
    noncompliant_rooms = total_rooms - compliant_rooms

    selected_pdf = os.getenv("SELECTED_CODE_PDF", "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # LLM cache (kept)
    llm_cache = _load_llm_cache(paths.llm_cache_path)
    llm_calls_made = 0

    # -------------------------
    # output JSON (now includes structured tables)
    # -------------------------
    out_json: Dict[str, Any] = {
        "generated_at": now,
        "selected_code_pdf": selected_pdf,
        "summary": {
            "total_rooms": total_rooms,
            "compliant_rooms": compliant_rooms,
            "noncompliant_rooms": noncompliant_rooms,
        },
        "levels": {},
        "tables": {
            "summary_by_level": [],
            "noncompliant_rooms": [],
        },
    }

    # -------------------------
    # Build Markdown
    # -------------------------
    md: List[str] = []
    md.append("# Fire & Life Safety Compliance Report")
    md.append("")
    md.append(f"- Generated at: **{now}**")
    if selected_pdf:
        md.append(f"- Code reference (selected PDF): **{selected_pdf}**")
    md.append(f"- Reports folder: `{paths.reports_dir}`")
    md.append("")

    md.append("## Overall summary")
    md.append("")
    md.append(f"- Total rooms: **{total_rooms}**")
    md.append(f"- Compliant rooms: **{compliant_rooms}**")
    md.append(f"- Non-compliant rooms: **{noncompliant_rooms}**")
    md.append("")

    # -------------------------
    # Summary by level (MD + JSON table)
    # -------------------------
    md.append("## Summary by level")
    md.append("")
    md.append("| Level | Rooms | Compliant | Non-compliant |")
    md.append("|---|---:|---:|---:|")

    out_json["tables"]["summary_by_level"].append(["Level", "Rooms", "Compliant", "Non-compliant"])

    for lvl in levels:
        rows = by_level[lvl]
        ok = sum(1 for r in rows if _is_ok(r))
        bad = len(rows) - ok

        md.append(f"| {_escape_cell(lvl)} | {len(rows)} | {ok} | {bad} |")
        out_json["tables"]["summary_by_level"].append([str(lvl), str(len(rows)), str(ok), str(bad)])

    md.append("")

    # -------------------------
    # All Non-Compliant Rooms (MD + JSON table)
    # -------------------------
    md.append("## Non-Compliant Rooms")
    md.append("")
    md.append("| Level - Room Name | Revit elementId | Classification | Area | OLF | Occupancy Load | Max Occupancy Load | Number of Exits | Travel Distance | Note |")
    md.append("|---|---:|---|---:|---|---:|---:|---:|---:|---|")

    out_json["tables"]["noncompliant_rooms"].append([
        "Level - Room Name",
        "Revit elementId",
        "Classification",
        "Area",
        "OLF",
        "Occupancy Load",
        "Max Occupancy Load",
        "Number of Exits",
        "Travel Distance",
        "Note",
    ])

    any_bad = False

    for lvl in levels:
        rows = by_level[lvl]

        def _sort_key(r: dict):
            nm = str(r.get("room_name") or "")
            return (nm.lower(), str(r.get("room_id") or ""))

        rows_sorted = sorted(rows, key=_sort_key)
        noncompliant_rows = [rr for rr in rows_sorted if not _is_ok(rr)]

        # level summary in JSON
        reason_counter = Counter()
        for rr in noncompliant_rows:
            for reason in _summarize_note(str(rr.get("fireSafetyNote") or "")):
                reason_counter[reason] += 1

        out_json["levels"][lvl] = {
            "rooms": [],
            "summary": {
                "room_count": len(rows_sorted),
                "compliant": sum(1 for rr in rows_sorted if _is_ok(rr)),
                "noncompliant": len(noncompliant_rows),
                "top_reasons": [{"reason": k, "count": v} for k, v in reason_counter.most_common(10)],
            },
        }

        for r in noncompliant_rows:
            any_bad = True

            room_name = _norm(str(r.get("room_name") or "(Unnamed Room)"))
            room_number = _norm(str(r.get("room_number") or ""))

            title = room_name
            if room_number:
                title += f" ({room_number})"
            level_room = f"{lvl} - {title}"

            classification = _norm(str(r.get("buildingClassification") or ""))
            area = r.get("area")
            olf = r.get("occupantLoadFactor")
            occ = r.get("occupancyLoad")
            max_occ = r.get("maximumOccupantLoad")
            num_exits = r.get("numOfExits")
            td = r.get("travelDistance")
            note = _norm(str(r.get("fireSafetyNote") or ""))

            # ---- DISPLAY versions (IMPORTANT: these fix wrap/overlap in your current PDF renderer) ----
            lvl_room_disp = _soft_break_note(level_room, max_word=20)
            note_disp = _soft_break_note(note, max_word=18)

            td_disp = _fmt_td(td)
            olf_disp = _fmt_olf(olf)

            # ---- Markdown row (use *_disp) ----
            md.append(
                "| {lvl_room} | {eid} | {cls} | {area} | {olf} | {occ} | {max_occ} | {exits} | {td} | {note} |".format(
                    lvl_room=_escape_cell(lvl_room_disp),
                    eid=_escape_cell(r.get("room_elementId")),
                    cls=_escape_cell(classification),
                    area=_escape_cell(area),
                    olf=_escape_cell(olf_disp),
                    occ=_escape_cell(occ),
                    max_occ=_escape_cell(max_occ),
                    exits=_escape_cell(num_exits),
                    td=_escape_cell(td_disp),
                    note=_escape_cell(note_disp),
                )
            )

            # ---- Structured table row (RAW values) for Platypus PDF later ----
            best_td = _travel_best(td)
            out_json["tables"]["noncompliant_rooms"].append([
                level_room,
                "" if r.get("room_elementId") is None else str(r.get("room_elementId")),
                classification,
                "" if area is None else str(area),
                "" if olf is None else str(olf),
                "" if occ is None else str(occ),
                "" if max_occ is None else str(max_occ),
                "" if num_exits is None else str(num_exits),
                "" if best_td is None else f"{best_td:.2f}",
                note,
            ])

            # JSON room payload (kept)
            status = str(r.get("complianceStatus") or "Non-Compliant").strip() or "Non-Compliant"

            room_payload = {
                "level": lvl,
                "room_name": room_name,
                "room_number": room_number or None,
                "elementId": r.get("room_elementId"),
                "classification": classification or None,
                "area": area,
                "occupant_load_factor": olf,
                "occupancy_load": occ,
                "maximum_occupant_load": max_occ,
                "numOfExits": num_exits,
                "travelDistance": td,
                "bestTravelDistance": best_td,
                "complianceStatus": status,
                "fireSafetyNote": note,
            }

            # LLM cache logic kept (not emitted to MD)
            llm_block: Dict[str, Any] = {}
            if use_llm:
                llm_mode = os.getenv("REPORT_LLM_MODE", "noncompliant").strip().lower()
                max_llm_calls = int(os.getenv("REPORT_LLM_MAX_CALLS", "50"))
                ck = _room_key(r)

                should_llm = (llm_mode == "all") or (llm_mode == "noncompliant")
                if ck in llm_cache and isinstance(llm_cache[ck], dict):
                    llm_block = llm_cache[ck]
                else:
                    if should_llm and llm_calls_made < max_llm_calls:
                        llm_block = _llm_room_narrative(room_payload)
                        llm_cache[ck] = llm_block
                        llm_calls_made += 1

            out_json["levels"][lvl]["rooms"].append({**room_payload, "raw": r, "llm": llm_block})

    if not any_bad:
        md.append("| (All levels) - (No non-compliant rooms) |  |  |  |  |  |  |  |  |  |  |")

    if use_llm:
        _save_llm_cache(paths.llm_cache_path, llm_cache)

    paths.out_md.write_text("\n".join(md), encoding="utf-8")
    paths.out_json.write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")

    return paths.out_md, paths.out_json

def _infer_project_model_ids_from_reports_dir(reports_dir: str) -> tuple[str, str]:
    """
    reports_dir looks like:
      C:\\ProgramData\\VeriFire\\<PROJECTID>_<MODELID>\\reports
    """
    try:
        p = Path(reports_dir)
        parent = p.parent.name  # "<PROJECTID>_<MODELID>"
        parts = parent.split("_")
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception:
        pass
    return "UNKNOWN_PROJECT", "UNKNOWN_MODEL"


def _qwen_chat_text(prompt: str, temperature: float = 0.2, max_tokens: int = 512) -> str:
    base = OLLAMA_URL.rstrip("/")
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("VLLM_API_KEY") or os.getenv("API_KEY") or ""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return your entire answer as GitHub-flavored Markdown only. "
                    "Use '##' headings and bullet lists. No code blocks."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "stream": False,
    }

    url = f"{base}/v1/chat/completions"
    resp = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT_S)
    if not resp.ok:
        # print body for debugging
        print(f"[qwen] HTTP {resp.status_code} for /v1/chat/completions", file=sys.stderr)
        print("[qwen] error body:", (resp.text or "")[:800], file=sys.stderr)
        resp.raise_for_status()

    rj = resp.json()
    try:
        txt = (rj["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        txt = (resp.text or "").strip()

    # If the model replies in plain text, coerce into Markdown structure
    if txt and ("## " not in txt):
        txt = (
            "### Executive Summary\n"
            f"- {txt.replace(chr(10), chr(10) + '- ')}\n\n"
            "### Findings by Level\n"
            "- (See digest)\n\n"
            "### Recommended Actions\n"
            "- (See digest)\n\n"
            "### Limitations and Data Gaps\n"
            "- (See digest)\n"
        )

    return txt


def _truncate_markdown(report_md: str, max_chars: int = 24000) -> str:
    """
    Build a token-safe DIGEST that preserves:
      - overall totals
      - summary-by-level counts
      - signals extracted from the non-compliant rooms table:
          * Most common reasons (from Note)

    Robust to BOTH table shapes:
      A) with Room ID column (11 cols)
      B) without Room ID column (10 cols)
    """
    hard_cap = int(max_chars or 24000)
    lines = (report_md or "").splitlines()

    def _find_line(pred):
        for i, ln in enumerate(lines):
            if pred(ln):
                return i
        return -1

    def _cells_from_md_row(ln: str):
        if not ln.strip().startswith("|"):
            return []
        s = ln.strip()
        # drop outer pipes
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        parts = [p.strip() for p in s.split("|")]
        # unescape pipes
        parts = [p.replace(r"\|", "|") for p in parts]
        return parts

    def _parse_md_table(start_idx: int):
        """
        Parse a markdown table starting at header row `start_idx`.
        Returns: (header:list[str], rows:list[list[str]], end_idx:int)
        """
        if start_idx < 0 or start_idx >= len(lines):
            return [], [], start_idx
        header = _cells_from_md_row(lines[start_idx])
        if not header:
            return [], [], start_idx
        i = start_idx + 1
        # skip separator row if present
        if i < len(lines) and re.match(r"^\s*\|\s*[-:]+\s*(\|\s*[-:]+\s*)+\|\s*$", lines[i]):
            i += 1

        rows = []
        while i < len(lines):
            ln = lines[i]
            if not ln.strip():
                break
            if not ln.strip().startswith("|"):
                break
            cells = _cells_from_md_row(ln)
            if cells:
                rows.append(cells)
            i += 1
        return header, rows, i

    # -------------------------
    # Totals block (best-effort)
    # -------------------------
    totals_block = ["## Totals"]
    idx_overall = _find_line(lambda ln: ln.strip().lower() == "## overall summary")
    if idx_overall >= 0:
        # grab a small window after heading
        window = []
        for j in range(idx_overall + 1, min(len(lines), idx_overall + 12)):
            t = lines[j].strip()
            if not t:
                if window:
                    break
                continue
            if t.startswith("- "):
                window.append(t)
        if window:
            totals_block.extend(window)
        else:
            totals_block.append("- (Could not parse totals from markdown.)")
    else:
        totals_block.append("- (Overall summary section not found.)")

    # -------------------------
    # Summary-by-level block
    # -------------------------
    level_bullets = ["", "## Levels (from Summary by level table)", ""]
    idx_lvl = _find_line(lambda ln: ln.strip().lower() == "## summary by level")
    if idx_lvl >= 0:
        # find the header row of the table
        idx_tbl = _find_line(lambda ln: ln.strip().startswith("|") and "Level" in ln and "Rooms" in ln)
        if idx_tbl >= 0 and idx_tbl > idx_lvl:
            hdr, rows, _ = _parse_md_table(idx_tbl)
            # expected hdr: Level | Rooms | Compliant | Non-compliant
            # build bullets from rows
            for r in rows:
                if len(r) >= 4:
                    lvl, rooms, ok, bad = r[0], r[1], r[2], r[3]
                    level_bullets.append(f"- **{lvl}**: rooms={rooms}, compliant={ok}, non-compliant={bad}")
            if len(level_bullets) <= 3:
                level_bullets.append("- (No level rows parsed.)")
        else:
            level_bullets.append("- (Summary-by-level table not found.)")
    else:
        level_bullets.append("- (Summary by level section not found.)")

    # -------------------------
    # Detailed noncompliant table signals
    # -------------------------
    reasons_counter = Counter()
    worst = []
    sample = []

    def _to_int(x):
        try:
            return int(str(x).strip())
        except Exception:
            return None

    def _to_float(x):
        try:
            s = str(x).strip()
            if not s or s.lower() in ("n/a", "na", "none"):
                return None
            return float(s)
        except Exception:
            return None

    def _norm_col(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

    idx_detail = _find_line(lambda ln: ln.strip().lower() == "## non-compliant rooms")
    if idx_detail >= 0:
        # find header row of the detailed table
        idx_tbl = -1
        for j in range(idx_detail + 1, min(len(lines), idx_detail + 80)):
            if lines[j].strip().startswith("|") and "Level" in lines[j] and "Room" in lines[j]:
                idx_tbl = j
                break

        if idx_tbl >= 0:
            hdr, rows, _ = _parse_md_table(idx_tbl)
            hdr_norm = [_norm_col(c) for c in hdr]
            hdr_map = {name: i for i, name in enumerate(hdr_norm)}

            # Identify columns by name (works for 10 or 11 cols)
            def find_idx(*needles):
                for i, name in enumerate(hdr_norm):
                    for n in needles:
                        if _norm_col(n) in name:
                            return i
                return None

            # core columns
            idx_level_room = find_idx("levelroomname", "level-roomname", "levelroom") or 0
            idx_eid = find_idx("revitelementid", "elementid")
            idx_cls = find_idx("classification")
            idx_occ = find_idx("occupancyload")
            idx_max = find_idx("maximumoccupantload", "maxoccupancyload")
            idx_exits = find_idx("numberofexits", "numofexits", "exits")
            idx_td = find_idx("traveldistance")
            idx_note = find_idx("note", "firesafetynote")

            # fallback to positional layout if header matching fails
            # 11 cols (with room id): [0 lvl_room, 1 room_id, 2 eid, 3 cls, 4 area, 5 olf, 6 occ, 7 max, 8 exits, 9 td, 10 note]
            # 10 cols (no room id):  [0 lvl_room, 1 eid, 2 cls, 3 area, 4 olf, 5 occ, 6 max, 7 exits, 8 td, 9 note]
            if idx_eid is None or idx_note is None:
                if len(hdr) >= 11:
                    idx_eid, idx_cls, idx_occ, idx_max, idx_exits, idx_td, idx_note = 2, 3, 6, 7, 8, 9, 10
                elif len(hdr) >= 10:
                    idx_eid, idx_cls, idx_occ, idx_max, idx_exits, idx_td, idx_note = 1, 2, 5, 6, 7, 8, 9

            def safe_get(r, k):
                return r[k] if (k is not None and 0 <= k < len(r)) else ""

            for r in rows:
                # ignore malformed rows
                if not r:
                    continue

                room_label = safe_get(r, idx_level_room)
                eid = safe_get(r, idx_eid)
                cls = safe_get(r, idx_cls)
                occ = safe_get(r, idx_occ)
                max_occ = safe_get(r, idx_max)
                exits = safe_get(r, idx_exits)
                td = safe_get(r, idx_td)
                note = safe_get(r, idx_note)

                # reasons
                for reason in _summarize_note(str(note or "")):
                    reasons_counter[reason] += 1

                exits_i = _to_int(exits)
                td_f = _to_float(td)

                # risk scoring
                score = 0
                if exits_i is None or exits_i <= 0:
                    score += 6
                # no path if td missing OR note hints it
                note_l = str(note or "").lower()
                if td_f is None or "no path" in note_l or "unreachable" in note_l:
                    score += 5

                # occupancy overshoot (minor bump)
                occ_i = _to_int(occ)
                max_i = _to_int(max_occ)
                if occ_i is not None and max_i is not None and occ_i > max_i:
                    score += 1

                compact = f"{room_label} | eid={eid} | cls={cls} | exits={exits} | td={td} | note={note}"
                sample.append((score, compact))
                if score >= 8:
                    worst.append((score, compact))

            worst.sort(key=lambda t: t[0], reverse=True)
            sample.sort(key=lambda t: t[0], reverse=True)

    # limit
    top_reasons_n = 10
    worst_rows_total = 12
    sample_rows_total = 18

    detail_block = []
    detail_block.append("")
    detail_block.append("## Non-compliance signals (from Non-compliant Rooms table)")
    detail_block.append("")

    if reasons_counter:
        detail_block.append("### Most common reasons (from Note column)")
        for reason, cnt in reasons_counter.most_common(top_reasons_n):
            detail_block.append(f"- {reason} (**{cnt}**)")
        detail_block.append("")
    else:
        detail_block.append("### Most common reasons (from Note column)")
        detail_block.append("- (No reasons extracted — check table parsing / column names.)")
        detail_block.append("")

    if sample:
        detail_block.append("### Sample of non-compliant rooms (compact)")
        for _, s in sample[:sample_rows_total]:
            detail_block.append(f"- {s}")
        detail_block.append("")

    # -------------------------
    # Final digest assembly
    # -------------------------
    digest_parts = []
    digest_parts.append("# REPORT DIGEST (Token-safe)")
    digest_parts.append("")
    digest_parts.extend(totals_block)
    digest_parts.extend(level_bullets)
    digest_parts.extend(detail_block)

    out = "\n".join(digest_parts).strip()
    if len(out) > hard_cap:
        out = out[:hard_cap] + "\n\n.[DIGEST TRUNCATED TO FIT CONTEXT LIMIT].\n"
    return out


def _build_qwen_summary_prompt(digest_md: str) -> str:
    return (
        "You will be given a Fire & Life Safety compliance REPORT DIGEST.\n"
        "Your job: write a submission-ready summary using ONLY the digest.\n\n"
        "STRICT RULES:\n"
        "- Output MUST be GitHub-flavored Markdown ONLY.\n"
        "- Use these EXACT section headings (each starts with '### '):\n"
        "  ### Executive Summary\n"
        "  ### Findings by Level\n"
        "  ### Recommended Actions\n"
        "  ### Limitations and Data Gaps\n"
        "- Under 'Findings by Level', LIST EACH LEVEL provided in the digest (with counts).\n"
        "- Do NOT invent any missing numbers, levels, or violations.\n"
        "- If something is missing, explicitly state it under 'Limitations and Data Gaps'.\n"
        "- Keep it concise (but concrete).\n\n"
        "REPORT DIGEST:\n"
        "-------------------------\n"
        f"{digest_md}\n"
    )

def _pdf_from_structured(out_json: Any, qwen_summary_md: str, pdf_path: str, title: str) -> None:
    """
    Render the final PDF using reportlab platypus tables.
    Accepts either:
      - out_json as a dict (already loaded), OR
      - out_json as a Path/str pointing to the JSON file.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from xml.sax.saxutils import escape

    # --------- NEW: Coerce out_json to dict ----------
    try:
        if isinstance(out_json, (str, Path)):
            p = Path(out_json)
            if p.exists():
                out_json = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        if not isinstance(out_json, dict):
            out_json = {}
    except Exception:
        out_json = {}

    # Use landscape A4 for wide tables
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        rightMargin=28,
        leftMargin=28,
        topMargin=28,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()

    cell = styles["Normal"].clone("Cell")
    cell.fontName = "Helvetica"
    cell.fontSize = 8.3
    cell.leading = 10
    cell.wordWrap = "CJK"  # breaks long tokens better

    header = styles["Heading5"].clone("Header")
    header.fontName = "Helvetica-Bold"
    header.fontSize = 9.5
    header.leading = 11

    h1 = styles["Heading2"]
    h2 = styles["Heading3"]

    def P(txt: str, st=cell) -> Paragraph:
        txt = escape("" if txt is None else str(txt)).replace("\n", "<br/>")
        # help break long hex IDs
        txt = re.sub(r"([0-9a-fA-F]{8})(?=[0-9a-fA-F]{8})", r"\\1<br/>", txt)
        txt = txt.replace(" - ", " -<br/>")
        return Paragraph(txt, st)

    elements = []
    elements.append(Paragraph(f"<b>{escape(title)}</b>", styles["Title"]))
    elements.append(Spacer(1, 8))

    # AI Summary page
    def _md_inline_to_html(s: str) -> str:
        # escape everything first, then selectively re-introduce <b> tags
        txt = escape("" if s is None else str(s))
        # **bold**
        txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
        return txt

    def _append_md_block(md: str):
        for ln in (md or "").splitlines():
            raw = ln.rstrip()

            if not raw.strip():
                elements.append(Spacer(1, 6))
                continue

            # headings
            m = re.match(r"^\s*(#{1,6})\s+(.*)$", raw)
            if m:
                level = len(m.group(1))
                text = m.group(2).strip()
                st = h1 if level <= 2 else h2
                elements.append(Paragraph(f"<b>{_md_inline_to_html(text)}</b>", st))
                elements.append(Spacer(1, 4))
                continue

            # bullets
            if re.match(r"^\s*[-•]\s+", raw):
                item = re.sub(r"^\s*[-•]\s+", "", raw).strip()
                elements.append(Paragraph(f"• {_md_inline_to_html(item)}", cell))
                continue

            # normal line
            elements.append(Paragraph(_md_inline_to_html(raw), cell))

    # ---- AI Summary page ----
    if (qwen_summary_md or "").strip():
        elements.append(Paragraph("<b>AI-Generated Summary</b>", h1))
        elements.append(Spacer(1, 6))
        _append_md_block(qwen_summary_md)
        elements.append(PageBreak())


    # Detailed Report
    elements.append(Paragraph("<b>Detailed Report</b>", h1))
    elements.append(Spacer(1, 8))

    summ = (out_json.get("summary") or {})
    elements.append(Paragraph("<b>Overall summary</b>", h2))
    elements.append(Paragraph(
        f"Total rooms: {summ.get('total_rooms','?')}<br/>"
        f"Compliant rooms: {summ.get('compliant_rooms','?')}<br/>"
        f"Non-compliant rooms: {summ.get('noncompliant_rooms','?')}",
        cell
    ))
    elements.append(Spacer(1, 10))

    # Summary by level table
    lvl_rows = (out_json.get("tables") or {}).get("summary_by_level") or []
    if lvl_rows:
        elements.append(Paragraph("<b>Summary by level</b>", h2))
        data = [[P(c, header) for c in lvl_rows[0]]]
        for r in lvl_rows[1:]:
            data.append([P(c, cell) for c in r])

        t = Table(data, colWidths=[140, 70, 80, 95], repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 12))

    # Non-compliant rooms table
    nc_rows = (out_json.get("tables") or {}).get("noncompliant_rooms") or []
    if nc_rows:
        elements.append(Paragraph("<b>Non-Compliant Rooms</b>", h2))
        elements.append(Spacer(1, 6))

        data = [[P(c, header) for c in nc_rows[0]]]
        for r in nc_rows[1:]:
            data.append([P(c, cell) for c in r])

        W = doc.width
        ncols = len(nc_rows[0]) if nc_rows else 0
        if ncols == 10:
            weights = [2.4, 0.9, 1.1, 0.8, 0.8, 1.0, 1.0, 0.9, 1.0, 2.4]
        elif ncols == 11:
            weights = [2.4, 1.4, 0.9, 1.1, 0.8, 0.8, 1.0, 1.0, 0.9, 1.0, 2.4]
        else:
            weights = [1.0] * ncols

        s = sum(weights)
        col_widths = [W * (w / s) for w in weights]

        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        elements.append(t)

    doc.build(elements)

def _demote_md_headings(md_text: str, shift: int = 1) -> str:
        """
        Demote Markdown headings by `shift` levels:
          # -> ##, ## -> ###, etc. (capped at ######)
        """
        out = []
        for ln in (md_text or "").splitlines():
            m = re.match(r"^(#{1,6})\s+(.*)$", ln)
            if not m:
                out.append(ln)
                continue
            hashes, rest = m.group(1), m.group(2)
            new_level = min(6, len(hashes) + shift)
            out.append("#" * new_level + " " + rest)
        return "\n".join(out)

def _soft_break_token(s: str, every: int = 10) -> str:
    """
    Insert spaces into long tokens so the canvas-wrap can break them.
    Keeps readable chunks for IDs like f5847206484daba7182380167ab51bb5.
    """
    s = (s or "").strip()
    if not s:
        return s
    # if it already has spaces, leave it
    if " " in s:
        return s
    # break long hex-ish strings / ids
    if len(s) <= every:
        return s
    return " ".join(s[i:i+every] for i in range(0, len(s), every))


def _soft_break_note(s: str, max_word: int = 18) -> str:
    """
    Notes sometimes contain long segments (paths/refs). Add breakpoints.
    """
    s = (s or "").strip()
    if not s:
        return s
    parts = []
    for w in s.split():
        if len(w) > max_word and "://" not in w:
            parts.append(_soft_break_token(w, every=max_word))
        else:
            parts.append(w)
    return " ".join(parts)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports-dir", default="", help="Folder containing compliance_report_*.json")
    ap.add_argument("--out-md", default="", help="Output Markdown path (default: <reports-dir>/fls_compliance_report.md)")
    ap.add_argument("--out-json", default="", help="Output JSON path (default: <reports-dir>/fls_compliance_report.json)")
    ap.add_argument("--no-llm", action="store_true", help="Disable Qwen3 narrative generation")
    ap.add_argument("--enrich-missing", action="store_true", help="Fill missing classification/OLF/maxOcc via helper scripts")
    ap.add_argument("--no-summary", action="store_true", help="Do not call Qwen3 to summarize the markdown report.")
    ap.add_argument("--no-pdf", action="store_true", help="Do not generate the final PDF.")
    ap.add_argument("--summary-max-chars", type=int, default=24000, help="Max markdown chars sent to Qwen for summary.")
    ap.add_argument("--summary-max-tokens", type=int, default=700, help="Max tokens for Qwen summary output.")

    args = ap.parse_args()

    reports_dir = _resolve_reports_dir(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    out_md = Path(args.out_md) if args.out_md else (reports_dir / "fls_compliance_report.md")
    out_json = Path(args.out_json) if args.out_json else (reports_dir / "fls_compliance_report.json")
    llm_cache_path = reports_dir / "fls_llm_room_cache.json"

    paths = ReportPaths(
        reports_dir=reports_dir,
        out_md=out_md,
        out_json=out_json,
        llm_cache_path=llm_cache_path,
    )

    md_path, json_path = generate_report(
        paths,
        use_llm=False,  # IMPORTANT: do NOT do per-room LLM anymore
        enrich_missing=args.enrich_missing
    )

    project_id, model_id = _infer_project_model_ids_from_reports_dir(str(reports_dir))

    md_text = Path(md_path).read_text(encoding="utf-8", errors="ignore")

    # 2) Qwen summary (single call)
    qwen_summary = ""
    if (not args.no_llm) and (not args.no_summary):
        try:
            md_for_llm = _truncate_markdown(md_text, max_chars=int(args.summary_max_chars))
            prompt = _build_qwen_summary_prompt(md_for_llm)
            qwen_summary = _qwen_chat_text(
                prompt,
                temperature=0.2,
                max_tokens=int(args.summary_max_tokens),
            )
        except Exception:
            # Never fail the whole report if the LLM endpoint is down.
            print("[qwen] WARN: summary generation failed; continuing without summary.", file=sys.stderr)
            traceback.print_exc()
            qwen_summary = ""

    # Combine: Summary + report (report nested under "Detailed Report" + hard page break)
    report_nested = _demote_md_headings(md_text, shift=1).strip()

    if qwen_summary.strip():
        final_md = (
            "# AI-Generated Summary\n\n"
            f"{qwen_summary.strip()}\n\n"
            "<!-- PAGEBREAK -->\n\n"
            "# Detailed Report\n\n"
            f"{report_nested}\n"
        )
    else:
        # Even without AI summary, keep a consistent "Detailed Report" wrapper
        final_md = (
            "# Detailed Report\n\n"
            f"{report_nested}\n"
        )


    # Save combined markdown (optional but useful)
    combined_md_path = str(Path(md_path).with_name(f"fls_compliance_report_{project_id}_{model_id}.md"))
    Path(combined_md_path).write_text(final_md, encoding="utf-8")

    # 3) Generate final PDF
    if not args.no_pdf:
        pdf_name = f"fls_compliance_report_{project_id}_{model_id}.pdf"
        pdf_path = str(Path(md_path).with_name(pdf_name))
        title = f"FLS Compliance Report - {project_id}_{model_id}"
        structured = json.loads(Path(json_path).read_text(encoding="utf-8"))
        _pdf_from_structured(structured, qwen_summary, str(pdf_path), title=title)

        print(f"[ok] PDF written: {pdf_path}")

    print(f"[ok] Markdown written: {combined_md_path}")
    print(f"[ok] Source JSON: {json_path}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as ex:
        print(f"[generate_report] FATAL: {ex}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
