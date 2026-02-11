from __future__ import annotations
import os, sys, json, traceback, re
from pathlib import Path
from typing import Dict, List, Optional

import requests  # NEW: use HTTP instead of Azure SDK

# dotenv (same pattern you’ve used successfully)
try:
    from dotenv import load_dotenv, find_dotenv
    _dotenv_path = find_dotenv(usecwd=True)
    load_dotenv(_dotenv_path or "", override=False)
    print(f"[env] .env loaded={bool(_dotenv_path)} path={_dotenv_path}")
except Exception:
    print("[env] WARN: dotenv not available", file=sys.stderr)

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

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

HTTP_TIMEOUT = int(os.getenv("CLASS_TIMEOUT", "120"))

# ---------------- ProgramData paths (match other scripts) ----------------
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\VeriFire")

_env_vec = os.environ.get("VECTOR_DB_DIR", "").strip()
VECTOR_DB_DIR = Path(_env_vec) if _env_vec else (PROGRAMDATA_ROOT / "vector_db")

MODELS_DEFAULT  = PROGRAMDATA_ROOT / "models" / "sentence-transformers" / "all-MiniLM-L6-v2"
OFFLINE_EMB_DIR = Path(os.environ.get("FLS_OFFLINE_EMBEDDINGS_DIR", str(MODELS_DEFAULT)))

def _resolve_selected_index() -> str:
    sel = os.environ.get("SELECTED_CODE_PDF") or os.environ.get("SELECTED_PDF") or ""
    if sel:
        base = os.path.splitext(os.path.basename(sel))[0]
        print(f"[config] using SELECTED_CODE_PDF={sel!r} → index={base!r}", file=sys.stderr)
        return base
    default = "SBC_Code_201"
    print(f"[config] SELECTED_CODE_PDF not set; falling back to {default!r}", file=sys.stderr)
    return default

def _get_embeddings():
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except Exception:
        from langchain.embeddings import HuggingFaceEmbeddings  # fallback

    if OFFLINE_EMB_DIR.exists() and OFFLINE_EMB_DIR.is_dir():
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"
        print(f"[emb] using OFFLINE embeddings at: {OFFLINE_EMB_DIR}", file=sys.stderr)
        return HuggingFaceEmbeddings(
            model_name=str(OFFLINE_EMB_DIR),
            cache_folder=str(OFFLINE_EMB_DIR),
            model_kwargs={"local_files_only": True},
            encode_kwargs={"normalize_embeddings": True},
        )

    print("[emb] offline folder not found; using online id", file=sys.stderr)
    return HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        encode_kwargs={"normalize_embeddings": True},
    )

def _load_faiss_retriever():
    """
    Load FAISS retriever from ProgramData first, then repo-local vector_db,
    using the selected SBC index (SELECTED_CODE_PDF) when set.
    """
    base = _resolve_selected_index()
    pd_folder = VECTOR_DB_DIR / base
    local_folder = Path("vector_db") / base
    folder = pd_folder if pd_folder.is_dir() else local_folder

    print(f"[extract_classification] Loading FAISS: folder={folder}, index_name={base}")
    try:
        from langchain_community.vectorstores import FAISS
        vs = FAISS.load_local(
            folder_path=str(folder),
            index_name=base,
            embeddings=_get_embeddings(),
            allow_dangerous_deserialization=True,
        )
        return vs.as_retriever(search_kwargs={"k": 8})
    except Exception as e:
        print(f"[faiss] WARN: {e}", file=sys.stderr)
        return None


CACHE_DIR = Path(os.getenv("CACHE_DIR", r"C:\ProgramData\VeriFire\cache"))
CACHE_PATH = CACHE_DIR / "classification_flat_cache.json"

def _read_cache() -> Dict[str, str]:
    """
    Flat cache ONLY:
      { "<exact room name>": "GROUP X", ... }

    If an old cache file exists with a different schema, we ignore it safely
    instead of corrupting results.
    """
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}

        out: Dict[str, str] = {}
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k.strip()] = v.strip()
        return out
    except Exception as e:
        print(f"[cache] WARN: read {CACHE_PATH}: {e}", file=sys.stderr)
        return {}

