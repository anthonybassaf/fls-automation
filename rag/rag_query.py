import os
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document

# === Load FAISS Index ===
print("📦 Loading FAISS index...")
embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
vectorstore = FAISS.load_local("vector_db", 
                               index_name="faiss_index", 
                               embeddings=embedding,
                               allow_dangerous_deserialization=True
                               )

# === Load Local Mistral Model ===
print("🧠 Loading Mistral model...")
tokenizer = AutoTokenizer.from_pretrained("models/mistral")
model = AutoModelForCausalLM.from_pretrained("models/mistral",
                                             torch_dtype=torch.float32,
                                              device_map={"": "cpu"})

llm_pipeline = pipeline("text-generation", model=model, tokenizer=tokenizer)

def ask_local_rag(query: str) -> str:
    # Retrieve relevant documents from FAISS
    docs = vectorstore.similarity_search(query, k=3)
    context = "\n\n".join(doc.page_content for doc in docs if isinstance(doc, Document))

    # Construct prompt for Mistral
    prompt = f"""You are a helpful assistant knowledgeable in fire and building safety codes.
Use the following context to answer the question.

Context:
{context}

Question: {query}
Answer:"""

    response = llm_pipeline(prompt, max_new_tokens=300, do_sample=True, temperature=0.7)[0]["generated_text"]
    return response

# === CLI Interface ===
if __name__ == "__main__":
    while True:
        query = input("\n🧠 Ask a building code question (or type 'exit'): ")
        if query.lower() in {"exit", "quit"}:
            break
        answer = ask_local_rag(query)
        print(f"\n📝 Answer:\n{answer}")
