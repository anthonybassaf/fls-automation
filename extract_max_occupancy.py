import os
import re
import json
import sys
import pandas as pd
from openai import AzureOpenAI
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings

# 🔐 Initialize OpenAI + LangChain
# client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
client = AzureOpenAI(api_key=os.getenv("AZURE_API_KEY"),
                     api_version=os.getenv("API_VERSION"),
                     azure_endpoint=os.getenv("AZURE_ENDPOINT"))

# embedding = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

selected_pdf = os.environ.get("SELECTED_CODE_PDF")
if not selected_pdf:
    raise ValueError("Environment variable SELECTED_CODE_PDF is not set.")

# New structured path for vectorstore
vectorstore = FAISS.load_local(
    folder_path=f"vector_db/{selected_pdf}",
    embeddings=embedding,
    index_name=selected_pdf,
    allow_dangerous_deserialization=True
)


def extract_max_occupancy_table_from_text(md_path: str) -> pd.DataFrame:
    with open(md_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    capture = False
    block = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Trigger capture
        if not capture and "MAXIMUM" in line.upper() and "OCCUPANT LOAD" in line.upper():
            capture = True
            i += 1
            continue

        if capture:
            # Stop if we encounter a new heading or irrelevant line
            if "EXIT" in line.upper() and "ACCESS" in line.upper():
                i += 1
                continue
            if re.match(r"^[A-Z][a-z]{2,}", line):  # paragraph or heading
                break

            block.append(line)

        i += 1

    # 🧠 Now parse the extracted block
    rows = []
    current_groups = []

    for line in block:
        if re.match(r"^[A-Z][A-Z0-9,\-\s]+$", line):  # Likely group line
            current_groups = re.split(r",\s*", line.strip())
        elif re.match(r"^\d+(\.\d+)?$", line) and current_groups:
            value = float(line)
            for group in current_groups:
                group = group.strip()
                if group:
                    rows.append({"Group": group, "MaxOccupancy": value})
            current_groups = []  # Reset after use

    return pd.DataFrame(rows)

def get_gpt_max_occupancy_for_classification(group_name: str) -> int | None:
    try:
        retriever = vectorstore.as_retriever()
        docs = retriever.get_relevant_documents(f"maximum occupant load for {group_name}")

        if not docs:
            print(f"❌ No relevant documents found for: {group_name}", file=sys.stderr)
            return None

        context = "\n".join(doc.page_content for doc in docs)

        prompt = f"""
You are a building code expert. Given the following context and the classification group "{group_name}", return only the maximum occupant load allowed for this group. Only return a number. Example: 49

Context:
{context}
"""

        response = client.chat.completions.create(
            model=os.getenv("DEPLOYMENT"),
            messages=[
                {"role": "system", "content": "You return code values extracted from context."},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0
        )

        content = response.choices[0].message.content.strip()
        match = re.search(r"\d+", content)
        if match:
            return int(match.group(0))
        else:
            print(f"❌ No number found in GPT response: {content}", file=sys.stderr)
            return None

    except Exception as e:
        print(f"❌ GPT query failed: {e}", file=sys.stderr)
        return None



def run_max_occupancy_batch(group_names: list[str]) -> dict:
    results = {}
    for group in group_names:
        val = get_gpt_max_occupancy_for_classification(group)
        results[group] = val
    return results

# def get_max_occupancy_with_cache(classification: str, cache_path="cached_data/classification_index.json") -> int:
#     classification = classification.upper().strip()

#     if os.path.exists(cache_path):
#         with open(cache_path, "r") as f:
#             try:
#                 index = json.load(f)
#             except json.JSONDecodeError:
#                 print("[WARNING] Max occupancy cache corrupted. Starting fresh.")
#                 index = {}
#     else:
#         index = {}

#     # ✅ Direct lookup by classification key
#     if classification in index and "max_occupancy" in index[classification]:
#         print(f"[CACHE HIT] Max occupancy for {classification} (direct key): {index[classification]['max_occupancy']}")
#         return index[classification]["max_occupancy"]

#     # ✅ Check all rooms for cached occupancy tied to this classification
#     for values in index.values():
#         if values.get("classification") == classification and "max_occupancy" in values:
#             print(f"[CACHE HIT] Max occupancy for {classification}: {values['max_occupancy']}")
#             return values["max_occupancy"]

#     # ❗Fallback to GPT
#     print(f"[GPT QUERY] Max occupancy not cached for: {classification}")
#     max_occ = get_gpt_max_occupancy_for_classification(classification)

#     if max_occ:
#         # Store under classification key directly
#         index.setdefault(classification, {})["max_occupancy"] = max_occ
#         os.makedirs(os.path.dirname(cache_path), exist_ok=True)
#         with open(cache_path, "w") as f:
#             json.dump(index, f, indent=2)
#     else:
#         print(f"[WARNING] GPT returned no occupancy for: {classification}")

#     return max_occ

def get_max_occupancy_with_cache(classification: str, cache_path="cached_data/classification_index.json") -> int | None:
    classification = classification.upper().strip()

    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            try:
                index = json.load(f)
            except json.JSONDecodeError:
                print("[WARNING] Cache file corrupted. Starting fresh.", file=sys.stderr)
                index = {}
    else:
        index = {}

    # Look inside room-based entries for a match
    for room_name, data in index.items():
        if data.get("classification", "").strip().lower() == classification.strip().lower():
            if "max_occupancy" in data:
                print(f"[CACHE HIT] Max occupancy for {classification} via {room_name}: {data['max_occupancy']}", file=sys.stderr)
                return data["max_occupancy"]

    # Else fallback to GPT
    print(f"[GPT QUERY] Max occupancy not cached for: {classification}", file=sys.stderr)
    max_occ = get_gpt_max_occupancy_for_classification(classification)

    if max_occ:
        # Inject max occupancy into all rooms that match this classification
        for room_name, data in index.items():
            if data.get("classification", "").upper().strip() == classification:
                data["max_occupancy"] = max_occ

        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(index, f, indent=2)

    return max_occ

