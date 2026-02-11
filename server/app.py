from fastapi import FastAPI, Request, File, Query, UploadFile, Header, HTTPException
import requests
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from specklepy.api.client import SpeckleClient
from typing import Any, Dict, List
from pathlib import Path
from dotenv import load_dotenv, set_key
import subprocess
import shutil
from urllib.parse import urlparse
from urllib.request import urlopen
import base64, hashlib, secrets
import datetime
import sys
import os
import re
import json
import time
import pickle

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # "http://verifire.dar.com:3000",  # Vite dev
        "http://verifire.dar.com",       # if you also serve without :3000
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load environment variables
# load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)
print(f"[env] Loaded .env from: {ENV_PATH} | DEV={os.environ.get('DEV')!r}")

def _sanitize_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (s or "").strip())

SPECKLE_SERVER_URL = os.getenv("SPECKLE_SERVER_URL")
PROJECT_ID = os.getenv("PROJECT_ID")
MODEL_ID = os.getenv("MODEL_ID")
BRANCH_NAME = os.getenv("BRANCH_NAME", "main")
SPECKLE_TOKEN_STG = os.getenv("SPECKLE_TOKEN_STG")

SPECKLE_CLIENT_ID = os.getenv("SPECKLE_CLIENT_ID")
SPECKLE_CLIENT_SECRET = os.getenv("SPECKLE_CLIENT_SECRET")


# BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = Path(__file__).resolve().parent.parent

# Absolute paths to the scripts
SCRIPT_GRID  = BASE_DIR / "run_grid_main.py"
SCRIPT_PATHS = BASE_DIR / "run_paths_main.py"
SCRIPT_FLS = BASE_DIR / "run_fls_main.py"

# ProgramData target for app data
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\VeriFire")
GRAPHS_DIR = Path(os.getenv("VERIFIRE_GRAPHS_DIR", str(PROGRAMDATA_ROOT / "graphs")))
USER_INPUTS_PATH = PROGRAMDATA_ROOT / "user_inputs.json"
PDF_DIR = PROGRAMDATA_ROOT / "pdfs"
VDB_DIR = PROGRAMDATA_ROOT / "vector_db"

# make sure folders exist
for p in (PDF_DIR, VDB_DIR):
    p.mkdir(parents=True, exist_ok=True)

def _has_faiss_files(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    # common FAISS artifacts
    names = {f.name.lower() for f in folder.iterdir() if f.is_file()}
    return any(n.endswith(".faiss") for n in names) or \
           "index.faiss" in names or "index.pkl" in names or "docstore.pkl" in names

def _ns_root(project_id: str, model_id: str) -> Path:
    proj = (project_id or "").strip()
    model = ((model_id or "").strip()).split("@", 1)[0]
    if not proj or not model:
        raise ValueError("Missing project_id or model_id")
    return PROGRAMDATA_ROOT / f"{proj}_{model}"

# --- add near top-level helpers in api.py ---
def _get_user_token_from_header(x_user_id: str | None) -> str | None:
    uid = (x_user_id or "").strip()
    if not uid:
        return None
    u = users_store.get(uid)
    return (u or {}).get("token")

def _sanitize(s: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (s or "").strip())

def _graphs_dir_for(project_id: str, model_id: str) -> Path:
    pm = f"{_sanitize(project_id)}_{_sanitize(model_id.split('@', 1)[0])}"
    return PROGRAMDATA_ROOT / pm / "graphs"


# --- Reports helpers (ProgramData-aware) ---

def _reports_dir_for(project_id: str, model_id: str) -> Path:
    pm = f"{_sanitize(project_id)}_{_sanitize(model_id.split('@', 1)[0])}"
    return PROGRAMDATA_ROOT / pm / "reports"


def _latest_report_pdf(reports_dir: Path) -> Path | None:
    """Return the newest consolidated FLS report PDF in a reports directory."""
    try:
        if not reports_dir.exists():
            return None
        pdfs = [p for p in reports_dir.glob("fls_compliance_report_*.pdf") if p.is_file()]
        if not pdfs:
            # fallback: any pdf
            pdfs = [p for p in reports_dir.glob("*.pdf") if p.is_file()]
        if not pdfs:
            return None
        pdfs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return pdfs[0]
    except Exception:
        return None


# Utility to run Python scripts and capture output
def run_python_script(script_path: Path, args: list[str] | None = None, extra_env: dict | None = None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"]       = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    if extra_env:
        env.update(extra_env)

    # Per-run ProgramData log directory: C:\ProgramData\VeriFire\<PROJECT_NAME>_<MODEL_NAME>\logs
    proj_name  = env.get("PROJECT_ID") or "UNKNOWN_PROJECT"
    model_name = env.get("MODEL_ID")  or "UNKNOWN_MODEL"
    proj_key   = f"{_sanitize_name(proj_name)}_{_sanitize_name(model_name)}"
    run_log_dir = (PROGRAMDATA_ROOT / proj_key / "logs")
    run_log_dir.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-u", str(script_path)]
    if args:
        cmd.extend(args)

    print(f"[runner] CWD={BASE_DIR}")
    print(f"[runner] CMD={' '.join(cmd)}")
    print(f"[runner] ENV.PROJECT_ID={env.get('PROJECT_ID')} ENV.MODEL_ID={env.get('MODEL_ID')}")
    print(f"[runner] ENV.DEV={env.get('DEV')!r}")

    result = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Log file named after the script
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    script_stem = Path(script_path).stem  # e.g., "run_grid_main"
    log_path = run_log_dir / f"{script_stem}_{ts}.log"

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"CWD: {BASE_DIR}\nCMD: {' '.join(cmd)}\nRET: {result.returncode}\n")
        f.write("\n--- STDOUT ---\n")
        f.write(result.stdout or "")
        f.write("\n--- STDERR ---\n")
        f.write(result.stderr or "")
    print(f"[runner] Wrote log: {log_path}")

    payload = {
        "cmd": cmd,
        "cwd": str(BASE_DIR),
        "env_ids": {"PROJECT_ID": env.get("PROJECT_ID"), "MODEL_ID": env.get("MODEL_ID")},
        "returncode": result.returncode,
        "stdout": (result.stdout or "")[-12000:],
        "stderr": (result.stderr or "")[-12000:],
        "log_file": str(log_path),
    }
    return payload


# --- PKCE helper (HTTP-safe) ---
def _b64url_no_pad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

@app.get("/auth/pkce/start")
def pkce_start():
    # RFC 7636: verifier length 43–128 chars; use 64 url-safe chars
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode()).digest())
    return {"verifier": verifier, "challenge": challenge}

