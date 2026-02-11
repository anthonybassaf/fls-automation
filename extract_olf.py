#!/usr/bin/env python
import os
import sys
import json
import re
import traceback
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import requests
from dotenv import load_dotenv, find_dotenv

# ---- LangChain / vectorstore ----
try:
    from langchain_community.vectorstores import FAISS
except ImportError:
    from langchain.vectorstores import FAISS  # fallback

# Prefer the new provider if present; else use community
try:
    from langchain_huggingface import HuggingFaceEmbeddings
except Exception:
    from langchain_community.embeddings import HuggingFaceEmbeddings  # fallback


# ---------------- env / helpers (stderr only) ----------------
def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


dotenv_path = find_dotenv(usecwd=True)
loaded = load_dotenv(dotenv_path)
eprint(f"[env] .env loaded={loaded} path={dotenv_path or '(none)'}")


# ---------------- OpenAI / Azure OpenAI config ----------------
try:
    from openai import AzureOpenAI, OpenAI
except ImportError:
    eprint("[ERROR] openai package not installed. Run: pip install openai")
    sys.exit(1)

# Azure OpenAI config (preferred)
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT", "")
AZURE_API_KEY = os.getenv("AZURE_API_KEY", "")
AZURE_DEPLOYMENT = os.getenv("DEPLOYMENT", os.getenv("MODEL_NAME", "gpt-4o"))
AZURE_API_VERSION = os.getenv("API_VERSION", "2024-02-15-preview")

# OpenAI config (fallback)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("MODEL_NAME", "gpt-4o")

HTTP_TIMEOUT = int(os.getenv("OLF_TIMEOUT", "120"))


# ---------------- ProgramData paths ----------------
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\VeriFire")

# VECTOR_DB_DIR: if env var is set and non-empty, use it; otherwise default to ProgramData\vector_db
_env_vec = os.environ.get("VECTOR_DB_DIR", "").strip()
if _env_vec:
    VECTOR_DB_DIR = Path(_env_vec)
else:
    VECTOR_DB_DIR = PROGRAMDATA_ROOT / "vector_db"

MODELS_DEFAULT   = PROGRAMDATA_ROOT / "models" / "sentence-transformers" / "all-MiniLM-L6-v2"
OFFLINE_EMB_DIR  = Path(os.environ.get("FLS_OFFLINE_EMBEDDINGS_DIR", str(MODELS_DEFAULT)))


def _resolve_selected_index() -> str:
    """
    Resolve which code index to use.

    Priority:
    1) SELECTED_CODE_PDF (or SELECTED_PDF) env var, if set.
    2) Fallback to default 'SBC_Code_201'.

    Mirrors extract_max_occupancy.py.
    """
    sel = os.environ.get("SELECTED_CODE_PDF") or os.environ.get("SELECTED_PDF") or ""
    if sel:
        base = os.path.splitext(os.path.basename(sel))[0]
        eprint(f"[config] using SELECTED_CODE_PDF={sel!r} → index={base!r}")
        return base

    default = "SBC_Code_201"
    eprint(f"[config] SELECTED_CODE_PDF not set; falling back to default index {default!r}")
    return default


def _get_embeddings() -> HuggingFaceEmbeddings:
    """
    Prefer an offline local model directory. If it exists, force offline mode.
    Otherwise, allow the online model id (works on dev boxes with internet).
    """
    if OFFLINE_EMB_DIR.exists() and OFFLINE_EMB_DIR.is_dir():
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"
        eprint(f"[emb] using OFFLINE embeddings at: {OFFLINE_EMB_DIR}")
        return HuggingFaceEmbeddings(
            model_name=str(OFFLINE_EMB_DIR),
            cache_folder=str(OFFLINE_EMB_DIR),
            model_kwargs={"local_files_only": True},
            encode_kwargs={"normalize_embeddings": True},
        )
    eprint("[emb] offline folder not found; using online id (will fail on locked networks)")
    return HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        encode_kwargs={"normalize_embeddings": True},
    )


def _load_faiss() -> Optional[FAISS]:
    """
    Try to load FAISS index from ProgramData vector_db first,
    then repo-local vector_db. If nothing is found, log a warning
    and return None so the caller can (in theory) fall back to LLM-only.
    """
    base = _resolve_selected_index()
    pd_folder = VECTOR_DB_DIR / base
    local_folder = Path("vector_db") / base
    folder = pd_folder if pd_folder.is_dir() else local_folder

    if not folder.is_dir():
        eprint(
            f"[extract_olf] WARN: FAISS folder not found. Looked in: {pd_folder} and {local_folder}"
        )
        return None

    eprint(f"[extract_olf] Loading FAISS: folder={folder}, index_name={base}")
    try:
        return FAISS.load_local(
            folder_path=str(folder),
            embeddings=_get_embeddings(),
            index_name=base,
            allow_dangerous_deserialization=True,
        )
    except Exception as ex:
        eprint(f"[extract_olf] WARN: FAISS load failed: {ex}")
        return None


