# test_max_occupancy.py
from extract_max_occupancy import extract_max_occupancy_table_from_text
#get_gpt_max_occupancy_for_classification

df = extract_max_occupancy_table_from_text("data/sbc_code_markdown.md")
print(df)

# test_inputs = [
#     "Group F-1",
#     "Group B",
#     "Group A-3",
#     "Group H-3",
#     "Group R-4",
#     "Group X"  # Invalid on purpose
# ]

# for group in test_inputs:
#     value = get_gpt_max_occupancy_for_classification(group)
#     print(f"\n➡️ {group} → {value}")
