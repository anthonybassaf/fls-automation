import os, sys, json
from dotenv import load_dotenv
from extract_classification import get_classification_with_cache

def _as_list(argv):
    if not argv: return []
    if len(argv) == 1 and argv[0].strip().startswith("["):
        try: return json.loads(argv[0])
        except Exception: return []
    return argv

if __name__ == "__main__":
    load_dotenv()
    rooms = _as_list(sys.argv[1:])
    if not rooms:
        print("{}", end=""); sys.exit(0)

    # Ask cache/GPT once for all rooms
    res = get_classification_with_cache(rooms)  # dict[str,str]
    # Guarantee flat mapping and strings
    out = {str(k): (str(v) if v else "Unknown") for k, v in (res or {}).items()}
    print(json.dumps(out, ensure_ascii=False))