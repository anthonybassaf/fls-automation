# embedding/embed_pdf_and_index.py
import os
import sys
import argparse
import base64
from io import BytesIO
from pathlib import Path
from typing import List, Dict
import requests
from dotenv import load_dotenv
from text_splitter import TextSplitter
from faiss_setup import create_faiss_vectorstore

try:
    import fitz  # PyMuPDF
except ImportError:
    print("[ERROR] PyMuPDF not installed. Run: pip install pymupdf")
    sys.exit(1)

load_dotenv()

# Qwen VLM Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://172.21.3.6:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "/v1/chat/completions")
HTTP_TIMEOUT = (120, 600)  # connect, read


def image_to_base64(pil_image) -> str:
    """Convert PIL image to base64 string."""
    buffered = BytesIO()
    pil_image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def extract_page_with_qwen_vlm(page_image_base64: str, page_num: int) -> str:
    """
    Use Qwen VLM to extract structured content from a PDF page image.
    Focuses on accurate table extraction.
    """
    url = f"{OLLAMA_URL.rstrip('/')}/{OLLAMA_ENDPOINT.lstrip('/')}"
    
    prompt = f"""You are a precise document extraction assistant. Extract ALL content from this PDF page (Page {page_num}) with HIGH ACCURACY.

CRITICAL REQUIREMENTS FOR TABLES:
1. Preserve exact table structure with clear row/column alignment
2. Use markdown table format: | Column1 | Column2 | Column3 |
3. Maintain numeric precision (e.g., 1.9, 0.65, 28)
4. Keep units exactly as shown (gross, net, kN/m², etc.)
5. Preserve hierarchical relationships (parent rows with child rows indented)

For TABLE 1004.1.2 and similar tables:
- Extract as proper markdown table
- Keep "FUNCTION OF SPACE" and "OCCUPANT LOAD FACTOR" as column headers
- Align values correctly with their row labels
- Preserve all footnote markers and references

For regular text:
- Maintain paragraph structure
- Preserve section numbers and headers
- Keep formulas and special formatting

Output format:
# Page {page_num}

[Extracted content with proper markdown formatting]

Be extremely accurate with tables - this is for building code compliance calculations."""

    try:
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{page_image_base64}"}
                        }
                    ]
                }
            ],
            "temperature": 0.0,
            "max_tokens": 4096,
            "stream": False
        }
        
        print(f"[INFO] Extracting page {page_num} with Qwen VLM...", end=" ", flush=True)
        resp = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        
        rj = resp.json()
        content = rj.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        if not content:
            print("⚠️  Empty response")
            return f"# Page {page_num}\n\n[Empty page or extraction failed]\n\n"
        
        print("✓")
        return content.strip() + "\n\n"
        
    except Exception as ex:
        print(f"✗ Error: {ex}")
        return f"# Page {page_num}\n\n[Extraction failed: {ex}]\n\n"


def pdf_to_markdown_with_vlm(pdf_path: str, output_md_path: str, start_page: int = 1, end_page: int = None):
    """
    Convert PDF to Markdown using Qwen VLM for accurate table extraction.
    
    Args:
        pdf_path: Path to input PDF
        output_md_path: Path for output markdown file
        start_page: First page to process (1-indexed)
        end_page: Last page to process (None = all pages)
    """
    print(f"[INFO] Opening PDF: {pdf_path}")
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    if end_page is None:
        end_page = total_pages
    
    end_page = min(end_page, total_pages)
    
    print(f"[INFO] Processing pages {start_page} to {end_page} of {total_pages}")
    print(f"[INFO] Using Qwen VLM model: {OLLAMA_MODEL}")
    print(f"[INFO] Ollama endpoint: {OLLAMA_URL}")
    
    markdown_content = []
    
    for page_num in range(start_page - 1, end_page):
        page = doc[page_num]
        
        progress_pct = ((page_num - start_page + 2) / (end_page - start_page + 1)) * 100
        print(f"\n[{progress_pct:.1f}%] Page {page_num + 1}/{end_page}...", flush=True)

        # Render page to image at high DPI for better OCR
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom = ~144 DPI
        
        # Convert to PIL Image for base64 encoding
        from PIL import Image
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # Extract content with VLM
        img_base64 = image_to_base64(img)
        page_content = extract_page_with_qwen_vlm(img_base64, page_num + 1)
        markdown_content.append(page_content)
    
    doc.close()
    
    # Write to file
    print(f"[INFO] Writing markdown to: {output_md_path}")
    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(markdown_content))
    
    print(f"[INFO] ✓ Markdown extraction complete: {len(markdown_content)} pages processed")


def main(pdf_path: str, output_dir: str = "vector_db", force_reextract: bool = False, start_page: int = 1, end_page: int = None):
    """
    Main pipeline: PDF → Markdown (with VLM) → Chunks → FAISS Index
    
    Args:
        pdf_path: Path to PDF file
        output_dir: Directory where FAISS index will be saved
        force_reextract: Force re-extraction even if markdown exists
        start_page: First page to process
        end_page: Last page to process (None = all)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"[ERROR] PDF not found: {pdf_path}")
        sys.exit(1)
    
    # Determine markdown path (save next to PDF)
    markdown_path = pdf_path.with_suffix(".md")
    
    # Step 1: Convert PDF to Markdown using VLM
    if force_reextract or not markdown_path.exists():
        print(f"[INFO] Converting PDF to Markdown with Qwen VLM...")
        pdf_to_markdown_with_vlm(str(pdf_path), str(markdown_path), start_page, end_page)
    else:
        print(f"[INFO] Using existing markdown: {markdown_path}")
        print(f"[INFO] (Use --force to re-extract)")
    
    # Step 2: Split markdown into chunks
    print(f"[INFO] Splitting markdown into chunks...")
    splitter = TextSplitter()
    chunks = splitter.split_markdown_file(str(markdown_path))
    print(f"[INFO] Created {len(chunks)} chunks")
    
    # Step 3: Create FAISS index
    print(f"[INFO] Creating FAISS vectorstore index...")
    print(f"[INFO] Output directory: {output_dir}")
    create_faiss_vectorstore(str(markdown_path), output_dir=output_dir)
    
    print(f"[INFO] ✓✓✓ Complete! Index ready for use.")
    print(f"[INFO] Markdown saved: {markdown_path}")
    print(f"[INFO] FAISS index: {output_dir}/{pdf_path.stem}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Embed PDF using Qwen VLM for accurate table extraction"
    )
    parser.add_argument("pdf_path", type=str, help="Path to PDF file")
    parser.add_argument("--outdir", type=str, default="vector_db", help="Output directory for FAISS index")
    parser.add_argument("--force", action="store_true", help="Force re-extraction of markdown")
    parser.add_argument("--start-page", type=int, default=1, help="First page to process (1-indexed)")
    parser.add_argument("--end-page", type=int, default=None, help="Last page to process")
    
    args = parser.parse_args()
    
    main(args.pdf_path, args.outdir, args.force, args.start_page, args.end_page)