# ---------------- JSON extraction helper (same pattern as extract_max_occupancy) ----------------
def _extract_first_json(txt: str):
    """Robustly coerce JSON from a model string.

    Handles:
      - direct JSON
      - ```json ... ``` fenced blocks
      - quoted/escaped JSON (double-encoded)
      - extra prose around a single top-level {...} block
    """
    if not txt:
        return {}

    s = txt.strip()

    # Strip markdown fences like ```json ... ```
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE).strip()

    # 1) direct parse
    try:
        return json.loads(s)
    except Exception:
        pass

    # 2) if it's a quoted JSON string (escaped quotes), try double-decoding
    try:
        first = json.loads(s)
        if isinstance(first, str):
            try:
                return json.loads(first)
            except Exception:
                pass
        elif isinstance(first, (dict, list)):
            return first
    except Exception:
        pass

    # 3) find the first {...} block
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        block = m.group(0)
        try:
            return json.loads(block)
        except Exception:
            try:
                return json.loads(block.encode("utf-8").decode("unicode_escape"))
            except Exception:
                pass

    return {}


# ---------------- OpenAI JSON helper ----------------
def _openai_generate_json(prompt: str) -> dict:
    """
    Call GPT-4o via Azure OpenAI or OpenAI API.
    Tries Azure first (if configured), falls back to OpenAI API.
    """
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0"))
    max_tokens = int(os.getenv("OLF_MAX_TOKENS", "512"))

    try:
        # Try Azure OpenAI first
        if AZURE_ENDPOINT and AZURE_API_KEY:
            eprint(f"[openai] Using Azure OpenAI: {AZURE_ENDPOINT} deployment={AZURE_DEPLOYMENT}")
            client = AzureOpenAI(
                api_key=AZURE_API_KEY,
                api_version=AZURE_API_VERSION,
                azure_endpoint=AZURE_ENDPOINT,
                timeout=HTTP_TIMEOUT
            )
            model = AZURE_DEPLOYMENT
        elif OPENAI_API_KEY:
            eprint(f"[openai] Using OpenAI API: model={OPENAI_MODEL}")
            client = OpenAI(
                api_key=OPENAI_API_KEY,
                timeout=HTTP_TIMEOUT
            )
            model = OPENAI_MODEL
        else:
            eprint("[ERROR] No OpenAI or Azure OpenAI credentials configured")
            return {}

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON. No prose."},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        eprint(f"[openai] Response: {len(content)} chars")
        eprint(f"[openai][debug]", repr(content[:400]))

        data = _extract_first_json(content)
        return data if isinstance(data, dict) else {}

    except Exception as ex:
        eprint(f"[openai][ERR] {ex}")
        traceback.print_exc()
        return {}


