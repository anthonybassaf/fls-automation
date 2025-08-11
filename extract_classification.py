# import os
# import re
# import json
# from openai import AzureOpenAI
# from langchain_community.vectorstores import FAISS
# from langchain_community.embeddings import HuggingFaceEmbeddings
# from dotenv import load_dotenv
# load_dotenv()


# # client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# client = AzureOpenAI(api_key=os.getenv("AZURE_API_KEY"),
#                      api_version=os.getenv("API_VERSION"),
#                      azure_endpoint=os.getenv("AZURE_ENDPOINT"))

# embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# # selected_pdf = os.environ.get("SELECTED_CODE_PDF")
# # vectorstore = None
# # if selected_pdf:
# #     vectorstore = FAISS.load_local(
# #         folder_path=f"vector_db/{selected_pdf}",
# #         embeddings=embedding,
# #         index_name=f"{selected_pdf}",
# #         allow_dangerous_deserialization=True
# #     )

# selected_pdf = os.environ.get("SELECTED_CODE_PDF")
# vectorstore = None
# if selected_pdf:
#     # Strip extension from SELECTED_CODE_PDF
#     index_name = os.path.splitext(os.path.basename(selected_pdf))[0]
#     index_path = os.path.join("vector_db", index_name)

#     print(f"[DEBUG] Loading FAISS index for classification: {index_path} (index_name={index_name})")

#     vectorstore = FAISS.load_local(
#         folder_path=index_path,
#         embeddings=embedding,
#         index_name=index_name,
#         allow_dangerous_deserialization=True
#     )


# def extract_classification_sections_with_gpt(room_name: str) -> str:
#     """
#     Query the FAISS index for context and ask GPT to identify the correct classification group.
#     Returns: e.g., "Group R-2"
#     """
#     print(f"[INFO] Searching for classification context for room: {room_name}", flush=True)
#     try:
#         print(f"[INFO] Using FAISS index for {selected_pdf}", flush=True)
#         docs = vectorstore.similarity_search(room_name, k=3)
#         context = "\n\n".join(doc.page_content for doc in docs)
#         print(f"[INFO] Context for '{room_name}': {context[:200]}...", flush=True)  # Print first 200 chars

#         prompt = f"""
#             You are an expert in fire safety building codes. Given the room name "{room_name}" and the building classification context below, return the most appropriate building classification group. 
#             Include subgroup if applicable (e.g., Group R-2, Group A-3, Group I-4, etc.). Only output the classification line.

#             Context:
#             {context}

#             Respond in the format:
#             Classification: Group X[-Y]
#             """

#         response = client.chat.completions.create(
#             model=os.getenv("DEPLOYMENT"),
#             messages=[
#                 {"role": "system", "content": "Only output one line in this format: Classification: Group X. Do not explain. Do not add extra lines."},
#                 {"role": "user", "content": prompt}
#             ],
#             temperature=0
#         )

#         content = response.choices[0].message.content
#         #print(f"[GPT response] {content}")
#         match = re.search(r"Classification:\s*(Group\s+[A-Z]+(?:-\d)?)", content)
#         if match:
#             return match.group(1)

#     except Exception as e:
#         print(f"[Error] GPT classification failed for '{room_name}': {e}")
#     print("Nothing found for:", room_name)
#     return None

# def get_classification_with_cache(room_name: str, cache_path="cached_data/classification_index.json") -> str:
#     room_name = room_name.upper().strip()
#     index = {}

#     # 🔁 Load cache if it exists
#     if os.path.exists(cache_path):
#         with open(cache_path, "r") as f:
#             try:
#                 index = json.load(f)
#             except json.JSONDecodeError:
#                 print("[WARNING] Cache file corrupted. Starting fresh.")
#                 index = {}

#     # ✅ Return cached value if available
#     if room_name in index and "classification" in index[room_name]:
#         print(f"[CACHE HIT] {room_name}: {index[room_name]['classification']}")
#         return index[room_name]["classification"]

#     # ❗Fallback to GPT if not cached
#     print(f"[GPT QUERY] Classification not cached for: {room_name}")
#     classification = extract_classification_sections_with_gpt(room_name)

#     if classification:
#         index.setdefault(room_name, {})["classification"] = classification
#         os.makedirs(os.path.dirname(cache_path), exist_ok=True)
#         with open(cache_path, "w") as f:
#             json.dump(index, f, indent=2)
#     else:
#         print(f"[WARNING] GPT returned no classification for: {room_name}")

#     return classification


import os
import re
import json
import pickle
from collections import defaultdict, Counter
from openai import AzureOpenAI
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from dotenv import load_dotenv
load_dotenv()

# === Speckle Graph Directory ===
GRAPH_DIR = "./graphs"

# === Initialize GPT client ===
client = AzureOpenAI(
    api_key=os.getenv("AZURE_API_KEY"),
    api_version=os.getenv("API_VERSION"),
    azure_endpoint=os.getenv("AZURE_ENDPOINT")
)

embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

selected_pdf = os.environ.get("SELECTED_CODE_PDF")
vectorstore = None
if selected_pdf:
    index_name = os.path.splitext(os.path.basename(selected_pdf))[0]
    index_path = os.path.join("vector_db", index_name)

    print(f"[DEBUG] Loading FAISS index for classification: {index_path} (index_name={index_name})")

    vectorstore = FAISS.load_local(
        folder_path=index_path,
        embeddings=embedding,
        index_name=index_name,
        allow_dangerous_deserialization=True
    )

