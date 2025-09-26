#!/usr/bin/env python
import os
import sys
import json
import re
from typing import Dict, List, Optional

# Azure OpenAI + LangChain (community or core, both imports supported)
try:
    from langchain_community.vectorstores import FAISS
    from langchain_community.embeddings import HuggingFaceEmbeddings
except ImportError:
    from langchain.vectorstores import FAISS  # fallback
    from langchain.embeddings import HuggingFaceEmbeddings  # fallback

from openai import AzureOpenAI
try:
    from dotenv import load_dotenv, find_dotenv
except Exception:
    # If python-dotenv isn't installed in langchain_venv, install it once:
    # pip install python-dotenv
    raise

# Load the nearest .env; usecwd ensures it searches from the current working dir
dotenv_path = find_dotenv(usecwd=True)
loaded = load_dotenv(dotenv_path)

# Helpful debug to STDERR so it won’t pollute JSON on STDOUT
print(f"[env] .env loaded={loaded} path={dotenv_path or '(none)'}", file=sys.stderr, flush=True)

# Also mirror/normalize Azure variable names so the OpenAI SDK can find them
def _alias_env(src: str, dst: str):
    if not os.getenv(dst) and os.getenv(src):
        os.environ[dst] = os.environ[src]

# Accept both your names and the SDK’s preferred names
_alias_env("AZURE_API_KEY", "AZURE_OPENAI_API_KEY")
_alias_env("AZURE_ENDPOINT", "AZURE_OPENAI_ENDPOINT")
_alias_env("API_VERSION", "AZURE_OPENAI_API_VERSION")
# --- END .env bootstrap ---


# ---------- Utilities ----------

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _resolve_selected_index() -> str:
    """
    Accepts SELECTED_CODE_PDF with or without .pdf.
    Returns the base name (e.g., 'SBC_Code_201').
    """
    sel = os.environ.get("SELECTED_CODE_PDF") or os.environ.get("SELECTED_PDF") or ""
    if not sel:
        raise RuntimeError("Environment variable SELECTED_CODE_PDF must be set (e.g., SBC_Code_201 or SBC_Code_201.pdf)")
    base = os.path.splitext(os.path.basename(sel))[0]
    return base


def _load_faiss() -> FAISS:
    base = _resolve_selected_index()
    folder = os.path.join("vector_db", base)
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"FAISS folder not found: {folder}")

    eprint(f"[extract_max_occupancy] Loading FAISS: folder={folder}, index_name={base}")
    return FAISS.load_local(
        folder_path=folder,
        embeddings=HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2"),
        index_name=base,
        allow_dangerous_deserialization=True,
    )

def _mask(s: str, keep: int = 4) -> str:
    if not s:
        return ""
    return s[:keep] + "*" * max(0, len(s) - keep)

def _azure_client() -> AzureOpenAI:
    """
    Resolves credentials for Azure OpenAI robustly.
    Accepts either AZURE_OPENAI_API_KEY (preferred) or AZURE_API_KEY (legacy).
    """
    api_key = (
        os.getenv("AZURE_OPENAI_API_KEY")
        or os.getenv("AZURE_API_KEY")           # your current .env
        or os.getenv("OPENAI_API_KEY")          # last-ditch if someone set this
    )
    endpoint = (
        os.getenv("AZURE_OPENAI_ENDPOINT")
        or os.getenv("AZURE_ENDPOINT")          # your current .env
        or os.getenv("OPENAI_API_BASE")         # legacy name some setups use
    )
    api_version = (
        os.getenv("AZURE_OPENAI_API_VERSION")
        or os.getenv("API_VERSION")             # your current .env
        or os.getenv("OPENAI_API_VERSION")      # legacy
    )

    # Helpful debug (stderr only, masked)
    print(
        "[azure-client] endpoint=", endpoint,
        " api_version=", api_version,
        " api_key=", _mask(api_key),
        file=sys.stderr, flush=True
    )

    if not api_key:
        raise RuntimeError(
            "Missing Azure OpenAI key. Set AZURE_OPENAI_API_KEY (preferred) "
            "or AZURE_API_KEY in your environment."
        )
    if not endpoint:
        raise RuntimeError(
            "Missing Azure endpoint. Set AZURE_OPENAI_ENDPOINT (preferred) or AZURE_ENDPOINT."
        )
    if not api_version:
        raise RuntimeError(
            "Missing API version. Set AZURE_OPENAI_API_VERSION (preferred) or API_VERSION."
        )

    # New OpenAI SDK (>=1.0) Azure usage
    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )



