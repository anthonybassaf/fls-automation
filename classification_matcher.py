# classification_matcher.py
import json
import os
from pathlib import Path
from rapidfuzz import fuzz, process

# Load structured classification index
def load_classification_index(path: str = "data/classification_index.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# Flatten classification index into a {label → classification} map
def flatten_index(index: dict) -> dict:
    flat_map = {}
    for classification, entries in index.items():
        for label in entries:
            flat_map[label.lower()] = classification
    return flat_map

# Load once at module level
CLASSIFICATION_INDEX_PATH = os.path.join("data", "classification_index.json")
with open(CLASSIFICATION_INDEX_PATH, "r", encoding="utf-8") as f:
    classification_index = json.load(f)

index_keys = list(classification_index.keys())

def match_classification(room_name: str, threshold: float = 80.0) -> str | None:
    room_name = room_name.lower().strip()

    match = process.extractOne(
        room_name,
        index_keys,
        scorer=fuzz.token_set_ratio,
        score_cutoff=threshold
    )

    if match:
        matched_key, score, _ = match
        classification = classification_index[matched_key]["classification"]
        print(f"✅ Matched '{room_name}' → '{classification}' (score: {score})")
        return classification

    print(f"❌ No match found for: '{room_name}'")
    return None

# Load classification index (flat format: room_type → classification data)
index_path = os.path.join("data", "classification_index.json")
with open(index_path, "r", encoding="utf-8") as f:
    classification_index = json.load(f)

def match_room_to_classification(room_name: str, threshold: int = 80) -> str | None:
    room_name_clean = room_name.strip().lower()
    best_match = None
    highest_score = 0

    for keyword, entry in classification_index.items():
        score = fuzz.partial_ratio(room_name_clean, keyword.lower())
        if score > highest_score and score >= threshold:
            highest_score = score
            best_match = entry.get("classification")

    # 🔁 Fallback with lower threshold
    if not best_match:
        for keyword, entry in classification_index.items():
            score = fuzz.partial_ratio(room_name_clean, keyword.lower())
            if score > highest_score and score >= 60:  # fallback threshold
                highest_score = score
                best_match = entry.get("classification")

    if best_match:
        print(f"✅ Matched '{room_name}' → '{best_match}' (score: {highest_score})")
    else:
        print(f"❌ No match found for: '{room_name}'")

    return best_match
