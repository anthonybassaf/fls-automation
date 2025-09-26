# # llm_max_occupancy.py
# import sys
# import json
# from dotenv import load_dotenv
# from extract_max_occupancy import get_max_occupancy_with_cache

# load_dotenv()

# if len(sys.argv) < 2:
#     print("❌ Error: classification groups not provided.", file=sys.stderr)
#     sys.exit(1)

# try:
#     groups = json.loads(sys.argv[1])
# except Exception as e:
#     print(f"❌ Failed to parse input: {e}", file=sys.stderr)
#     sys.exit(1)

# results = {}

# for group in groups:
#     val = get_max_occupancy_with_cache(group)
#     if val:
#         with open("cached_data/classification_index.json", "r") as f:
#             index = json.load(f)

#         for room_name, data in index.items():
#             if data.get("classification", "").strip().lower() == group.strip().lower():
#                 results[room_name] = {
#                     "classification": data.get("classification"),
#                     "max_occupancy": val
#                 }

# print(json.dumps(results))

import sys, json
from dotenv import load_dotenv
from extract_max_occupancy import get_max_occupancy_with_cache

def _parse_argv_to_list(argv):
    if not argv:
        return []
    if len(argv) == 1 and argv[0].strip().startswith("["):
        try:
            return json.loads(argv[0])
        except Exception:
            return [argv[0]]
    return argv

if __name__ == "__main__":
    load_dotenv()
    args = sys.argv[1:]
    groups = _parse_argv_to_list(args)
    if not groups:
        print("{}", end="")
        sys.exit(0)

    # Parent expects: { "Group E": <number or null>, "Group B": <number or null>, ... }
    out = {}
    for g in groups:
        try:
            val = get_max_occupancy_with_cache(g)
            # val may be int/float/None; keep it as-is (None becomes null)
            out[g] = val if val is not None else None
        except Exception as e:
            print(f"[llm_max_occupancy][ERR] {g}: {e}", file=sys.stderr)
            out[g] = None

    print(json.dumps(out, ensure_ascii=False))
