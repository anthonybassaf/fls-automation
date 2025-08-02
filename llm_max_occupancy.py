# llm_max_occupancy.py
import sys
import json
from dotenv import load_dotenv
from extract_max_occupancy import get_max_occupancy_with_cache

load_dotenv()

if len(sys.argv) < 2:
    print("❌ Error: classification groups not provided.", file=sys.stderr)
    sys.exit(1)

try:
    groups = json.loads(sys.argv[1])
except Exception as e:
    print(f"❌ Failed to parse input: {e}", file=sys.stderr)
    sys.exit(1)

results = {}

for group in groups:
    val = get_max_occupancy_with_cache(group)
    if val:
        with open("cached_data/classification_index.json", "r") as f:
            index = json.load(f)

        for room_name, data in index.items():
            if data.get("classification", "").strip().lower() == group.strip().lower():
                results[room_name] = {
                    "classification": data.get("classification"),
                    "max_occupancy": val
                }

print(json.dumps(results))
