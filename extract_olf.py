# import os
# import re
# import sys
# import json
# from openai import AzureOpenAI
# import pandas as pd
# from pathlib import Path

# # client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# client = AzureOpenAI(api_key=os.getenv("AZURE_API_KEY"),
#                      api_version=os.getenv("API_VERSION"),
#                      azure_endpoint=os.getenv("AZURE_ENDPOINT"))

# def extract_olf_section_from_markdown(md_path: str) -> list[str]:
#     with open(md_path, "r", encoding="utf-8") as f:
#         lines = f.readlines()

#     olf_section = []
#     inside_olf = False

#     for idx, line in enumerate(lines):
#         if re.search(r"MAXIMUM\s+FLOOR\s+AREA\s+ALLOWANCES\s+PER\s+OCCUPANT", line, re.IGNORECASE):
#             inside_olf = True
#             continue

#         if inside_olf and (line.strip().startswith("#") or "---" in line):
#             break

#         if inside_olf:
#             olf_section.append(line.strip())

#     return olf_section

# def olf_lines_to_dataframe(olf_lines: list[str]) -> pd.DataFrame:
#     rows = []
#     current_label = []
#     unit_pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(gross|net)?", re.IGNORECASE)

#     for line in olf_lines:
#         line = line.strip()
#         if not line or "function of space" in line.lower() or "occupant load factor" in line.lower():
#             continue
#         if "see section" in line.lower():
#             continue

#         match = unit_pattern.match(line)
#         if match:
#             value = float(match.group(1))
#             unit = match.group(2) if match.group(2) else ""
#             label = " ".join(current_label).strip()
#             if label:
#                 rows.append({
#                     "FUNCTION OF SPACE": label,
#                     "OCCUPANT LOAD FACTOR": value,
#                     "UNIT": unit.lower()
#                 })
#                 current_label = []
#         else:
#             current_label.append(line)

#     return pd.DataFrame(rows)

# def get_gpt_olf_for_room(room_name: str, classification: str = "") -> tuple[float, str] | tuple[None, None]:
#     from pathlib import Path
#     import re

#     markdown_path = Path("data/sbc_code_markdown.md")
#     olf_lines = extract_olf_section_from_markdown(markdown_path)
#     olf_df = olf_lines_to_dataframe(olf_lines)

#     if olf_df.empty:
#         print("[WARNING] No OLF data available.")
#         return None, None

#     formatted_entries = "\n".join([
#         f"- {row['FUNCTION OF SPACE']}: {row['OCCUPANT LOAD FACTOR']} {row['UNIT']}"
#         for _, row in olf_df.iterrows()
#     ])

#     prompt = f"""
# You are an expert in building codes. Given the room name "{room_name}" and the list of Occupant Load Factors (OLF) below, return the most appropriate OLF for the room.

# {formatted_entries}

# Respond in the format:
# OLF: <value> <unit>
# """

#     try:
#         response = client.chat.completions.create(
#             model=os.getenv("DEPLOYMENT"),
#             messages=[
#                 {"role": "system", "content": "You match room names with Occupant Load Factors from building code entries."},
#                 {"role": "user", "content": prompt}
#             ],
#             temperature=0
#         )
#         content = response.choices[0].message.content
#         match = re.search(r"OLF:\s*([\d.]+)\s*(\w+)", content)
#         if match:
#             olf_value = float(match.group(1))
#             unit = match.group(2)
#             return olf_value, unit
#         else:
#             print(f"[Error] GPT didn't return a parsable OLF for '{room_name}': {content}", file=sys.stderr)
#             return None, None
#     except Exception as e:
#         print(f"[ERROR] GPT API call failed for '{room_name}': {e}")
#         return None, None
    
# def get_olf_with_cache(room_name: str, classification: str, cache_path="cached_data/classification_index.json") -> tuple:
#     room_name = room_name.upper().strip()

#     # Load cache if available
#     if os.path.exists(cache_path):
#         with open(cache_path, "r") as f:
#             try:
#                 index = json.load(f)
#             except json.JSONDecodeError:
#                 print("[WARNING] OLF cache corrupted. Starting fresh.")
#                 index = {}
#     else:
#         index = {}

#     # ✅ Return cached value
#     if room_name in index and "olf" in index[room_name]:
#         print(f"[CACHE HIT] OLF for {room_name}: {index[room_name]['olf']}")
#         return tuple(index[room_name]["olf"])

#     # ❗Fallback to GPT if not cached
#     print(f"[GPT QUERY] OLF not cached for: {room_name}")
#     olf_value, unit = get_gpt_olf_for_room(room_name, classification)

#     if olf_value:
#         index.setdefault(room_name, {})["olf"] = [olf_value, unit]
#         os.makedirs(os.path.dirname(cache_path), exist_ok=True)
#         with open(cache_path, "w") as f:
#             json.dump(index, f, indent=2)
#     else:
#         print(f"[WARNING] GPT returned no OLF for: {room_name}")

