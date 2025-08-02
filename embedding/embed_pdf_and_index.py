# import os
# import sys
# from text_splitter import markdown_splitter
# from faiss_setup import create_faiss_vectorstore

# # PDF to embed
# pdf_path = sys.argv[1]
# pdf_basename = os.path.splitext(os.path.basename(pdf_path))[0]

# # Output paths
# markdown_path = os.path.join("data", f"{pdf_basename}_markdown.md")
# vectorstore_path = os.path.join("vector_db")  # will create subfolder with same name

# print(f"[INFO] Processing {pdf_path}...")

# # Convert PDF → markdown
# markdown_splitter(pdf_path, markdown_path)

# # Create vectorstore in correct location
# create_faiss_vectorstore(markdown_path, vectorstore_path)

import os
import argparse
from text_splitter import TextSplitter
from faiss_setup import create_faiss_vectorstore


def ensure_markdown_for_pdf(pdf_path: str) -> str:
    """
    Ensure a corresponding Markdown file exists for the given PDF.
    If not, convert the PDF to Markdown with structured headers.
    Returns the markdown file path.
    """
    md_path = os.path.splitext(pdf_path)[0] + ".md"
    if not os.path.exists(md_path):
        print(f"[INFO] Markdown file not found for {pdf_path}, creating {md_path}...")
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)

            with open(md_path, "w", encoding="utf-8") as f:
                for i, page in enumerate(doc, start=1):
                    page_text = page.get_text().strip()
                    if not page_text:
                        continue
                    f.write(f"# Page {i}\n\n")
                    f.write(page_text + "\n\n")

            doc.close()
            print(f"[INFO] Markdown created: {md_path}")
        except Exception as e:
            print(f"[ERROR] Failed to convert PDF to Markdown: {e}")
            raise
    else:
        print(f"[INFO] Markdown file already exists: {md_path}")
    return md_path


def main(pdf_path: str):
    print(f"[INFO] Starting indexing for: {pdf_path}")

    # Step 1: Ensure markdown exists
    markdown_path = ensure_markdown_for_pdf(pdf_path)

    # Step 2: Split markdown into chunks
    print(f"[INFO] Splitting markdown: {markdown_path}")
    splitter = TextSplitter()
    chunks = splitter.split_markdown_file(markdown_path)
    print(f"[INFO] Total chunks created: {len(chunks)}")

    # Step 3: Create FAISS vectorstore index
    print(f"[INFO] Creating FAISS index...")
    create_faiss_vectorstore(markdown_path, output_dir="vector_db")
    print(f"[INFO] Indexing complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Embed and index a PDF document")
    parser.add_argument("pdf_path", type=str, help="Path to the uploaded PDF file")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"[ERROR] File not found: {args.pdf_path}")
        exit(1)

    main(args.pdf_path)