def _write_cache(cache: Dict[str, str]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # write only flat {str: str}
        clean = {str(k): str(v) for k, v in (cache or {}).items() if k and v}
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[cache] WARN: write {CACHE_PATH}: {e}", file=sys.stderr)

SYSTEM_PROMPT = (
    "You are a building code assistant. Classify rooms by their building occupancy classification.\n\n"
    "Standard occupancy groups:\n"
    "- GROUP A (Assembly): A-1 through A-5 (theaters, restaurants, churches, stadiums, large gathering spaces)\n"
    "  * Assembly spaces with < 50 occupants are classified as GROUP B (not Assembly)\n"
    "  * Small meeting/conference rooms in offices → GROUP B\n"
    "  * Large assembly halls / auditoriums → GROUP A-3\n"
    "- GROUP B (Business): offices, professional services, banks, educational above 12th grade, conference rooms\n"
    "- GROUP E (Educational): schools K-12, day care facilities\n"
    "- GROUP F (Factory/Industrial): F-1 (moderate hazard), F-2 (low hazard)\n"
    "- GROUP H (High Hazard): H-1 through H-5 (hazardous materials/processes)\n"
    "- GROUP I (Institutional): I-1 through I-4 (hospitals, nursing homes, prisons)\n"
    "- GROUP M (Mercantile): retail stores, shops, markets, department stores, sales of merchandise\n"
    "- GROUP R (Residential): R-1 through R-4 (hotels, apartments, dwellings)\n"
    "- GROUP S (Storage): S-1 (moderate hazard), S-2 (low hazard)\n"
    "- GROUP U (Utility/Miscellaneous): carports, sheds, minor structures\n\n"
    "CRITICAL distinctions:\n"
    "- Small meeting/conference rooms (< 50 occupants, accessory to offices) → GROUP B\n"
    "- Large meeting halls/auditoriums (≥ 50 occupants, primary assembly use) → GROUP A-3\n"
    "- Retail/sales spaces → GROUP M (not B)\n"
    "- Offices/professional services → GROUP B\n"
    "- Storage rooms → GROUP S (not B)\n"
    "- Restaurants/bars/cafeterias → GROUP A-2\n\n"
    "Return ONLY a JSON object mapping each room name to its classification, "
    'for example: {\"Office\":\"GROUP B\",\"Small Meeting Room\":\"GROUP B\",\"Retail Store\":\"GROUP M\",\"Assembly Hall\":\"GROUP A-3\"}. '
    "Do not output 'Unknown'; pick the most reasonable standard group based on the room's primary function."
)

# -------------- JSON extraction helpers (adapted from boq.py) --------------

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

    # 2) if it's a quoted JSON string (escaped quotes), json.loads once may yield
    #    a JSON string; try a second pass if that happens.
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

    # 3) find the first {...} block and try that
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

def _openai_generate_json(prompt: str) -> dict:
    """
    Call GPT-4o via Azure OpenAI or OpenAI API.
    Tries Azure first (if configured), falls back to OpenAI API.
    """
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0"))
    max_tokens = int(os.getenv("CLASS_MAX_TOKENS", "512"))

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



# -------------- Core classification with Qwen --------------

def _norm_key(s: str) -> str:
    """Normalize a key for fuzzy matching (case/spacing-insensitive)."""
    return "".join(ch.lower() for ch in str(s) if ch.isalnum())


def _classify_with_qwen(room_names: List[str], context_snippets: Optional[List[str]] = None) -> Dict[str, str]:
    """
    Hardened:
    - forces exact keys
    - retries ONLY missing keys (faster)
    - rejects empty/Unknown values
    """
    original_names = [str(rn).strip() for rn in room_names if str(rn).strip()]
    if not original_names:
        return {}

    ctx = ""
    if context_snippets:
        ctx = "\n\nRelevant code excerpts:\n" + "\n---\n".join(context_snippets[:6])

    def _prompt_for(names: List[str], strict: bool) -> str:
        rooms_json = json.dumps(names, ensure_ascii=False)
        strict_rules = ""
        if strict:
            strict_rules = (
                "\nExtra strict rules:\n"
                "- You MUST classify every room (no null, no Unknown).\n"
                "- If uncertain, choose the closest standard group.\n"
            )

        return (
            f"{SYSTEM_PROMPT}\n\n"
            "Important formatting rules:\n"
            "- Respond ONLY with a single JSON object.\n"
            "- Use EXACTLY each input string as a key (same spelling, same case).\n"
            "- Do not invent extra keys or rename rooms.\n"
            "- One key per room.\n"
            f"{strict_rules}\n"
            f"Rooms to classify (JSON array): {rooms_json}"
            f"{ctx}\n\n"
            "Good example: {\"Office\":\"GROUP B\",\"Small Meeting Room\":\"GROUP B\",\"Retail Store\":\"GROUP M\",\"Assembly Hall\":\"GROUP A-3\"}."
        )

    def _coerce_map(data: object) -> Dict[str, str]:
        norm_map: Dict[str, str] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, str) and v.strip():
                    norm_map[_norm_key(k)] = v.strip()
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                rk = item.get("room") or item.get("name") or item.get("Room") or item.get("RoomName")
                rv = item.get("classification") or item.get("class") or item.get("group") or item.get("value")
                if isinstance(rk, str) and isinstance(rv, str) and rv.strip():
                    norm_map[_norm_key(rk)] = rv.strip()
        return norm_map

    def _assign(names: List[str], norm_map: Dict[str, str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for rn in names:
            nk = _norm_key(rn)
            if nk in norm_map:
                val = norm_map[nk].strip()
                if val and val.upper() != "UNKNOWN":
                    out[rn] = val
        return out

    # 1) first pass (batch)
    data1 = _openai_generate_json(_prompt_for(original_names, strict=False))
    norm1 = _coerce_map(data1)
    result = _assign(original_names, norm1)

    # 2) retry only missing (strict)
    missing = [rn for rn in original_names if rn not in result]
    if missing:
        data2 = _openai_generate_json(_prompt_for(missing, strict=True))
        norm2 = _coerce_map(data2)
        result.update(_assign(missing, norm2))

    # final: filter empties
    result = {k: v for k, v in result.items() if isinstance(v, str) and v.strip()}

    return result


def _context_snippets(retriever, room_names: List[str]) -> List[str]:
    if not retriever:
        return []
    snippets: List[str] = []
    try:
        for q in set(room_names):
            docs = retriever.get_relevant_documents(q)
            for d in docs[:2]:
                page = getattr(d, "page_content", "")
                if page:
                    snippets.append(page)
    except Exception as e:
        print(f"[rag] WARN: {e}", file=sys.stderr)
    return snippets[:8]

def get_classification_with_cache(room_names: List[str] | str) -> Dict[str, str] | str:
    """
    Public API for both:
      - list[str] -> dict[str,str]
      - str -> str
    Cache is a FLAT mapping: { "<exact room name>": "GROUP X", ... }.
    """
    cache = _read_cache()

    # single-string mode
    if isinstance(room_names, str):
        key = room_names.strip()
        # cache hit
        if (
            key in cache
            and isinstance(cache[key], str)
            and cache[key].strip()
            and cache[key].strip().upper() != "UNKNOWN"
        ):
            return cache[key]

        # miss -> Qwen
        retriever = _load_faiss_retriever()
        fresh = _classify_with_qwen(
            [key],
            context_snippets=_context_snippets(retriever, [key]) if retriever else None,
        )
        val = fresh.get(key)
        if isinstance(val, str) and val.strip():
            cache[key] = val.strip()
            _write_cache(cache)
            return val.strip()
        return "Unknown"

    # list mode
    if not isinstance(room_names, list):
        raise TypeError("room_names must be list[str] or str")

    need: List[str] = []
    out: Dict[str, str] = {}
    for rn in room_names:
        k = str(rn).strip()
        if (
            k in cache
            and isinstance(cache[k], str)
            and cache[k].strip()
            and cache[k].strip().upper() != "UNKNOWN"
        ):
            out[k] = cache[k].strip()
        else:
            need.append(k)

    if need:
        retriever = _load_faiss_retriever()
        fresh = _classify_with_qwen(
            need,
            context_snippets=_context_snippets(retriever, need) if retriever else None,
        )
        # merge back (write only good values; never store Unknown)
        updated = 0
        for k in need:
            v = fresh.get(k)
            if isinstance(v, str) and v.strip() and v.strip().upper() != "UNKNOWN":
                out[k] = v.strip()
                cache[k] = v.strip()
                updated += 1
        if updated:
            _write_cache(cache)

        # any still missing -> default Unknown in the return (but NOT cached)
        for k in need:
            if k not in out:
                out[k] = "Unknown"

    return out

# CLI
if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            print("{}", end="")
            sys.exit(0)
        arg = sys.argv[1]
        # JSON array → list mode; otherwise treat as single string
        if arg.strip().startswith("["):
            names = json.loads(arg)
            res = get_classification_with_cache(names)
            print(json.dumps(res, ensure_ascii=False))
        else:
            val = get_classification_with_cache(arg)
            print(json.dumps({arg: val}, ensure_ascii=False))
    except Exception as e:
        print(f"[extract_classification] FATAL: {e}", file=sys.stderr)
        traceback.print_exc()
        print("{}", end="")