# ---------------- OLF logic (JSON-based, similar to max_occupancy) ----------------
def _ask_llm_for_olf(
    room_name: str, 
    db: Optional[FAISS],
    classification: Optional[str] = None
) -> Tuple[Optional[float], Optional[str]]:
    """
    OLF extractor that uses BOTH room name AND classification:
    - Table 1004.1.2 assigns OLF by "FUNCTION OF SPACE" which depends on:
      * Room name (e.g., "Office", "Classroom", "Storage")
      * Classification (e.g., GROUP B, GROUP E, GROUP S)
    - Example: "Office" + "GROUP B" → "Business areas" → 9 gross
    - Example: "Storage" + "GROUP S" → "Warehouses" → 46 gross
    - Retrieves many chunks (MMR if available), then asks GPT to SELECT relevant chunk IDs.
    - Allows combining multiple chunks to reconstruct a split table/row.
    - Uses soft validation: must return used_chunks + evidence + unit net/gross + numeric value.
    """

    MAX_DOCS = int(os.getenv("OLF_MAX_DOCS", "24"))              # more chunks to cover split tables
    MAX_CHARS_PER_DOC = int(os.getenv("OLF_MAX_CHARS_PER_DOC", "1200"))
    MIN_EVIDENCE_KEYWORDS = ("occupant", "area", "per", "net", "gross", "allowance")

    def _retrieve_chunks() -> list[str]:
        if db is None:
            return []

        queries = [
            # semantic (works across codes)
            f'occupant load factor "{room_name}" maximum floor area allowances per occupant net gross',
            f'"maximum floor area allowances per occupant" "{room_name}"',
            f'"area per occupant" "{room_name}" net gross occupant load',
            # a bit more general (helps when room names are generic like "CIRCULATION")
            f'occupant load factor table net gross "{room_name}"',
            f'egress occupant load factor net gross {room_name}',
        ]

        chunks: list[str] = []

        # Try MMR if your FAISS wrapper supports it
        for q in queries:
            try:
                docs = db.max_marginal_relevance_search(q, k=MAX_DOCS, fetch_k=max(40, MAX_DOCS * 2))
            except Exception:
                try:
                    docs = db.similarity_search(q, k=MAX_DOCS)
                except Exception:
                    docs = []

            for d in docs or []:
                t = (getattr(d, "page_content", "") or "").strip()
                if not t:
                    continue
                if len(t) > MAX_CHARS_PER_DOC:
                    t = t[:MAX_CHARS_PER_DOC]
                chunks.append(t)

            # If we already have plenty, stop early
            if len(chunks) >= MAX_DOCS:
                break

        # De-dup while preserving order
        seen = set()
        out = []
        for c in chunks:
            key = c[:200]  # cheap signature
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out[:MAX_DOCS]

    def _format_ctx(chunks: list[str]) -> str:
        # Label chunks so the LLM can reference them
        parts = []
        for i, c in enumerate(chunks, start=1):
            parts.append(f"[C{i}]\n{c}")
        return "\n\n".join(parts)

    def _soft_validate(v: Optional[float], u: Optional[str], used_chunks: list, evidence: str) -> bool:
        if v is None or u not in ("net", "gross"):
            return False
        if not used_chunks or not isinstance(used_chunks, list):
            return False
        if not evidence or len(evidence.strip()) < 20:
            return False
        ev = evidence.lower()
        # must look like occupant-load-factor context (but not table-number specific)
        return any(k in ev for k in MIN_EVIDENCE_KEYWORDS)

    chunks = _retrieve_chunks()
    ctx = _format_ctx(chunks)
    eprint(f"[extract_olf][lenient] chunks={len(chunks)} room={room_name!r} class={classification!r} ctx_chars={len(ctx)}")

    if not chunks:
        return None, None

    # Build classification context for prompt
    class_context = ""
    if classification:
        class_context = f"\nThe room classification is: {classification}"
        class_context += "\nUse BOTH the room name AND classification to determine the correct 'FUNCTION OF SPACE' row."
        class_context += "\nExamples:"
        class_context += "\n- Office + GROUP B → 'Business areas' → 9 gross"
        class_context += "\n- Classroom + GROUP E → 'Classroom area' → 1.9 net"
        class_context += "\n- Storage + GROUP S → 'Warehouses' → 46 gross"
        class_context += "\n- Retail + GROUP M (basement/grade) → 'Mercantile - Basement and grade floor areas' → 2.8 gross"
        class_context += "\n- Meeting Room + GROUP B → 'Business areas' → 9 gross"
        class_context += "\n- Assembly Hall + GROUP A → 'Assembly without fixed seats' → varies by type\n"

    prompt = f"""
You are a building code assistant.

Goal:
Find the Occupant Load Factor (OLF) for the space/function "{room_name}" using ONLY the provided context chunks.
The OLF is typically presented as an "area per occupant" allowance in Table 1004.1.2 and is labeled as NET or GROSS.{class_context}

Important:
- The relevant table/row may be split across MULTIPLE chunks.
- First, identify which chunk IDs contain the occupant-load-factor table and/or the row for "{room_name}".
- The table lists "FUNCTION OF SPACE" which may not exactly match the room name.
- Use the room name AND classification (if provided) to find the correct function category.
- Then extract the OLF numeric value and whether it is NET or GROSS.

Return ONLY JSON in the exact schema below:
{{
  "olf_value": <number or null>,
  "olf_unit": "net" | "gross" | null,
  "matched_row_label": "<exact table row label used>" | null,
  "used_chunks": ["C1","C7", "..."],
  "evidence": "<short excerpt copied from the chunks that justifies the value>",
  "notes": "<optional short note explaining the match>"
}}

Matching rules (IMPORTANT — use classification to guide matching):
1) Use the classification to narrow down the function category:
   - GROUP A → Look for "Assembly" rows (gaming floors, exhibit gallery, concentrated/standing/unconcentrated seating)
   - GROUP B → Look for "Business areas" (9 gross)
   - GROUP E → Look for "Educational" rows (Classroom area: 1.9 net, Shops/vocational: 4.6 net)
   - GROUP F → Look for "Industrial areas" (22 gross) or manufacturing rows
   - GROUP M → Look for "Mercantile" rows (Areas on other floors: 5.6 gross, Basement/grade: 2.8 gross, Storage: 28 gross)
   - GROUP R → Look for "Residential" (19 gross) or "Dormitories" (4.6 gross)
   - GROUP S → Look for "Warehouses" (46 gross) or "Accessory storage areas" (28 gross)
   - GROUP I → Look for "Inpatient/Outpatient/Sleeping areas"

2) Then match the specific room name to a row within that category:
   - WC / Toilet / Bathroom / Washroom / Lavatory → Look for toilet-specific rows first, then fall back to building function
   - Office / Conference / Meeting + GROUP B → "Business areas"
   - Classroom + GROUP E → "Classroom area"
   - Retail / Shop / Store + GROUP M → "Mercantile" (check floor level for 2.8 vs 5.6 gross)
   - Storage + GROUP S → "Warehouses" or "Accessory storage areas"
   - Assembly spaces + GROUP A → Match by seating type (fixed/concentrated/standing/unconcentrated)

3) If the exact room type is not in the table, choose the closest row within the classification category.

4) You MUST output the exact row label you used from the table as "matched_row_label".

5) Only return null if the provided chunks do not contain any occupant-load-factor / area-per-occupant table at all.

Context chunks:
{ctx}
""".strip()

    data = _openai_generate_json(prompt)

    # Parse leniently
    try:
        if isinstance(data, str):
            data = json.loads(data)
    except Exception:
        data = None

    if not isinstance(data, dict):
        eprint(f"[extract_olf][lenient] bad json for {room_name!r}: {data!r}")
        return None, None
    
    matched = str(data.get("matched_row_label") or "").strip()
    if matched:
        eprint(f"[extract_olf] matched_row_label={matched!r} room={room_name!r}")

    raw_v = data.get("olf_value")
    raw_u = (data.get("olf_unit") or "").strip().lower() if isinstance(data.get("olf_unit"), str) else None
    used_chunks = data.get("used_chunks") or []
    evidence = str(data.get("evidence") or "").strip()

    v = None
    if isinstance(raw_v, (int, float)):
        v = float(raw_v)
    elif isinstance(raw_v, str):
        m = re.search(r"(\d+(?:\.\d+)?)", raw_v)
        if m:
            try:
                v = float(m.group(1))
            except Exception:
                v = None

    u = raw_u if raw_u in ("net", "gross") else None

    if _soft_validate(v, u, used_chunks, evidence):
        return v, u

    eprint(f"[extract_olf][lenient] failed soft-validate room={room_name!r} resp={data!r}")
    return None, None


