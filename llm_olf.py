# # llm_olf.py
# import sys
# import json
# from dotenv import load_dotenv
# from extract_olf import get_olf_with_cache

# load_dotenv()

# if __name__ == "__main__":
#     try:
#         room_names = sys.argv[1:]
#         result = {}

#         for name in room_names:
#             classification = "UNKNOWN"  # fallback if not passed, could also read from cache
#             olf, unit = get_olf_with_cache(name, classification)
#             if olf:
#                 result[name.upper()] = {"olf": olf, "unit": unit}

#         json.dump(result, sys.stdout)
#         print("", file=sys.stderr)
#     except Exception as e:
#         print(f"[ERROR] Exception in llm_olf.py: {e}", file=sys.stderr)
#         sys.exit(1)

import sys, json
from dotenv import load_dotenv
from extract_olf import get_olf_with_cache

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
    room_names = _parse_argv_to_list(args)
    if not room_names:
        print("{}", end="")
        sys.exit(0)

    result = {}
    for name in room_names:
        try:
            # If your OLF needs a classification but you don't have it here,
            # pass "Unknown" (or adjust to read a cache if you prefer).
            olf, unit = get_olf_with_cache(name, "Unknown")
            # Do NOT uppercase keys; keep exact room names for upstream mapping
            result[name] = {"olf": olf if olf is not None else None,
                            "unit": unit if unit is not None else None}
        except Exception as e:
            print(f"[llm_olf][ERR] {name}: {e}", file=sys.stderr)
            result[name] = {"olf": None, "unit": None}

    print(json.dumps(result, ensure_ascii=False))