# def _ask_gpt_for_max_occ(group_name: str, db: FAISS, client: AzureOpenAI) -> Optional[int]:
#     docs = db.similarity_search(f"maximum occupant load allowed for {group_name}", k=5)
#     if not docs:
#         eprint(f"[extract_max_occupancy] no docs for group: {group_name}")
#         return None

#     ctx = "\n\n".join(d.page_content for d in docs)
#     prompt = f"""
# You are a building code expert. From the context below, return ONLY the maximum occupant load value (a single integer) for the classification group "{group_name}".

# Context:
# {ctx}
# """

#     try:
#         resp = client.chat.completions.create(
#             model=os.getenv("DEPLOYMENT"),
#             messages=[
#                 {"role": "system", "content": "Return only an integer. No words."},
#                 {"role": "user", "content": prompt.strip()},
#             ],
#             temperature=0
#         )
#         content = (resp.choices[0].message.content or "").strip()
#         m = re.search(r"\d+", content)
#         if not m:
#             eprint(f"[extract_max_occupancy] no integer in GPT response: {content!r}")
#             return None
#         return int(m.group(0))
#     except Exception as ex:
#         eprint(f"[extract_max_occupancy] GPT error for {group_name}: {ex}")
#         return None
    
def _ask_gpt_for_max_occ(group_name: str, db: FAISS, client: AzureOpenAI) -> Optional[int]:
    # Build two queries: broad and egress-focused fallback
    q1 = f"maximum permitted occupant load threshold for {group_name} means of egress number of exits shall not exceed more than occupants"
    q2 = f"{group_name} occupant load threshold 'shall not exceed' 'more than' 'number of exits' 'means of egress' SBC Chapter 10"

    def retrieve(q: str, k: int = 20):
        try:
            hits = db.similarity_search_with_score(q, k=k)
            docs = [d for d, _ in hits]
        except Exception:
            docs = db.similarity_search(q, k=k)
        return docs

    docs = retrieve(q1)

    g = group_name.strip()
    group_patterns = [
        rf"\b{re.escape(g)}\b",
        rf"\bGROUP\s*{re.escape(g.split()[-1])}\b" if " " in g else rf"\bGROUP\s*{re.escape(g)}\b",
    ]
    trigger_patterns = [
        r"\boccup(an|e)nt(s)?\b",
        r"\bmeans of egress\b",
        r"\bnumber of exits\b",
        r"\bexit(s)? access\b",
        r"\bshall not exceed\b",
        r"\bnot more than\b",
        r"\bmore than\b",
        r"\blimit\b",
        r"\bthreshold\b",
        r"\bmaximum\b",
        r"\bcap\b",
        r"\brequires two exits\b",
        r"\brequires (an|two|multiple) exit(s)?\b",
    ]
    negative_patterns = [
        r"\blive load\b",
        r"\bpartition load\b",
        r"\bvehicle\b",
        r"\bdead load\b",
        r"\bstructural\b",
        r"\bload combination\b",
        r"\brisk category\b",
        r"\bfuel|hazardous|H-?\d\b",
    ]

    def chunk_ok(txt: str) -> bool:
        t = txt.lower()
        if any(re.search(p, t) for p in negative_patterns):
            return False
        gp = any(re.search(p, txt, flags=re.I) for p in group_patterns)
        tp = sum(1 for p in trigger_patterns if re.search(p, txt, flags=re.I))
        return gp and tp > 0

    def score_chunk(txt: str) -> int:
        s = 0
        s += 3 * sum(1 for p in group_patterns if re.search(p, txt, flags=re.I))
        s += 2 * sum(1 for p in trigger_patterns if re.search(p, txt, flags=re.I))
        if re.search(r"\bshall not exceed\b|\bnot more than\b|\brequires two exits\b", txt, flags=re.I):
            s += 4
        if re.search(r"\bSection\s*100\d|\bChapter\s*10\b", txt, flags=re.I):
            s += 2
        return s

    filtered = []
    for i, d in enumerate(docs):
        txt = (d.page_content or "")
        if chunk_ok(txt):
            filtered.append((score_chunk(txt), i, d))
    filtered.sort(reverse=True, key=lambda x: x[0])

    if not filtered:
        docs = retrieve(q2)
        filtered = []
        for i, d in enumerate(docs):
            txt = (d.page_content or "")
            if chunk_ok(txt):
                filtered.append((score_chunk(txt), i, d))
        filtered.sort(reverse=True, key=lambda x: x[0])

    filtered_docs = [d for _, _, d in filtered[:6]] if filtered else docs[:3]

    # Build final context & prompt (no logging of the context)
    ctx = "\n\n".join((d.page_content or "").strip() for d in filtered_docs)
    prompt = f"""
You are a building code expert. Using ONLY the excerpts in Context, extract the **hard cap** (integer) for the *maximum permitted occupant load* for a single room/space classified as "{group_name}".

Rules:
- Return **only** the integer (no words, units, or punctuation).
- If several numbers appear, choose the one that is a **limit/threshold** stated with wording like:
  "shall not exceed", "not more than", "more than … occupants requires two exits", "limit", "cap".
- **Do NOT** compute from occupant-load factors or floor-area-per-person tables (ignore phrases like “per person”, “m² per person”, “occupant load factor”).
- Prefer canonical egress thresholds **only if** the text clearly states them as a limit.

Context:
{ctx}
""".strip()

    try:
        resp = client.chat.completions.create(
            model=os.getenv("DEPLOYMENT"),
            messages=[
                {"role": "system", "content": "Return only an integer. No words."},
                {"role": "user", "content": prompt},
            ],
            temperature=0
        )
        content = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\d+", content)
        if not m:
            # minimal warning only
            print(f"[extract_max_occupancy] no integer in GPT response for {group_name!r}", file=sys.stderr)
            return None
        return int(m.group(0))
    except Exception as ex:
        print(f"[extract_max_occupancy] GPT error for {group_name}: {ex}", file=sys.stderr)
        return None




