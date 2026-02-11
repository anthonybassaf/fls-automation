# llm_olf.py
import sys
import json
from dotenv import load_dotenv

# New: use the batch entrypoint from the Qwen-based script
from extract_olf import run_batch as olf_run_batch


def _parse_argv_to_list(argv):
    """
    Accept either:
      - a single JSON list argument: '["Classroom","Office","WC"]'
      - or multiple args: Classroom Office WC
    Return a Python list of room names.
    """
    if not argv:
        return []
    if len(argv) == 1 and argv[0].strip().startswith("["):
        try:
            return json.loads(argv[0])
        except Exception:
            # Fallback: treat as single room string
            return [argv[0]]
    return argv


if __name__ == "__main__":
    load_dotenv()

    args = sys.argv[1:]
    room_names = _parse_argv_to_list(args)
    if not room_names:
        print("{}", end="")
        sys.exit(0)

    # Parent expects:
    # {
    #   "Classroom": {"olf": <float or null>, "unit": <str or null>},
    #   "Office":    {"olf": <float or null>, "unit": <str or null>},
    #   ...
    # }
    try:
        # run_batch returns: {room_name: [value, unit]}
        raw = olf_run_batch(room_names)
    except Exception as e:
        print(f"[llm_olf][ERR] batch call failed: {e}", file=sys.stderr)
        # On fatal error: return all nulls
        result = {name: {"olf": None, "unit": None} for name in room_names}
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)

    result = {}
    for name in room_names:
        pair = raw.get(name)
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            val, unit = pair[0], pair[1]
        else:
            val, unit = None, None

        # Do NOT uppercase keys; keep exact room names for upstream mapping
        result[name] = {
            "olf": float(val) if isinstance(val, (int, float)) else val if val is not None else None,
            "unit": str(unit) if unit is not None else None,
        }

    print(json.dumps(result, ensure_ascii=False))