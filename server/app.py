from fastapi import FastAPI, Request, File, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from specklepy.api.client import SpeckleClient
from dotenv import load_dotenv, set_key
import subprocess
import shutil
from pathlib import Path
import sys
import os
import json

app = FastAPI()

# CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

SPECKLE_SERVER_URL = os.getenv("SPECKLE_SERVER_URL")
PROJECT_ID = os.getenv("PROJECT_ID")
MODEL_ID = os.getenv("MODEL_ID")
BRANCH_NAME = os.getenv("BRANCH_NAME", "main")
SPECKLE_TOKEN_STG = os.getenv("SPECKLE_TOKEN_STG")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Utility to run Python scripts and capture output
def run_python_script(script_name: str, env: dict = None):
    script_path = os.path.join(BASE_DIR, script_name)
    env = env or os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # Ensure UTF-8 encoding for subprocess output
    print(f"📂 Running: {script_path} with Python: {sys.executable}")

    try:
        result = subprocess.run(
            [sys.executable, script_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            cwd=BASE_DIR,
            encoding="utf-8",
            errors="replace", 
            env=env or os.environ.copy()
        )

        return {
            "status": f"✅ {script_name} completed successfully.",
            "stdout": result.stdout
        }
    except subprocess.CalledProcessError as e:
        return {
            "status": f"❌ {script_name} failed with error code {e.returncode}.",
            "stdout": e.stdout,
            "stderr": e.stderr
        }
    except Exception as e:
        return {
            "status": f"❌ Unexpected error running {script_name}.",
            "stderr": str(e)
        }

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


# 🔁 Speckle Viewer Commit URL
@app.get("/config")
def get_config():
    client = SpeckleClient(host=SPECKLE_SERVER_URL)
    client.authenticate_with_token(SPECKLE_TOKEN_STG)
    branch = client.branch.get(PROJECT_ID, MODEL_ID)
    default_commit = branch.commits.items[-1] if branch.commits.items else None

    if not default_commit:
        return {"error": "No commits found"}

    commit_id = default_commit.id
    url = f"{SPECKLE_SERVER_URL}/projects/{PROJECT_ID}/models/{MODEL_ID}/commits/{commit_id}#embed=%7B%22isEnabled%22%3Atrue%7D"
    return {"url": url}

@app.get("/graph/floors")
def get_floors():
    graph_dir = os.path.join(BASE_DIR, "graphs")
    if not os.path.exists(graph_dir):
        return {"floors": []}
    files = sorted(f for f in os.listdir(graph_dir) if f.startswith("G_") and f.endswith(".pkl"))
    return {"floors": [f.replace(".pkl", "").split("_")[-1] for f in files]}


@app.post("/save-user-inputs")
async def save_user_inputs(request: Request):
    data = await request.json()
    with open(os.path.join(BASE_DIR, "user_inputs.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return {"status": "✅ Inputs saved"}

@app.get("/fls/pdfs")
def list_embedded_pdfs():
    vector_db_path = os.path.join(BASE_DIR, "vector_db")
    if not os.path.exists(vector_db_path):
        return {"pdfs": []}

    # Each subfolder is a named vectorstore (e.g. sbc_code, nfpa, etc.)
    pdfs = []
    for name in os.listdir(vector_db_path):
        subdir = os.path.join(vector_db_path, name)
        if os.path.isdir(subdir) and any(f.endswith(".faiss") for f in os.listdir(subdir)):
            pdfs.append(name)
    return {"pdfs": sorted(pdfs)}

@app.post("/fls/upload")
async def upload_and_embed_pdf(pdf: UploadFile = File(...)):
    if not pdf.filename.endswith(".pdf"):
        return {"status": "❌ Invalid file format. Please upload a PDF."}

    upload_dir = os.path.join(BASE_DIR, "data", "uploaded_pdfs")
    os.makedirs(upload_dir, exist_ok=True)

    pdf_path = os.path.join(upload_dir, pdf.filename)
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(pdf.file, f)

    # Launch embed subprocess from langchain_venv
    embed_script = os.path.join(BASE_DIR, "embedding", "embed_pdf_and_index.py")
    venv_python = os.path.join(BASE_DIR, "langchain_venv", "Scripts", "python.exe")

    result = subprocess.run(
        [venv_python, embed_script, pdf_path],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("❌ Subprocess failed")
        print("STDOUT:\n", result.stdout)
        print("STDERR:\n", result.stderr)
        return {"status": "❌ Failed to embed PDF", "stderr": result.stderr, "stdout": result.stdout}

    return {
        "status": "✅ PDF embedded successfully",
        "stdout": result.stdout
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




# 🔘 Script execution routes
@app.post("/run/grid")
def run_grid():
    return run_python_script("run_grid_main.py")

@app.post("/run/paths")
def run_paths():
    return run_python_script("run_paths_main.py")

@app.post("/run/fls")
def run_fls(pdf_id: str = Query(...)):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["SELECTED_CODE_PDF"] = pdf_id

    return run_python_script("run_fls_main.py", env=env)

@app.post("/set-project")
async def set_project(request: Request):
    data = await request.json()
    project_id = data.get("project_id")
    model_id = data.get("model_id")

    if not project_id or not model_id:
        return {"status": "❌ Missing project_id or model_id"}

    # Remove anything after '@' in model_id
    clean_model_id = model_id.split('@')[0]

    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')

    # Update .env file without quotes
    set_key(env_path, "PROJECT_ID", project_id, quote_mode="never")
    set_key(env_path, "MODEL_ID", clean_model_id, quote_mode="never")

    # Update current environment variables
    os.environ["PROJECT_ID"] = project_id
    os.environ["MODEL_ID"] = clean_model_id

    return {"status": f"✅ PROJECT_ID and MODEL_ID updated to {project_id} / {clean_model_id}"}


