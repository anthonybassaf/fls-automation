from extract_olf import get_gpt_olf_for_room

# Example test cases
test_cases = [
    ("Lobby", "Group A-3"),
    ("Electrical Room", "Group U"),
    ("Workshop", "Group F-1"),
    ("Shower", "Group R-1"),
    ("Corridor", "Group H-5"),
]

for room_name, classification in test_cases:
    print(f"\n🔍 Testing: {room_name} ({classification})")
    olf, unit = get_gpt_olf_for_room(room_name, classification)
    if olf:
        print(f"✅ OLF: {olf} {unit}")
    else:
        print("❌ Failed to extract OLF")