# ---------------- batch entry ----------------
def run_batch(room_data: List[str] | List[dict]) -> Dict[str, List[Optional[str]]]:
    """
    Accept either:
    - List[str]: room names only (backward compatible)
    - List[dict]: [{"room":"Office", "class":"GROUP B"}, ...] (new format with classification)
    """
    db = _load_faiss()  # may be None

    # Normalize input to list of (room_name, classification) tuples
    room_tuples: List[Tuple[str, Optional[str]]] = []
    for item in room_data:
        if isinstance(item, str):
            room_tuples.append((item.strip(), None))
        elif isinstance(item, dict):
            room_name = str(item.get("room", "") or item.get("name", "")).strip()
            classification = str(item.get("class", "") or item.get("classification", "")).strip() or None
            room_tuples.append((room_name, classification))
        else:
            eprint(f"[run_batch] WARN: invalid item type {type(item)}: {item!r}")
            room_tuples.append(("", None))

    out: Dict[str, List[Optional[str]]] = {}
    for room_name, classification in room_tuples:
        key = room_name or ""
        if not key:
            out[key] = [None, None]
            continue

        if db is None:
            # No FAISS: you *could* still call the LLM with no context,
            # but for now we return nulls to avoid hallucinated OLFs.
            out[key] = [None, None]
            continue

        val, unit = _ask_llm_for_olf(key, db, classification=classification)
        out[key] = [val, unit]
    return out


# ---------------- CLI ----------------
"""
Usage:
    # Old format (backward compatible):
    python extract_olf.py "[\"Classroom\",\"Office\",\"WC\"]"
    
    # New format (with classification for better accuracy):
    python extract_olf.py '[{"room":"Office","class":"GROUP B"},{"room":"Classroom","class":"GROUP E"}]'

Prints exactly ONE JSON object to stdout, e.g.:
    {"Classroom":[1.9,"net"],"Office":[9,"gross"],"WC":[50,"net"]}
"""
if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            raise SystemExit(
                'expected 1 JSON-encoded array argument, e.g. [{"room":"Office","class":"GROUP B"}]'
            )
        try:
            data = json.loads(sys.argv[1])
            if not isinstance(data, list):
                raise ValueError("first argument must be a JSON list")
        except Exception as ex:
            raise SystemExit(f"invalid JSON argument: {ex}")

        result = run_batch(data)
        print(json.dumps(result, separators=(",", ":")))
    except SystemExit as se:
        eprint(f"[extract_olf] {se}")
        print("{}")
        sys.exit(0)
    except Exception as ex:
        eprint(f"[extract_olf] fatal: {ex}")
        traceback.print_exc()
        print("{}")
        sys.exit(0)
