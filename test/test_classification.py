import re
from pathlib import Path
from openai import OpenAI
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

client = OpenAI()

# Load FAISS vectorstore
embedding = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = FAISS.load_local(
    "vector_db",
    embeddings=embedding,
    index_name="faiss_index",
    allow_dangerous_deserialization=True,
)

def extract_classification_sections_with_gpt(room_name: str) -> str | None:
    """
    Retrieve the most appropriate building classification for a room name using GPT and a FAISS vectorstore.
    """
    k = 6
    relevant_chunks = vectorstore.similarity_search(room_name, k=k)
    combined_context = "\n\n".join(doc.page_content for doc in relevant_chunks)

    prompt = f"""
You are a fire safety code expert. Based on the classification information below, classify the room named "{room_name}" into the most appropriate SBC classification group (e.g., Group B, Group A-3, Group M, etc.).

SBC Classification Information:
{combined_context}

Respond in the format:
Classification: Group <letter-or-subgroup>
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You classify rooms based on building codes."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        content = response.choices[0].message.content
        print(f"📦 GPT response: {content}")
        match = re.search(r"Classification:\s*Group\s*([A-Z]+(?:-[0-9]+)?)", content, re.IGNORECASE)
        if match:
            return f"Group {match.group(1).upper()}"
    except Exception as e:
        print(f"❌ GPT classification failed: {e}")
        return None

    print(f"❌ No classification found for: {room_name}")
    return None

# Run tests
room_names = [
    "Office",
    "General Office",
    "Meeting Room",
    "Prayer Hall",
    "Retail",
    "Educational Lab",
    "Kitchen",
    "Apartment 2 bedrooms",
    "Staging Area",
    "Mechanical Services",
    "Unknown Room"
]

print("\n🔍 GPT Classification Test Results:")
for name in room_names:
    result = extract_classification_sections_with_gpt(name)
    print(f"{name:<30} → {result}")