# 🔍 Optional debug endpoint to verify paths
@app.get("/debug/paths")
def debug_paths():
    graph_dir = os.path.join(BASE_DIR, "graphs")
    return {
        "cwd": os.getcwd(),
        "base_dir": BASE_DIR,
        "scripts_found": os.listdir(BASE_DIR),
        "graph_files": os.listdir(graph_dir) if os.path.exists(graph_dir) else "graphs/ not found"
    }

@app.get("/project/current")
def project_current(
    request: Request,
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
):
    """
    Return the currently effective PROJECT_ID / MODEL_ID as seen by this worker.
    Priority: explicit headers -> query params -> in-process env vars.
    """
    # 1) headers (lets the client test/override explicitly)
    proj  = (x_project_id or "").strip()
    model = (x_model_id or "").strip()

    # 2) query params (so you can hit it directly in the browser if needed)
    if not proj:
        proj = (request.query_params.get("project_id") or request.query_params.get("project") or "").strip()
    if not model:
        model = (request.query_params.get("model_id") or request.query_params.get("model") or "").strip()

    # 3) process env (what /set-project already sets in this worker)
    if not proj:
        proj = (os.environ.get("PROJECT_ID") or "").strip()
    if not model:
        model = (os.environ.get("MODEL_ID") or "").strip()

    model = model.split("@", 1)[0]

    if not proj or not model:
        # 404 is intentional: signals "not set" to the client
        return JSONResponse(status_code=404, content={"error": "Not set"})

    return {"project_id": proj, "model_id": model}

@app.get("/debug/user-inputs")
def read_user_inputs():
    try:
        if not USER_INPUTS_PATH.exists():
            return {"exists": False, "path": str(USER_INPUTS_PATH)}
        text = USER_INPUTS_PATH.read_text(encoding="utf-8", errors="strict")
        # Also return JSON-parsed content (if valid)
        try:
            parsed = json.loads(text)
        except Exception as je:
            parsed = None
        return {
            "exists": True,
            "path": str(USER_INPUTS_PATH),
            "bytes": len(text.encode("utf-8")),
            "parsed_ok": parsed is not None,
            "parsed_keys": list(parsed.keys()) if isinstance(parsed, dict) else [],
            "raw_head": text[:200]  # small preview
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})


# 🔁 Speckle Viewer Commit URL
@app.get("/config")
def get_config(x_user_id: str | None = Header(None)):
    token = _get_user_token_from_header(x_user_id)
    if not token:
        return JSONResponse(status_code=401, content={"error": "Not authenticated. Please log into Speckle."})

    client = SpeckleClient(host=SPECKLE_SERVER_URL)
    client.authenticate_with_token(token)

    branch = client.branch.get(PROJECT_ID, MODEL_ID)
    default_commit = branch.commits.items[-1] if branch.commits.items else None
    if not default_commit:
        return {"error": "No commits found"}

    commit_id = default_commit.id
    url = f"{SPECKLE_SERVER_URL}/projects/{PROJECT_ID}/models/{MODEL_ID}/commits/{commit_id}#embed=%7B%22isEnabled%22%3Atrue%7D"
    return {"url": url}


def resolve_ids(request: Request) -> tuple[str, str]:
    # 1) headers
    pid = request.headers.get("x-project-id")
    mid = request.headers.get("x-model-id")

    # 2) query params (fallback)
    if not pid:
        pid = request.query_params.get("project_id")
    if not mid:
        mid = request.query_params.get("model_id")

    # 3) server env (fallback; e.g. set by /set-project or your runner)
    if not pid:
        pid = os.getenv("PROJECT_ID")
    if not mid:
        mid = os.getenv("MODEL_ID")

    # normalize
    if mid:
        mid = mid.split("@", 1)[0]

    if not pid or not mid:
        raise ValueError("Missing project/model id")

    return pid.strip(), mid.strip()

