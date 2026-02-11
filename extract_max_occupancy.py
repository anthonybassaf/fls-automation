#!/usr/bin/env python
import os
import sys
import json
import re
import traceback
from typing import Dict, List, Optional
from pathlib import Path

import requests
from dotenv import load_dotenv, find_dotenv

# ---- LangChain / FAISS ----
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

HTTP_TIMEOUT = int(os.getenv("MAXOCC_TIMEOUT", "120"))

# ---------------- ProgramData paths ----------------
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\VeriFire")

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

    This keeps things working out-of-the-box while still allowing
    you to switch code versions later via env.
    """
    sel = os.environ.get("SELECTED_CODE_PDF") or os.environ.get("SELECTED_PDF") or ""
    if sel:
        base = os.path.splitext(os.path.basename(sel))[0]
        eprint(f"[config] using SELECTED_CODE_PDF={sel!r} → index={base!r}")
        return base

    # Fallback default
    default = "SBC_Code_201"
    eprint(
        f"[config] SELECTED_CODE_PDF not set; falling back to default index {default!r}"
    )
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
    and return None (so the caller can fall back to LLM-only).
    """
    base = _resolve_selected_index()
    pd_folder = VECTOR_DB_DIR / base
    local_folder = Path("vector_db") / base
    folder = pd_folder if pd_folder.is_dir() else local_folder

    if not folder.is_dir():
        eprint(
            f"[extract_max_occupancy] WARN: FAISS folder not found. Looked in: {pd_folder} and {local_folder}"
        )
        return None

    eprint(f"[extract_max_occupancy] Loading FAISS: folder={folder}, index_name={base}")
    try:
        return FAISS.load_local(
            folder_path=str(folder),
            embeddings=_get_embeddings(),
            index_name=base,
            allow_dangerous_deserialization=True,
        )
    except Exception as ex:
        eprint(f"[extract_max_occupancy] WARN: FAISS load failed: {ex}")
        return None
    
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

def _kw_score(txt: str) -> int:
    t = (txt or "").lower()
    score = 0
    # generic table signals (works across codes)
    if "maximum occupant load" in t: score += 6
    if "single exit" in t or "one exit" in t: score += 4
    if "exit access doorway" in t: score += 4
    if "spaces with one exit" in t: score += 4
    if "table" in t: score += 2
    # penalize “classification definition” traps
    if "shall be classified as" in t: score -= 3
    if "group b occupancy" in t and "<" in t: score -= 2
    return score


def retrieve_table_focused_chunks(db, group_name: str, max_docs: int = 12, fetch_k: int = 40):
    """
    Pull many candidates, re-rank by keyword evidence of the *table itself*,
    then expand with neighbors to help reconstruct split tables.
    """
    queries = [
        f'maximum occupant load single exit "{group_name}"',
        f'"maximum occupant load" "exit access doorway" "{group_name}"',
        f'"spaces with one exit" "maximum occupant load" "{group_name}"',
        # broader (in case group label doesn’t match)
        'maximum occupant load single exit exit access doorway table',
        'spaces with one exit exit access doorway maximum occupant load',
    ]

    candidates = []

    for q in queries:
        # try MMR if present
        try:
            docs = db.max_marginal_relevance_search(q, k=fetch_k, fetch_k=fetch_k * 2)
        except Exception:
            docs = db.similarity_search(q, k=fetch_k)

        for d in docs or []:
            txt = (getattr(d, "page_content", "") or "").strip()
            if not txt:
                continue
            candidates.append((d, txt, _kw_score(txt)))

    # de-dup by short signature
    seen = set()
    uniq = []
    for d, txt, sc in candidates:
        sig = txt[:220]
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append((d, txt, sc))

    # sort by keyword score desc, then keep best
    uniq.sort(key=lambda x: x[2], reverse=True)

    # If the best score is low, it means we never retrieved the table. That’s your current problem.
    best_score = uniq[0][2] if uniq else -999
    eprint(f"[max_occ][retrieve] best_kw_score={best_score} candidates={len(uniq)} group={group_name}")

    top = uniq[:max_docs]

    # Neighbor expansion (cheap): retrieve “similar” chunks to each top chunk
    # This helps when the table is split across chunks and only part is retrieved.
    expanded = list(top)
    for d, txt, sc in top[:4]:
        try:
            neighbors = db.similarity_search(txt[:400], k=4)
        except Exception:
            neighbors = []
        for nd in neighbors or []:
            nt = (getattr(nd, "page_content", "") or "").strip()
            if not nt:
                continue
            expanded.append((nd, nt, _kw_score(nt)))

    # final de-dup and sort again
    seen2 = set()
    out = []
    for d, txt, sc in expanded:
        sig = txt[:220]
        if sig in seen2:
            continue
        seen2.add(sig)
        out.append((txt, sc))

    out.sort(key=lambda x: x[1], reverse=True)
    return [txt for txt, _ in out[:max_docs]]