def run_batch(groups: List[str]) -> Dict[str, Optional[int]]:
    db = _load_faiss()
    client = _azure_client()
    out: Dict[str, Optional[int]] = {}
    for g in groups:
        key = (g or "").strip()
        if not key:
            out[g] = None
            continue
        val = _ask_gpt_for_max_occ(key, db, client)
        out[key] = val
    return out


# ---------- CLI ----------

"""
Usage (parent process):
    python extract_max_occupancy.py "[\"Group B\",\"Group E\"]"

- Pass a single JSON array string as argv[1].
- Script prints exactly ONE JSON object to stdout like:
    {"Group B": 49, "Group E": 35}
- Any debug messages go to stderr.
"""

if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            raise SystemExit("expected 1 JSON-encoded array argument, e.g. [\"Group B\",\"Group E\"]")

        try:
            groups = json.loads(sys.argv[1])
            if not isinstance(groups, list):
                raise ValueError("first argument must be a JSON list")
        except Exception as ex:
            raise SystemExit(f"invalid JSON argument: {ex}")

        result = run_batch(groups)
        # IMPORTANT: print only ONE line of JSON to stdout)
        print(json.dumps(result, separators=(",", ":")))
    except SystemExit as se:
        eprint(f"[extract_max_occupancy] {se}")
        # print empty result to stdout so caller doesn't hang
        print("{}")
        sys.exit(0)
    except Exception as ex:
        eprint(f"[extract_max_occupancy] fatal: {ex}")
        print("{}")
        sys.exit(0)