# llm_classify.py
import os
import sys
import json
from dotenv import load_dotenv
from extract_classification import get_classification_with_cache

load_dotenv()
print("[DEBUG] SELECTED_CODE_PDF =", os.environ.get("SELECTED_CODE_PDF"), flush=True)

if len(sys.argv) < 2:
    print("[ERROR] Error: room names not provided.")
    sys.exit(1)

try:
    print("[INFO] Parsing input room names...", flush=True)
    room_names = json.loads(sys.argv[1])
except Exception as e:
    print(f"[ERROR] Failed to parse input: {e}")
    sys.exit(1)

results = {}

for name in room_names:
    classification = get_classification_with_cache(name)
    if isinstance(classification, str):
        results[name] = classification.strip()
    else:
        print(f"[WARNING] No classification for '{name}' — got: {classification}")
        results[name] = "UNKNOWN"

print(json.dumps(results))