# ---------------- OpenAI call helper ----------------
def _openai_generate_json(prompt: str) -> dict:
    """
    Call GPT-4o via Azure OpenAI or OpenAI API.
    Tries Azure first (if configured), falls back to OpenAI API.
    """
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0"))
    max_tokens = int(os.getenv("MAXOCC_MAX_TOKENS", "512"))

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


# ---------------- Qwen logic for max occupancy ----------------
def _ask_llm_for_max_occ(
    group_name: str,
    db: Optional[FAISS],
    story_condition: str = "first_story",   # "first_story" | "other_story" | "unknown"
    sprinklered: Optional[bool] = None      # True/False/None
) -> Optional[int]:
    """
    Extracts single-exit maximum occupant load PER STORY (typically Table 1006.3.2(1)/(2) or equivalent).
    Condition-aware to reduce nulls/mis-picks.
    """

    chunks = retrieve_table_focused_chunks(db, group_name, max_docs=12, fetch_k=40)
    if not chunks:
        return None

    ctx = "\n\n".join([f"[C{i}]\n{c[:1200]}" for i, c in enumerate(chunks, start=1)])

    sprinkler_txt = (
        "The building/story IS equipped throughout with an automatic sprinkler system."
        if sprinklered is True else
        "The building/story is NOT sprinklered."
        if sprinklered is False else
        "Sprinkler status is UNKNOWN."
    )

    story_txt = {
        "first_story": "Use the row/column for: FIRST STORY ABOVE OR BELOW GRADE PLANE (or equivalent wording).",
        "other_story": "Use the row/column for: OTHER STORIES (or equivalent wording).",
        "unknown": "If multiple story conditions exist, pick the MOST CONSERVATIVE (smallest) max occupant load and explain."
    }.get(story_condition, "If multiple story conditions exist, pick the MOST CONSERVATIVE (smallest) max occupant load and explain.")

    prompt = f"""
You are a building code assistant.

Goal:
Find the MAXIMUM OCCUPANT LOAD PER STORY for allowing a SINGLE EXIT (or single exit access doorway)
for occupancy group "{group_name}".

Context:
{sprinkler_txt}
{story_txt}

Rules:
- Use ONLY the provided context chunks.
- The numeric limit is usually in a table referenced by "single exits", "one exit", or "exit access doorway".
- If the table is split across chunks, combine them.
- Do NOT guess from occupancy definitions (e.g., "Group B < 50 persons" is NOT the exit table).
- Return null if the limit is not explicitly present.

Return ONLY JSON:
{{
  "max_occupancy": <integer or null>,
  "used_chunks": ["C2","C4", ...],
  "evidence": "<short excerpt proving the value>",
  "notes": "<short note about which table/condition was used>"
}}

Context chunks:
{ctx}
""".strip()

    data = _openai_generate_json(prompt)

    # parse
    try:
        if isinstance(data, str):
            data = json.loads(data)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    raw = data.get("max_occupancy")
    ev = str(data.get("evidence") or "").strip()
    if not ev or len(ev) < 20:
        return None

    val = None
    if isinstance(raw, (int, float)):
        val = int(raw)
    elif isinstance(raw, str):
        m = re.search(r"\d+", raw)
        if m:
            val = int(m.group(0))

    # very light sanity
    if val is None or val <= 0 or val > 10000:
        return None

    # reject the old “classification trap”
    if "shall be classified as" in ev.lower():
        return None

    return val


# ---------------- batch entry ----------------
def run_batch(groups: List[str]) -> Dict[str, Optional[int]]:
    db = _load_faiss()  # may be None

    out: Dict[str, Optional[int]] = {}
    for g in groups:
        key = (g or "").strip()
        if not key:
            out[key or g] = None
            continue
        val = _ask_llm_for_max_occ(key, db)  # db can be None; handle inside
        out[key] = val
    return out



# ---------------- CLI ----------------
"""
Usage:
    python extract_max_occupancy.py "[\"Group B\",\"Group E\"]"
Prints exactly ONE JSON object to stdout, e.g.:
    {"Group B":49,"Group E":35}
"""
if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            raise SystemExit(
                'expected 1 JSON-encoded array argument, e.g. ["Group B","Group E"]'
            )
        try:
            groups = json.loads(sys.argv[1])
            if not isinstance(groups, list):
                raise ValueError("first argument must be a JSON list")
        except Exception as ex:
            raise SystemExit(f"invalid JSON argument: {ex}")

        result = run_batch(groups)
        print(json.dumps(result, separators=(",", ":")))
    except SystemExit as se:
        eprint(f"[extract_max_occupancy] {se}")
        print("{}")
        sys.exit(0)
    except Exception as ex:
        eprint(f"[extract_max_occupancy] fatal: {ex}")
        print("{}")
        sys.exit(0)
