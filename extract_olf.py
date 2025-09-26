#!/usr/bin/env python
import os
import sys
import json
import re
from typing import Dict, List, Optional, Tuple

# LangChain: prefer community; fall back to core if needed
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


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _resolve_selected_index() -> str:
    sel = os.environ.get("SELECTED_CODE_PDF") or os.environ.get("SELECTED_PDF") or ""
    if not sel:
        raise RuntimeError("Environment variable SELECTED_CODE_PDF must be set (e.g., SBC_Code_201 or SBC_Code_201.pdf)")
    return os.path.splitext(os.path.basename(sel))[0]


def _load_faiss() -> FAISS:
    base = _resolve_selected_index()
    folder = os.path.join("vector_db", base)
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"FAISS folder not found: {folder}")
    eprint(f"[extract_olf] Loading FAISS: folder={folder}, index_name={base}")
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



def _ask_gpt_for_olf(room_name: str, db: FAISS, client: AzureOpenAI) -> Tuple[Optional[float], Optional[str]]:
    query = f"Occupant load factor table or maximum floor area per occupant for a space similar to {room_name}"
    docs = db.similarity_search(query, k=5)
    if not docs:
        eprint(f"[extract_olf] no docs for room: {room_name}")
        return None, None

    ctx = "\n\n".join(d.page_content for d in docs)
    prompt = f"""
You are a building code expert.
Using ONLY the context below, choose the most suitable Occupant Load Factor (OLF) for the room "{room_name}".

Respond strictly as:
OLF: <value> <unit>

Examples:
OLF: 9 gross
OLF: 11 net

Context:
{ctx}
"""

    try:
        resp = client.chat.completions.create(
            model=os.getenv("DEPLOYMENT"),
            messages=[
                {"role": "system", "content": "Return OLF in the exact format 'OLF: <value> <unit>'."},
                {"role": "user", "content": prompt.strip()},
            ],
            temperature=0
        )
        content = (resp.choices[0].message.content or "").strip()
        m = re.search(r"OLF:\s*([\d.]+)\s+(\w+)", content, re.IGNORECASE)
        if not m:
            eprint(f"[extract_olf] unparsable GPT response for {room_name}: {content!r}")
            return None, None
        return float(m.group(1)), m.group(2)
    except Exception as ex:
        eprint(f"[extract_olf] GPT error for {room_name}: {ex}")
        return None, None


def run_batch(room_names: List[str]) -> Dict[str, List[Optional[str]]]:
    db = _load_faiss()
    client = _azure_client()
    out: Dict[str, List[Optional[str]]] = {}
    for rn in room_names:
        key = (rn or "").strip()
        if not key:
            out[rn] = [None, None]
            continue
        val, unit = _ask_gpt_for_olf(key, db, client)
        # store as [value, unit] to match your cache shape
        out[key] = [val, unit]
    return out


"""
Usage (parent):
    python extract_olf.py "[\"Classroom\",\"Office\",\"WC\"]"

- One JSON list string arg.
- Prints exactly ONE JSON object to stdout:
    {"Classroom":[9,"gross"],"Office":[100,"gross"],"WC":[50,"net"]}
- Debug goes to stderr.
"""
if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            raise SystemExit("expected 1 JSON-encoded array argument, e.g. [\"Classroom\",\"Office\"]")

        try:
            names = json.loads(sys.argv[1])
            if not isinstance(names, list):
                raise ValueError("first argument must be a JSON list")
        except Exception as ex:
            raise SystemExit(f"invalid JSON argument: {ex}")

        result = run_batch(names)
        print(json.dumps(result, separators=(",", ":")))
    except SystemExit as se:
        eprint(f"[extract_olf] {se}")
        print("{}")
        sys.exit(0)
    except Exception as ex:
        eprint(f"[extract_olf] fatal: {ex}")
        print("{}")
        sys.exit(0)
