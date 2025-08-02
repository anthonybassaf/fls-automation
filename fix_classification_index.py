import json

with open("cached_data/classification_index.json", "r") as f:
    data = json.load(f)

# Extract group-level max occupancy
group_occupancies = {
    k.upper().strip(): v["max_occupancy"]
    for k, v in data.items()
    if k.upper().startswith("GROUP ") and "max_occupancy" in v
}

# Update room entries
for room, info in data.items():
    classification = info.get("classification", "").upper().strip()
    if classification in group_occupancies and "max_occupancy" not in info:
        info["max_occupancy"] = group_occupancies[classification]

# Remove standalone group entries
for k in list(data.keys()):
    if k.upper().startswith("GROUP ") and "max_occupancy" in data[k]:
        del data[k]

# Save back to same file
with open("cached_data/classification_index.json", "w") as f:
    json.dump(data, f, indent=2)

print("✅ classification_index.json updated and cleaned.")
