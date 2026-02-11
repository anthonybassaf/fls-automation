# llm_max_occupancy.py
import sys
import json
from dotenv import load_dotenv

# New: use the batch entrypoint from the Qwen-based script
from extract_max_occupancy import run_batch as max_occ_run_batch


def _parse_argv_to_list(argv):
    """
    Accept either:
      - a single JSON list argument: '["Group B","Group E"]'
      - or multiple args: Group B "Group E"
    Return a Python list of group names.
    """
    if not argv:
        return []
    if len(argv) == 1 and argv[0].strip().startswith("["):
        try:
            return json.loads(argv[0])
        except Exception:
            # Fallback: treat as single group string
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
    try:
        # run_batch returns a dict[group_name -> int | None]
        result = max_occ_run_batch(groups)
    except Exception as e:
        # On fatal error, log and return all nulls
        print(f"[llm_max_occupancy][ERR] batch call failed: {e}", file=sys.stderr)
        for g in groups:
            out[g] = None
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(0)

    # Preserve input order and default to null if missing
    for g in groups:
        out[g] = result.get(g, None)

    print(json.dumps(out, ensure_ascii=False))