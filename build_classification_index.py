import os
import json
import re
from pathlib import Path
from collections import defaultdict

def normalize(text):
    return re.sub(r"[^a-zA-Z0-9\s]", "", text).lower().strip()

def extract_classification_sections(markdown_text):
    # Find headings like "SECTION 304  BUSINESS GROUP B"
    pattern = re.compile(r"(SECTION\s+30\d+\s+[\w\s]+GROUP\s+([A-Z]-?\d?)\b)(.*?)((?=\nSECTION\s+30\d+)|\Z)", re.DOTALL)
    return pattern.findall(markdown_text)

def extract_room_types(section_text):
    """
    Look for bullet points or inline lists of room types.
    """
    bullet_lines = re.findall(r"•\s*(.+)", section_text)
    # Split on commas or semicolons for inline room type listings
    flat_room_names = []
    for line in bullet_lines:
        parts = re.split(r",|;|including but not limited to|including|but not limited to", line, flags=re.IGNORECASE)
        flat_room_names.extend(p.strip(" .") for p in parts if p.strip())
    return flat_room_names

def build_classification_index(markdown_path: str) -> dict:
    text = Path(markdown_path).read_text(encoding="utf-8")
    index = {}

    sections = extract_classification_sections(text)
    for full_heading, group_code, section_body, _ in sections:
        # Try to find section number in heading
        match = re.search(r"SECTION\s+(30\d+)", full_heading)
        section_number = match.group(1) if match else "Unknown"
        code_ref = f"Section {section_number}"

        room_types = extract_room_types(section_body)
        for room in room_types:
            key = normalize(room)
            index[key] = {
                "classification": f"Group {group_code}",
                "reference": code_ref,
                "original_name": room
            }

    return index

def find_markdown_file(directory: str, keyword: str = "markdown") -> str:
    """Searches for the first .md file containing a keyword in the given directory."""
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".md") and keyword.lower() in file.lower():
                return os.path.join(root, file)
    raise FileNotFoundError(f"No markdown file containing '{keyword}' found in {directory}")

if __name__ == "__main__":
    search_dir = "data"  # adjust this to where your markdown is likely stored
    try:
        md_path = find_markdown_file(search_dir)
        print(f"📄 Found Markdown file: {md_path}")
        index = build_classification_index(md_path)
        print("✅ Classification index built successfully.")
        output_path = "classification_index.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

            print(f"💾 Index saved to {output_path}")
    except FileNotFoundError as e:
        print(f"❌ {e}")

