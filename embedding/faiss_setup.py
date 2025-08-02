import os
import sys
import pickle
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from text_splitter import TextSplitter
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings

# Load environment variables
load_dotenv()

def create_faiss_vectorstore(md_path: str, output_dir: str):
    splitter = TextSplitter()
    
    with open(md_path, "r", encoding="utf-8") as f:
        markdown_text = f.read()

    docs = splitter.split_text([{
        "page_content": markdown_text,
        "file_name": os.path.basename(md_path)
    }])

    texts = [doc["page_content"] for doc in docs]
    metadatas = [doc["headers"] for doc in docs]

    embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

    # Create vectorstore
    vectorstore = FAISS.from_texts(texts=texts, embedding=embedding, metadatas=metadatas)

    # Define save path
    pdf_basename = os.path.splitext(os.path.basename(md_path))[0]
    output_folder = os.path.join(output_dir, pdf_basename)
    os.makedirs(output_folder, exist_ok=True)

    # Save with the same name inside the subfolder
    vectorstore.save_local(output_folder, index_name=pdf_basename)

    print(f"[INFO] FAISS index saved to: {output_folder}/{pdf_basename}.faiss and .pkl")



if __name__ == "__main__":
    print("[INFO] Splitting markdown...")
    splitter = TextSplitter()

    markdown_path = "data/sbc_code_markdown.md"
    pdf_basename = os.path.splitext(os.path.basename(markdown_path))[0]
    index_name = f"{pdf_basename}_index"

    with open(markdown_path, "r", encoding="utf-8") as f:
        markdown_text = f.read()

    docs = splitter.split_text([
        {"page_content": markdown_text, "file_name": os.path.basename(markdown_path)}
    ])

    texts = [doc["page_content"] for doc in docs]
    metadatas = [doc["headers"] for doc in docs]

    print("[INFO] Creating FAISS index...")
    embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vectorstore = FAISS.from_texts(texts=texts, embedding=embedding, metadatas=metadatas)

    vectorstore.save_local("vector_db", index_name=index_name)
    print(f"[INFO] FAISS index saved to: vector_db/{index_name}.faiss and vector_db/{index_name}.pkl")