#     return olf_value, unit

import os
import sys
import json
from openai import AzureOpenAI
from langchain.vectorstores import FAISS
from langchain.embeddings import HuggingFaceEmbeddings

client = AzureOpenAI(
    api_key=os.getenv("AZURE_API_KEY"),
    api_version=os.getenv("API_VERSION"),
    azure_endpoint=os.getenv("AZURE_ENDPOINT")
)

def retrieve_olf_context_from_faiss(room_name: str) -> str:
    """
    Query FAISS vectorstore for OLF-relevant sections.

    Always loads from:
        vector_db/[SELECTED_CODE_PDF_WITHOUT_EXT]/
            [SELECTED_CODE_PDF_WITHOUT_EXT].faiss
            [SELECTED_CODE_PDF_WITHOUT_EXT].pkl

    The only required environment variable is SELECTED_CODE_PDF.
    """
    import os
    from langchain.vectorstores import FAISS
    from langchain.embeddings import HuggingFaceEmbeddings

    # Base folder is fixed
    base_dir = "vector_db"

    # Use SELECTED_CODE_PDF environment variable
    selected_code_pdf = os.environ.get("SELECTED_CODE_PDF")
    if not selected_code_pdf:
        raise RuntimeError(
            "Environment variable SELECTED_CODE_PDF must be set (e.g., SBC_Code_201.pdf)"
        )

    index_name = os.path.splitext(os.path.basename(selected_code_pdf))[0]
    index_path = os.path.join(base_dir, index_name)

    if not os.path.isdir(index_path):
        raise FileNotFoundError(
            f"Expected FAISS index folder not found: {index_path}"
        )

    print(f"[DEBUG] Loading FAISS index from {index_path} (index_name={index_name})")

    db = FAISS.load_local(
        index_path,
        embeddings=HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2"),
        index_name=index_name,
        allow_dangerous_deserialization=True,
    )

    query = (
        f"Occupant Load Factor table or maximum floor area per occupant "
        f"for a room type similar to {room_name}"
    )
    docs = db.similarity_search(query, k=5)
    return "\n\n".join([doc.page_content for doc in docs])



def get_gpt_olf_for_room(room_name: str, classification: str = "") -> tuple[float, str] | tuple[None, None]:
    """
    Use FAISS-retrieved OLF context instead of reading markdown.
    """
    try:
        context = retrieve_olf_context_from_faiss(room_name)
    except Exception as e:
        print(f"[ERROR] Failed to retrieve context from FAISS: {e}")
        return None, None

    if not context:
        print(f"[WARNING] No OLF context retrieved from FAISS for: {room_name}")
        return None, None

    prompt = f"""
You are an expert in building codes.
Given the room name "{room_name}" and the following building code context:

{context}

Identify the most appropriate Occupant Load Factor (OLF) for the room.

Respond strictly in this format:
OLF: <value> <unit>
Examples: 
OLF: 9 gross
OLF: 11 net
"""

    try:
        response = client.chat.completions.create(
            model=os.getenv("DEPLOYMENT"),
            messages=[
                {"role": "system", "content": "You match room types with Occupant Load Factors using the provided building code context."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        content = response.choices[0].message.content
        import re
        match = re.search(r"OLF:\s*([\d.]+)\s*(\w+)", content)
        if match:
            olf_value = float(match.group(1))
            unit = match.group(2)
            return olf_value, unit
        else:
            print(f"[Error] GPT didn't return a parsable OLF for '{room_name}': {content}", file=sys.stderr)
            return None, None
    except Exception as e:
        print(f"[ERROR] GPT API call failed for '{room_name}': {e}")
        return None, None


def get_olf_with_cache(room_name: str, classification: str, cache_path="cached_data/classification_index.json") -> tuple:
    """
    Same as before, but now uses FAISS instead of markdown.
    """
    room_name = room_name.upper().strip()

    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            try:
                index = json.load(f)
            except json.JSONDecodeError:
                print("[WARNING] OLF cache corrupted. Starting fresh.")
                index = {}
    else:
        index = {}

    # Return cached value
    if room_name in index and "olf" in index[room_name]:
        print(f"[CACHE HIT] OLF for {room_name}: {index[room_name]['olf']}")
        return tuple(index[room_name]["olf"])

    # Otherwise query GPT
    print(f"[GPT QUERY] OLF not cached for: {room_name}")
    olf_value, unit = get_gpt_olf_for_room(room_name, classification)

    if olf_value:
        index.setdefault(room_name, {})["olf"] = [olf_value, unit]
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(index, f, indent=2)
    else:
        print(f"[WARNING] GPT returned no OLF for: {room_name}")

    return olf_value, unit
