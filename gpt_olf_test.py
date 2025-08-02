import os
import pandas as pd
import re
from openai import OpenAI

# Set your API key
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# Step 1: Extract OLF table section from markdown
def extract_olf_section_from_markdown(filepath: str) -> list[str]:
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    start_index = None
    end_index = None
    for i, line in enumerate(lines):
        if "MAXIMUM FLOOR AREA ALLOWANCES PER OCCUPANT" in line:
            start_index = i
        elif start_index and "# Page" in line and i > start_index:
            end_index = i
            break

    if start_index is None or end_index is None:
        print("❌ OLF section not found.")
        return []

    return [line.strip().lstrip("➡️").strip() for line in lines[start_index:end_index] if line.strip()]


# Step 2: Parse OLF lines into DataFrame
def olf_lines_to_dataframe(olf_lines: list[str]) -> pd.DataFrame:
    rows = []
    buffer = []

    def flush_buffer_with_value(value_str, unit):
        full_label = " ".join(buffer).strip()
        buffer.clear()
        try:
            value = float(value_str)
            return {
                "FUNCTION OF SPACE": full_label,
                "OCCUPANT LOAD FACTOR": value,
                "UNIT": unit or ""
            }
        except ValueError:
            return None

    for line in olf_lines:
        line = line.strip()
        if not line or "function of space" in line.lower():
            continue

        match_value_only = re.match(r"^(\d+(?:\.\d+)?)(?:\s*(gross|net))?$", line, re.IGNORECASE)
        if match_value_only and buffer:
            value_str, unit = match_value_only.groups()
            row = flush_buffer_with_value(value_str, unit)
            if row:
                rows.append(row)
            continue

        match_label_value = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)(?:\s*(gross|net))?$", line, re.IGNORECASE)
        if match_label_value:
            label, value_str, unit = match_label_value.groups()
            buffer.append(label)
            row = flush_buffer_with_value(value_str, unit)
            if row:
                rows.append(row)
            continue

        buffer.append(line)

    return pd.DataFrame(rows)

def gpt_match_olf(room_name: str, olf_df: pd.DataFrame) -> tuple:
    prompt = f"""You are a fire safety expert. Your job is to classify rooms by finding the best match from a list of occupancy load factor (OLF) entries. 

Given the room name: "{room_name}"

Which of the following function labels best describes this room? Return the best match only.

List:
{chr(10).join(f'- {label}' for label in olf_df["FUNCTION OF SPACE"] if isinstance(label, str))}

Respond ONLY with the matching label string.
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        match_label = response.choices[0].message.content.strip()
        if match_label in olf_df["FUNCTION OF SPACE"].values:
            match_row = olf_df[olf_df["FUNCTION OF SPACE"] == match_label].iloc[0]
            return match_label, match_row["OCCUPANT LOAD FACTOR"], match_row["UNIT"]
        else:
            return None, None, None
    except Exception as e:
        print(f"❌ GPT API call failed: {e}")
        return None, None, None

# === MAIN TEST ===
md_path = os.path.join("data", "sbc_code_markdown.md")
olf_lines = extract_olf_section_from_markdown(md_path)
olf_df = olf_lines_to_dataframe(olf_lines)

print("📋 OLF DataFrame Loaded:", len(olf_df), "entries")

test_rooms = [
    "Reception",
    "Kitchen Area",
    "Stairs 1",
    "Corridor",
    "Electrical Room",
    "Prayer Hall",
    "Retail",
    "Office",
    "Library"
]

print("🔍 GPT Match Test Results:\n")
for room in test_rooms:
    label, value, unit = gpt_match_olf(room, olf_df)
    if label:
        print(f"🏷️ '{room}' → GPT Match: '{label}' | OLF: {value} {unit}")
    else:
        print(f"❌ No OLF match for: '{room}'")
