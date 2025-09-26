from __future__ import annotations
import os, sys, json, traceback, re
from pathlib import Path
from typing import Dict, List, Optional

# dotenv (same pattern you’ve used successfully)
try:
    from dotenv import load_dotenv, find_dotenv
    _dotenv_path = find_dotenv(usecwd=True)
    load_dotenv(_dotenv_path or "", override=False)
    print(f"[env] .env loaded={bool(_dotenv_path)} path={_dotenv_path}")
except Exception:
    print("[env] WARN: dotenv not available", file=sys.stderr)

# Azure OpenAI client
def _azure_client_from_env():
    endpoint   = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_ENDPOINT")
    api_key    = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_API_KEY")
    api_ver    = os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv("API_VERSION")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("DEPLOYMENT")
    print(f"[azure-client] endpoint= {endpoint or 'None'}  api_version= {api_ver or 'None'}  deployment= {deployment or 'None'}  api_key= {'SET' if api_key else ''}")
    if not (endpoint and api_key and api_ver and deployment):
        raise RuntimeError("Missing Azure OpenAI config (endpoint/key/version/deployment).")
    from openai import AzureOpenAI
    return AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_ver), deployment

# Optional FAISS
def _load_faiss_retriever(folder="vector_db\\SBC_Code_201", index_name="SBC_Code_201"):
    print(f"[extract_classification] Loading FAISS: folder={folder}, index_name={index_name}")
    try:
        from langchain_community.vectorstores import FAISS
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
        except Exception:
            from langchain.embeddings import HuggingFaceEmbeddings  # deprecated fallback
        vs = FAISS.load_local(
            folder_path=folder,
            index_name=index_name,
            embeddings=HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2"),
            allow_dangerous_deserialization=True,
        )
        return vs.as_retriever(search_kwargs={"k": 4})
    except Exception as e:
        print(f"[faiss] WARN: {e}", file=sys.stderr)
        return None

CACHE_PATH = Path("cached_data") / "classification_index.json"

def _read_cache() -> Dict[str, str]:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[cache] WARN: read {CACHE_PATH}: {e}", file=sys.stderr)
    return {}

def _write_cache(mapping: Dict[str, str]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"[cache] wrote classifications -> {CACHE_PATH}")
    except Exception as e:
        print(f"[cache] WARN: write {CACHE_PATH}: {e}", file=sys.stderr)

SYSTEM_PROMPT = (
    "You are a building code assistant. Classify rooms by their building occupancy "
    "classification (e.g., GROUP A-1..A-5, B, E, I-1..I-4, etc.). "
    "Return ONLY a JSON object that maps each input room name to a single classification string, "
    'for example: {"Office":"GROUP B","Classroom":"GROUP E","WC":"GROUP B"}. '
    "Do not output 'Unknown'; pick the most reasonable standard group."
)

def _classify_with_gpt(room_names: List[str], context_snippets: Optional[List[str]] = None) -> Dict[str, str]:
    client, deployment = _azure_client_from_env()
    ctx = ""
    if context_snippets:
        ctx = "\n\nRelevant code excerpts:\n" + "\n---\n".join(context_snippets[:6])

    user_prompt = (
        f"Rooms to classify (JSON array): {json.dumps(room_names, ensure_ascii=False)}"
        f"{ctx}\n\nRespond ONLY with a JSON object mapping each input string to a classification. "
        f'Example: {{"Office":"GROUP B","Classroom":"GROUP E","WC":"GROUP B"}}.'
    )
    try:
        resp = client.chat.completions.create(
            model=deployment,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content if resp and resp.choices else ""
        # parse JSON, or recover largest {...} blob
        try:
            data = json.loads(content)
        except Exception:
            m = re.search(r"\{.*\}", content, flags=re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
        out: Dict[str, str] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, str) and v.strip() and v.strip().upper() != "UNKNOWN":
                    out[k.strip()] = v.strip()
        return out
    except Exception as e:
        print(f"[gpt] ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        return {}

def _context_snippets(retriever, room_names: List[str]) -> List[str]:
    if not retriever: return []
    snippets: List[str] = []
    try:
        for q in set(room_names):
            docs = retriever.get_relevant_documents(q)
            for d in docs[:2]:
                page = getattr(d, "page_content", "")
                if page: snippets.append(page)
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
        if key in cache and isinstance(cache[key], str) and cache[key].strip() and cache[key].strip().upper() != "UNKNOWN":
            return cache[key]
        # miss -> GPT
        fresh = _classify_with_gpt([key], context_snippets=_context_snippets(_load_faiss_retriever(), [key]))
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
        if k in cache and isinstance(cache[k], str) and cache[k].strip() and cache[k].strip().upper() != "UNKNOWN":
            out[k] = cache[k].strip()
        else:
            need.append(k)

    if need:
        retriever = _load_faiss_retriever()
        fresh = _classify_with_gpt(need, context_snippets=_context_snippets(retriever, need))
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
            print("{}", end=""); sys.exit(0)
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