# === BBox Utility ===
def bbox_touch_or_overlap(b1, b2, margin=1.0):
    return not (
        b1[1] + margin < b2[0] or
        b2[1] + margin < b1[0] or
        b1[3] + margin < b2[2] or
        b2[3] + margin < b1[2]
    )

# === Build Room Adjacency from Graphs ===
def build_room_adjacency_from_graphs():
    adjacency_map = defaultdict(set)

    for fname in os.listdir(GRAPH_DIR):
        if not fname.endswith(".pkl") or not fname.startswith("G_"):
            continue

        with open(os.path.join(GRAPH_DIR, fname), "rb") as f:
            G = pickle.load(f)

        room_nodes = defaultdict(list)
        for node, data in G.nodes(data=True):
            rid = data.get("room_id")
            if rid:
                room_nodes[rid].append((node, data))

        room_bboxes = {}
        room_names = {}
        unnamed_counter = 1

        for rid, node_data_list in room_nodes.items():
            xs = [pt[0][0] for pt in node_data_list]
            ys = [pt[0][1] for pt in node_data_list]
            if not xs or not ys:
                continue
            room_bboxes[rid] = (min(xs), max(xs), min(ys), max(ys))

            name_counts = Counter()
            for _, data in node_data_list:
                name = data.get("room_name")
                if name and name.strip() and name != "?":
                    name_counts[name.strip().upper()] += 1

            best_name = name_counts.most_common(1)[0][0] if name_counts else f"UNNAMED_ROOM_{unnamed_counter}"
            if not name_counts:
                unnamed_counter += 1

            room_names[rid] = best_name

        room_ids = list(room_bboxes.keys())
        for i, r1 in enumerate(room_ids):
            for j in range(i + 1, len(room_ids)):
                r2 = room_ids[j]
                if bbox_touch_or_overlap(room_bboxes[r1], room_bboxes[r2]):
                    adjacency_map[room_names[r1]].add(room_names[r2])
                    adjacency_map[room_names[r2]].add(room_names[r1])

    return {k: sorted(v) for k, v in adjacency_map.items()}

# === Load room adjacency ===
print("[INFO] Computing room adjacency from graph PKLs...")
room_adjacency_map = build_room_adjacency_from_graphs()

# === GPT Classification ===
def extract_classification_sections_with_gpt(room_name: str) -> str:
    print(f"[INFO] Searching for classification context for room: {room_name}", flush=True)
    try:
        print(f"[INFO] Using FAISS index for {selected_pdf}", flush=True)
        docs = vectorstore.similarity_search(room_name, k=3)
        context = "\n\n".join(doc.page_content for doc in docs)
        print(f"[INFO] Context for '{room_name}': {context[:200]}...", flush=True)

        # 🔍 Inject adjacency for context-sensitive room types
        base_name = room_name.upper().strip()
        adjacency_list = room_adjacency_map.get(base_name, [])
        is_contextual_room = base_name in {"WC", "TOILET", "KITCHEN", "PANTRY", "BATHROOM", "WASHROOM"}

        if is_contextual_room and adjacency_list:
            room_context = f"Room: {room_name}\nAdjacent rooms: {', '.join(adjacency_list)}"
        else:
            room_context = f"Room: {room_name}"

        prompt = f"""
            You are an expert in fire safety building codes.
            Given the room context below, return the most appropriate building classification group.
            Include subgroup if applicable (e.g., Group R-2, Group A-3, Group I-4, etc.).
            Only output the classification line.

            {room_context}

            Code Context:
            {context}

            Respond in the format:
            Classification: Group X[-Y]
        """

        response = client.chat.completions.create(
            model=os.getenv("DEPLOYMENT"),
            messages=[
                {"role": "system", "content": "Only output one line in this format: Classification: Group X. Do not explain. Do not add extra lines."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )

        content = response.choices[0].message.content
        match = re.search(r"Classification:\s*(Group\s+[A-Z]+(?:-\d)?)", content)
        if match:
            return match.group(1)

    except Exception as e:
        print(f"[Error] GPT classification failed for '{room_name}': {e}")
    print("Nothing found for:", room_name)
    return None

# === Caching Layer ===
def get_classification_with_cache(room_name: str, cache_path="cached_data/classification_index.json") -> str:
    room_name = room_name.upper().strip()
    index = {}

    # 🔁 Load cache if it exists
    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            try:
                index = json.load(f)
            except json.JSONDecodeError:
                print("[WARNING] Cache file corrupted. Starting fresh.")
                index = {}

    # ✅ Return cached value if available
    if room_name in index and "classification" in index[room_name]:
        print(f"[CACHE HIT] {room_name}: {index[room_name]['classification']}")
        return index[room_name]["classification"]

    # ❗Fallback to GPT if not cached
    print(f"[GPT QUERY] Classification not cached for: {room_name}")
    classification = extract_classification_sections_with_gpt(room_name)

    if classification:
        index.setdefault(room_name, {})["classification"] = classification
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(index, f, indent=2)
    else:
        print(f"[WARNING] GPT returned no classification for: {room_name}")

    return classification
