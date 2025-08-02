# llm_olf.py
import sys
import json
from dotenv import load_dotenv
from extract_olf import get_olf_with_cache

load_dotenv()

if __name__ == "__main__":
    try:
        room_names = sys.argv[1:]
        result = {}

        for name in room_names:
            classification = "UNKNOWN"  # fallback if not passed, could also read from cache
            olf, unit = get_olf_with_cache(name, classification)
            if olf:
                result[name.upper()] = {"olf": olf, "unit": unit}

        json.dump(result, sys.stdout)
        print("", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] Exception in llm_olf.py: {e}", file=sys.stderr)
        sys.exit(1)