@app.get("/graph/floors")
def get_floors(
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
    project_id:   str | None = Query(None),
    model_id:     str | None = Query(None),
):
    """
    Lists floors by reading G_<Level>.pkl from:
      ProgramData/VeriFire/<PROJECT_ID>_<MODEL_ID>/graphs
    Project/Model are taken from headers X-Project-Id / X-Model-Id,
    or from query params ?project_id=&model_id= (for testing).
    """
    try:
        proj = (x_project_id or project_id or "").strip()
        model = (x_model_id   or model_id   or "").strip()

        if not proj or not model:
            # Keep behavior predictable: no IDs → no graphs.
            return {"floors": []}

        graphs_dir = _graphs_dir_for(proj, model)
        if not graphs_dir.exists():
            return {"floors": []}

        floors: list[str] = []
        for p in sorted(graphs_dir.glob("G_*.pkl")):
            stem = p.stem  # "G_<LevelName>"
            level_name = stem.split("_", 1)[-1] if "_" in stem else stem
            floors.append(level_name)

        return {"floors": floors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list floors: {e}")

# add these imports near your other imports
from pathlib import Path
import os, json, tempfile, time, sys, getpass, traceback

PROGRAMDATA_ROOT = Path(r"C:\ProgramData\VeriFire")
USER_INPUTS_DIR  = PROGRAMDATA_ROOT / "user_inputs"
USER_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
USER_INPUTS_PATH = USER_INPUTS_DIR / "user_inputs.json"

@app.post("/save-user-inputs")
async def save_user_inputs(request: Request):
    # Parse JSON body
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")

    # Write atomically to a temp file in the same directory, then replace
    tmp_path = USER_INPUTS_DIR / f"user_inputs_{int(time.time())}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Atomic replace (same volume)
        os.replace(tmp_path, USER_INPUTS_PATH)
    finally:
        # Best-effort cleanup if something goes wrong before replace
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    return {"status": "✅ Inputs saved", "path": str(USER_INPUTS_PATH)}

@app.get("/fls/pdfs")
def list_embedded_pdfs():
    """
    Lists vector DBs under ProgramData\vector_db.
    Each subfolder is a selectable code base (e.g., SBC_Code_201).
    """
    if not VDB_DIR.exists():
        return {"pdfs": []}
    pdfs = sorted([d.name for d in VDB_DIR.iterdir() if _has_faiss_files(d)])
    return {"pdfs": pdfs}


@app.get("/fls/report/status")
def fls_report_status(
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
    x_user_id:    str | None = Header(None),
):
    """Return whether a consolidated FLS PDF report exists for the current project/model."""
    # Require auth (consistent with other FLS endpoints)
    token = _get_user_token_from_header(x_user_id)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or unknown user; no token available")

    proj  = (x_project_id or "").strip()
    model = (x_model_id or "").strip().split("@", 1)[0]
    if not proj or not model:
        raise HTTPException(status_code=400, detail="Missing X-Project-Id / X-Model-Id")

    reports_dir = _reports_dir_for(proj, model)
    pdf = _latest_report_pdf(reports_dir)

    if not pdf:
        return {"available": False, "filename": None}

    try:
        ts = int(pdf.stat().st_mtime)
    except Exception:
        ts = None

    return {"available": True, "filename": pdf.name, "mtime": ts}


@app.get("/fls/report/download")
def fls_report_download(
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
    x_user_id:    str | None = Header(None),
):
    """Download the latest consolidated FLS PDF report for the current project/model."""
    token = _get_user_token_from_header(x_user_id)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or unknown user; no token available")

    proj  = (x_project_id or "").strip()
    model = (x_model_id or "").strip().split("@", 1)[0]
    if not proj or not model:
        raise HTTPException(status_code=400, detail="Missing X-Project-Id / X-Model-Id")

    reports_dir = _reports_dir_for(proj, model)
    pdf = _latest_report_pdf(reports_dir)
    if not pdf:
        raise HTTPException(status_code=404, detail="Report not found. Run FLS check first.")

    return FileResponse(
        path=str(pdf),
        media_type="application/pdf",
        filename=pdf.name,
    )

@app.post("/fls/upload")
async def upload_and_embed_pdf(pdf: UploadFile = File(...)):
    """
    Saves PDF to ProgramData/pdfs and runs the embedder so its FAISS index
    is written to ProgramData/vector_db/<basename>.
    """
    if not (pdf.filename and pdf.filename.lower().endswith(".pdf")):
        return {"status": "❌ Invalid file format. Please upload a PDF."}

    # Save PDF in ProgramData
    dest_pdf = PDF_DIR / pdf.filename
    try:
        with dest_pdf.open("wb") as f:
            shutil.copyfileobj(pdf.file, f)
    except Exception as e:
        return {
            "status": "❌ Failed to save PDF",
            "error": str(e)
        }

    # Launch embed subprocess
    embed_script = Path(BASE_DIR) / "embedding" / "embed_pdf_and_index.py"
    venv_python  = Path(BASE_DIR) / "langchain_venv" / "Scripts" / "python.exe"

    # Check if venv python exists
    if not venv_python.exists():
        return {
            "status": "❌ Python venv not found",
            "error": f"Expected venv at: {venv_python}",
            "hint": "Run: python -m venv langchain_venv"
        }

    if not embed_script.exists():
        return {
            "status": "❌ Embed script not found",
            "error": f"Expected script at: {embed_script}"
        }

    # Environment: point scripts to ProgramData
    env = os.environ.copy()
    env["VECTOR_DB_DIR"] = str(VDB_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # Command with correct arguments
    cmd = [
        str(venv_python),
        str(embed_script),
        str(dest_pdf),
        "--outdir", str(VDB_DIR)
    ]

    print(f"[fls/upload] Running: {' '.join(cmd)}")
    print(f"[fls/upload] VDB_DIR: {VDB_DIR}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(BASE_DIR),
            timeout=3600  # 1 hour max for large PDFs
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "❌ Embedding timed out (>1 hour)",
            "hint": "Try processing fewer pages with --start-page/--end-page"
        }
    except Exception as e:
        return {
            "status": "❌ Failed to run embedding script",
            "error": str(e)
        }

    if proc.returncode != 0:
        # Surface detailed error info
        return {
            "status": "❌ Failed to embed PDF",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-2000:] if proc.stdout else "",  # last 2000 chars
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
            "cmd": " ".join(cmd),
            "hint": "Check if Qwen/Ollama is running and accessible"
        }

    # Success
    index_path = VDB_DIR / Path(pdf.filename).stem
    return {
        "status": f"✅ Embedded {pdf.filename}",
        "index_path": str(index_path),
        "stdout": proc.stdout[-1000:] if proc.stdout else "",  # last 1000 chars
    }


@app.post("/fls/pdf-selection")
async def save_selected_pdf(request: Request):
    data = await request.json()
    selected_pdf = data.get("selected_pdf")
    if not selected_pdf:
        return {"error": "Missing PDF selection."}
    with open("selected_pdf.json", "w", encoding="utf-8") as f:
        json.dump({"selected_pdf": selected_pdf}, f, indent=2)
    return {"status": f"✅ PDF '{selected_pdf}' selected."}




# /run/grid — require per-user token
@app.post("/run/grid")
def run_grid(
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
    x_user_id:    str | None = Header(None),
):
    proj  = (x_project_id or "").strip()
    model = (x_model_id or "").strip().split("@", 1)[0]

    extra_env: dict[str, str] = {}
    if proj and model:
        extra_env.update({"PROJECT_ID": proj, "MODEL_ID": model})

    token = _get_user_token_from_header(x_user_id)
    if not token:
        return JSONResponse(status_code=401, content={"error": "Missing or unknown user; no token available"})

    extra_env["SPECKLE_USER_TOKEN"] = token

    payload = run_python_script(
        SCRIPT_GRID,
        args=(["--project-id", proj, "--model-id", model] if proj and model else []),
        extra_env=extra_env,
    )
    if payload["returncode"] != 0:
        return JSONResponse(status_code=500, content=payload)
    return payload


# /run/paths
@app.post("/run/paths")
def run_paths(
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
    x_user_id:    str | None = Header(None),
):
    proj  = (x_project_id or "").strip()
    model = (x_model_id or "").strip().split("@", 1)[0]

    extra_env: dict[str, str] = {}
    if proj and model:
        extra_env.update({"PROJECT_ID": proj, "MODEL_ID": model})

    # 🔐 per-user Speckle token (required)
    token = _get_user_token_from_header(x_user_id)
    if not token:
        return JSONResponse(
            status_code=401,
            content={"error": "Missing or unknown user; no token available"}
        )
    extra_env["SPECKLE_USER_TOKEN"] = token

    payload = run_python_script(
        SCRIPT_PATHS,
        args=(["--project-id", proj, "--model-id", model] if proj and model else []),
        extra_env=extra_env,
    )
    if payload["returncode"] != 0:
        return JSONResponse(status_code=500, content=payload)
    return payload



# /run/fls
@app.post("/run/fls")
def run_fls(
    pdf_id: str = Query(...),
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
    x_user_id:    str | None = Header(None),
):
    proj  = (x_project_id or "").strip()
    model = (x_model_id or "").strip().split("@", 1)[0]

    extra_env: dict[str, str] = {
        "PYTHONIOENCODING": "utf-8",
        "SELECTED_CODE_PDF": pdf_id,   # which PDF to use
    }
    if proj and model:
        extra_env["PROJECT_ID"] = proj
        extra_env["MODEL_ID"]   = model

    # 🔐 per-user Speckle token (REQUIRED)
    token = _get_user_token_from_header(x_user_id)
    if not token:
        return JSONResponse(
            status_code=401,
            content={"error": "Missing or unknown user; no token available"}
        )
    extra_env["SPECKLE_USER_TOKEN"] = token

    payload = run_python_script(
        SCRIPT_FLS,
        args=[],
        extra_env=extra_env,
    )

    # Attach report info (best-effort)
    try:
        reports_dir = _reports_dir_for(proj, model) if (proj and model) else None
        pdf = _latest_report_pdf(reports_dir) if reports_dir else None
        payload["report"] = {
            "available": bool(pdf),
            "filename": (pdf.name if pdf else None),
        }
    except Exception:
        payload["report"] = {"available": False, "filename": None}

    if payload["returncode"] != 0:
        return JSONResponse(status_code=500, content=payload)
    return payload


@app.post("/set-project")
async def set_project(request: Request):
    data = await request.json()
    project_id = (data.get("project_id") or "").strip()
    model_id   = (data.get("model_id") or "").strip()
    if not project_id or not model_id:
        return JSONResponse(status_code=400, content={"error": "Missing project_id or model_id"})

    clean_model_id = model_id.split("@", 1)[0]

    # (Optional) set in-process env for *this* worker only, if you still read from os.environ
    os.environ["PROJECT_ID"] = project_id
    os.environ["MODEL_ID"]   = clean_model_id

    return {"status": "ok", "project_id": project_id, "model_id": clean_model_id}


# In-memory store: { speckle_user_id: { token, refresh_token, email, name } }
users_store = {}

@app.post("/auth/speckle/exchange")
async def exchange_token(payload: dict):
    code = payload.get("code")
    challenge = payload.get("challenge")

    print(f"[Backend] 📥 Received exchange request:")
    print(f"         code = {code}")
    print(f"    code_challenge = {challenge}")

    if not code or not challenge:
        print("[Backend] ❌ Missing code or challenge in request")
        return {"error": "Missing code or challenge"}

    try:
        # Step 1 — Exchange access code for token
        url = f"{SPECKLE_SERVER_URL}/auth/token"
        body = {
            "appId": SPECKLE_CLIENT_ID,
            "appSecret": SPECKLE_CLIENT_SECRET,
            "accessCode": code,
            "challenge": challenge
        }

        print(f"[Backend] 🌐 Sending POST to Speckle: {url}")
        print(f"[Backend] Payload: {body}")

        token_res = requests.post(url, json=body)
        print(f"[Backend] 📡 Speckle responded with status {token_res.status_code}")
        print(f"[Backend] Response body: {token_res.text}")

        if token_res.status_code != 200:
            return {"error": "Token exchange failed", "details": token_res.text}

        token_data = token_res.json()
        access_token = token_data.get("token")
        refresh_token = token_data.get("refreshToken")

        print(f"[Backend] ✅ Token obtained: {access_token}")
        print(f"[Backend] 🔄 Refresh token: {refresh_token}")

        # Step 2 — Fetch user info via GraphQL
        graphql_url = f"{SPECKLE_SERVER_URL}/graphql"
        graphql_query = {
            "query": "{ user { id email name } }"
        }
        print(f"[Backend] 🌐 Fetching user profile via GraphQL at: {graphql_url}")

        profile_res = requests.post(
            graphql_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            json=graphql_query
        )

        print(f"[Backend] 📡 Profile response status: {profile_res.status_code}")
        print(f"[Backend] Profile response body: {profile_res.text}")

        if profile_res.status_code != 200:
            return {"error": "Profile fetch failed", "details": profile_res.text}

        profile_data = profile_res.json()
        user_info = profile_data.get("data", {}).get("user")

        if not user_info:
            return {"error": "No user info returned from Speckle"}

        user_id = user_info.get("id")
        email = user_info.get("email")
        name = user_info.get("name")

        print(f"[Backend] 👤 Speckle user: {name} ({email}), ID: {user_id}")

        # Step 3 — Store user in memory
        users_store[user_id] = {
            "token": access_token,
            "refresh_token": refresh_token,
            "email": email,
            "name": name
        }

        print(f"[Backend] 💾 User {user_id} stored in memory")

        return {
            "user_id": user_id,
            "email": email,
            "name": name, 
            "token": access_token,
            "refresh_token": refresh_token
        }

    except Exception as e:
        print(f"[Backend] 💥 Exception during token exchange: {e}")
        return {"error": str(e)}


@app.get("/auth/whoami")
def whoami(
    x_user_id: str | None = Header(None),
    user_id:   str | None = Query(None),
):
    uid = (x_user_id or user_id or "").strip()
    if not uid:
        return JSONResponse(status_code=401, content={"error": "Missing user id"})

    user = users_store.get(uid)
    if not user:
        return JSONResponse(status_code=404, content={"error": "User not found"})

    return {"user_id": uid, "email": user.get("email"), "name": user.get("name")}

@app.get("/auth/check-access")
def check_access(user_id: str, speckle_url: str):
    print(f"[AccessCheck] Checking access for user_id={user_id}")
    print(f"[AccessCheck] Target URL: {speckle_url}")

    user = users_store.get(user_id)
    if not user:
        print("[AccessCheck] ❌ No user found in memory")
        return {"access": False, "reason": "User not logged in"}

    print(f"[AccessCheck] Found user: {user.get('name')} ({user.get('email')})")

    # ── Parse projectId from Speckle URL ────────────────────────────────────────
    try:
        parsed = urlparse(speckle_url)
        parts = parsed.path.strip("/").split("/")
        print(f"[AccessCheck] Parsed URL parts: {parts}")
        proj_idx = parts.index("projects") + 1
        project_id = parts[proj_idx]
    except (ValueError, IndexError):
        print("[AccessCheck] ❌ Could not parse project ID from URL")
        return {"access": False, "reason": "Invalid Speckle URL"}

    print(f"[AccessCheck] Project ID: {project_id}")

    # ── Build GraphQL query (use 'role'; some servers return 'stream:owner' etc.) ─
    graphql_query = {
        "query": f"""
        query {{
          project(id: "{project_id}") {{
            id
            role
          }}
        }}
        """
    }

    # ── Call Speckle GraphQL API ───────────────────────────────────────────────
    try:
        res = requests.post(
            f"{SPECKLE_SERVER_URL}/graphql",
            headers={
                "Authorization": f"Bearer {user['token']}",
                "Content-Type": "application/json",
            },
            json=graphql_query,
            timeout=15,
        )
    except Exception as e:
        print(f"[AccessCheck] ❌ Exception calling GraphQL: {e}")
        return {"access": False, "reason": "Unable to reach Speckle API"}

    print(f"[AccessCheck] GraphQL status: {res.status_code}")
    print(f"[AccessCheck] GraphQL response: {res.text}")

    # Always return JSON with access flag (no browser alerts from backend)
    if res.status_code != 200:
        return {"access": False, "reason": f"GraphQL error {res.status_code}"}

    # Be defensive if the body isn't valid JSON
    try:
        res_json = res.json()
    except Exception:
        return {"access": False, "reason": "Invalid response from Speckle API"}

    # If GraphQL reports an error, treat as "no access" but keep it quiet
    if "errors" in res_json:
        print(f"[AccessCheck] ❌ GraphQL errors: {res_json['errors']}")
        # Use a friendly message; frontend already shows its own modal
        return {
            "access": False,
            "reason": "You do not have the required permissions to access this project. Please contact the Project Owner.",
            "code": "FORBIDDEN",
        }

    proj_data = res_json.get("data", {}).get("project")
    if not proj_data:
        print("[AccessCheck] ❌ No project data in GraphQL response")
        return {"access": False, "reason": "Project not found"}

    role_raw = proj_data.get("role") or ""
    role = role_raw.lower()
    print(f"[AccessCheck] Role from GraphQL: {role_raw}")

    # Accept roles like "owner", "contributor", "stream:owner", "stream:contributor"
    if ("owner" in role) or ("contributor" in role):
        print("[AccessCheck] ✅ Access granted")
        return {"access": True}

    print("[AccessCheck] ❌ Insufficient permissions")
    return {
        "access": False,
        "reason": f"Insufficient permissions (role: {role_raw})",
        "code": "FORBIDDEN",
    }


# --- projects (streams) ---
@app.get("/speckle/projects")
def list_user_projects(
    x_user_id: str | None = Header(None),
    user_id:   str | None = Query(None),
) -> Dict[str, List[Dict[str, Any]]]:
    uid = (x_user_id or user_id or "").strip()
    if not uid:
        return JSONResponse(status_code=401, content={"error": "Missing user id"})

    token = _get_user_token_from_header(uid) or (users_store.get(uid) or {}).get("token")
    if not token:
        return JSONResponse(status_code=401, content={"error": "Not authenticated. Please log into Speckle."})

    client = SpeckleClient(host=SPECKLE_SERVER_URL)
    client.authenticate_with_token(token)

    projects: List[Dict[str, Any]] = []

    # Support both shapes:
    #  - old: client.stream.list() -> List[Stream]
    #  - newer: client.stream.list() -> { items: [Stream], cursor: ... }
    try:
        res = client.stream.list()  # no args for max compatibility
    except TypeError:
        # Some very old builds expose .streams.list()
        res = getattr(client, "streams", client.stream).list()

    items = getattr(res, "items", None)
    if items is None:
        items = res  # assume it's already a list[Stream]

    for s in (items or []):
        projects.append({
            "id": getattr(s, "id", None) or "",
            "name": (getattr(s, "name", None) or getattr(s, "id", "") or ""),
        })

    return {"projects": projects}


# --- models (branches) for a given project/stream ---
@app.get("/speckle/projects/{project_id}/models")
def list_project_models(
    project_id: str,
    x_user_id: str | None = Header(None),
    user_id:   str | None = Query(None),
) -> Dict[str, List[Dict[str, Any]]]:
    uid = (x_user_id or user_id or "").strip()
    if not uid:
        return JSONResponse(status_code=401, content={"error": "Missing user id"})

    token = _get_user_token_from_header(uid) or (users_store.get(uid) or {}).get("token")
    if not token:
        return JSONResponse(status_code=401, content={"error": "Not authenticated. Please log into Speckle."})

    client = SpeckleClient(host=SPECKLE_SERVER_URL)
    client.authenticate_with_token(token)

    models: List[Dict[str, Any]] = []

    # Support both shapes:
    #  - old: client.branch.list(stream_id=...) -> List[Branch]
    #  - newer: client.branch.list(stream_id=...) -> { items: [Branch], cursor: ... }
    res = client.branch.list(stream_id=project_id)  # no pagination args
    items = getattr(res, "items", None)
    if items is None:
        items = res  # assume it's already a list[Branch]

    for b in (items or []):
        models.append({
            "id": getattr(b, "id", None) or "",
            "name": (getattr(b, "name", None) or getattr(b, "id", "") or ""),
        })

    return {"models": models}

# ------------ paths & utils ------------
ROOT = Path(r"C:\ProgramData\VeriFire")

def _norm_level(s: str) -> str:
    if not isinstance(s, str):
        return ""
    t = " ".join(s.strip().split()).lower()
    parts = t.split(" ")
    if parts and parts[-1].isdigit():
        parts[-1] = str(int(parts[-1]))  # Level 01 -> level 1
    return " ".join(parts)

def _proj_model_root(proj: str, model: str) -> Path:
    return ROOT / f"{proj}_{model}"

def _graphs_dir_for(proj: str, model: str) -> Path:
    return _proj_model_root(proj, model) / "graphs"

def _graph_pkl_for_floor(graphs_dir: Path, floor_name: str) -> Path:
    # Expect exact file "G_<floor>.pkl" as created by your pipeline
    return graphs_dir / f"G_{floor_name}.pkl"

# ---------- node inspectors (schema-agnostic) ----------
ATTR_IS_DOOR  = ("is_door","door","isDoor")
ATTR_IS_STAIR = ("is_stair","stair","isStair")
ATTR_KIND     = ("kind","node_kind","category","node_category","node_type","type")
ATTR_TAGS     = ("tags","labels")

ID_KEYS       = ("source_id","elementId","ElementId","applicationId","Id","id","node_id")
NAME_KEYS     = ("name","Name","displayName","label","Label")

def _as_dict(o):
    if isinstance(o, dict):
        return o
    to_dict = getattr(o, "to_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    dct = getattr(o, "__dict__", None)
    if isinstance(dct, dict):
        return {k:v for k,v in dct.items() if not k.startswith("_")}
    return {}

def _true(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("true","yes","y","1")
    return False

def _has_tag(d: dict, needle: str) -> bool:
    for k in ATTR_TAGS:
        v = d.get(k)
        if isinstance(v, (list, tuple, set)):
            if any(isinstance(t, str) and needle in t.lower() for t in v):
                return True
        elif isinstance(v, str) and needle in v.lower():
            return True
    return False

def _is_door_node(d: dict) -> bool:
    # explicit booleans
    if any(_true(d.get(k)) for k in ATTR_IS_DOOR):
        return True
    # kind/category contains 'door'
    for k in ATTR_KIND:
        v = d.get(k)
        if isinstance(v, str) and "door" in v.lower():
            return True
    # tags mention door
    return _has_tag(d, "door")

def _is_stair_node(d: dict) -> bool:
    if any(_true(d.get(k)) for k in ATTR_IS_STAIR):
        return True
    for k in ATTR_KIND:
        v = d.get(k)
        if isinstance(v, str) and "stair" in v.lower():
            return True
    return _has_tag(d, "stair")

def _pick_id(d: dict, fallback_prefix: str, idx: int) -> str:
    for k in ID_KEYS:
        v = d.get(k)
        if isinstance(v, (str,int)) and f"{v}".strip():
            return f"{v}".strip()
    return f"{fallback_prefix}:{idx}"

def _pick_name(d: dict, id_str: str) -> str:
    for k in NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # families/types if present
    fam = d.get("revitFamily") or d.get("family")
    typ = d.get("revitType")  or d.get("type")
    if isinstance(fam, str) or isinstance(typ, str):
        label = " ".join([x for x in [fam, typ] if isinstance(x, str) and x.strip()]).strip()
        if label:
            return label
    return id_str

from specklepy.api.client import SpeckleClient

@app.get("/graph/exits")
def list_exits_for_floor(
    floor: str = Query(..., description="Level name exactly as shown in /graph/floors"),
    x_project_id: str | None = Header(None),
    x_model_id:   str | None = Header(None),
    debug: int = Query(0),
):
    proj  = (x_project_id or "").strip()
    model = (x_model_id   or "").strip().split("@",1)[0]
    if not proj or not model:
        return JSONResponse(status_code=400, content={"error":"Missing headers (X-Project-Id, X-Model-Id)"})

    graphs_dir = _graphs_dir_for(proj, model)
    if not graphs_dir.exists():
        raise HTTPException(status_code=404, detail=f"Graphs folder not found: {graphs_dir}")

    pkl_path = _graph_pkl_for_floor(graphs_dir, floor)
    if not pkl_path.exists():
        pkl_alt = _graph_pkl_for_floor(graphs_dir, f"Level { _norm_level(floor).split()[-1] }")
        if pkl_alt.exists():
            pkl_path = pkl_alt
        else:
            raise HTTPException(status_code=404, detail=f"Graph PKL for floor not found: {pkl_path}")

    try:
        with open(pkl_path, "rb") as f:
            blob = pickle.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load graph PKL: {e}")

    # ---- normalize shapes
    nodes_iter = []
    walls_meta = []
    try:
        import networkx as nx
        if hasattr(blob, "nodes") and hasattr(blob, "edges") and isinstance(blob, nx.Graph):
            nodes_iter = (data for _, data in blob.nodes(data=True))
            # walls stored in graph attrs (set in create_graph)
            walls_meta = list((blob.graph or {}).get("walls", []))
        else:
            raise ImportError
    except Exception:
        if isinstance(blob, dict):
            nodes_iter = (blob.get("nodes") or blob.get("graph", {}).get("nodes") or [])
            # support walls either at top-level or under "graph"
            walls_meta = list(blob.get("walls") or blob.get("graph", {}).get("walls") or [])
        elif isinstance(blob, list):
            nodes_iter = blob
        else:
            d = _as_dict(blob)
            nodes_iter = d.get("nodes") or d.get("graph", {}).get("nodes", [])
            walls_meta = list(d.get("walls") or d.get("graph", {}).get("walls") or [])

    def _label_from_meta(meta: dict, fallback_id: str) -> str:
        t = (meta.get("type") or "").strip() if isinstance(meta, dict) else ""
        f = (meta.get("family") or "").strip() if isinstance(meta, dict) else ""
        if f and t: return f"{f} — {t} ({fallback_id})"
        if t:       return f"{t} ({fallback_id})"
        if f:       return f"{f} ({fallback_id})"
        return fallback_id

    doors, stairs = [], []
    seen_doors, seen_stairs = set(), set()
    dcount_gen, scount_gen = 0, 0

    for n in nodes_iter:
        nd = _as_dict(n)
        if not nd: 
            continue

        # ------- DOORS (unchanged, your existing cleaned logic)
        if _is_door_node(nd):
            emitted = 0
            metas = nd.get("doors")
            if isinstance(metas, list) and metas:
                for meta in metas:
                    did = str((meta.get("id") if isinstance(meta, dict) else "") or nd.get("source_id") or "").strip()
                    if (not did or "-" in did) and isinstance(meta, dict):
                        for k in ("object_id", "speckle_id", "id", "source_id"):
                            v = meta.get(k) or nd.get(k)
                            if v and "-" not in str(v):
                                did = str(v).strip(); break
                    if not did or "-" in did or did in seen_doors:
                        continue
                    doors.append({"id": did, "name": _label_from_meta(meta if isinstance(meta, dict) else {}, did)})
                    seen_doors.add(did); emitted += 1

            if emitted == 0:
                ids = nd.get("door_ids") or [nd.get("source_id")]
                if ids:
                    for raw in ids:
                        did = str(raw or "").strip()
                        if not did or "-" in did or did in seen_doors: 
                            continue
                        name = _pick_name(nd, did) or f"Door ({did})"
                        doors.append({"id": did, "name": name})
                        seen_doors.add(did); emitted += 1

            if emitted == 0:
                dcount_gen += 1
                did = _pick_id(nd, "door", dcount_gen)
                if did and did not in seen_doors:
                    doors.append({"id": did, "name": _pick_name(nd, did)})
                    seen_doors.add(did)
            continue

        # ------- STAIRS (unchanged, cleaned)
        if _is_stair_node(nd):
            emitted = 0
            metas = nd.get("stairs")
            if isinstance(metas, list) and metas:
                for meta in metas:
                    sid = str((meta.get("id") if isinstance(meta, dict) else "") or nd.get("source_id") or "").strip()
                    if (not sid or "-" in sid) and isinstance(meta, dict):
                        for k in ("object_id", "speckle_id", "id", "source_id"):
                            v = meta.get(k) or nd.get(k)
                            if v and "-" not in str(v):
                                sid = str(v).strip(); break
                    if not sid or "-" in sid or sid in seen_stairs:
                        continue
                    stairs.append({"id": sid, "name": _label_from_meta(meta if isinstance(meta, dict) else {}, sid)})
                    seen_stairs.add(sid); emitted += 1

            if emitted == 0:
                ids = nd.get("stair_ids") or [nd.get("source_id")]
                if ids:
                    for raw in ids:
                        sid = str(raw or "").strip()
                        if not sid or "-" in sid or sid in seen_stairs:
                            continue
                        name = _pick_name(nd, sid) or f"Stair ({sid})"
                        stairs.append({"id": sid, "name": name})
                        seen_stairs.add(sid); emitted += 1

            if emitted == 0:
                scount_gen += 1
                sid = _pick_id(nd, "stair", scount_gen)
                if sid and sid not in seen_stairs:
                    stairs.append({"id": sid, "name": _pick_name(nd, sid)})
                    seen_stairs.add(sid)
            continue

    # ------- NEW: walls (from graph.global attrs)
    walls = []
    seen_walls = set()
    for meta in walls_meta or []:
        wid = str((meta or {}).get("id") or "").strip()
        if not wid or wid in seen_walls:
            continue
        walls.append({"id": wid, "name": _label_from_meta(meta, wid)})
        seen_walls.add(wid)

    payload = {"doors": doors, "stairs": stairs, "walls": walls}
    if debug:
        payload["debug_counts"] = {
            "doors": len(doors),
            "stairs": len(stairs),
            "walls": len(walls),
            "generated_door_ids": dcount_gen,
            "generated_stair_ids": scount_gen,
            "file": str(pkl_path),
        }
    return payload
