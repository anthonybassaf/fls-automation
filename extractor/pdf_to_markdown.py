import fitz  # PyMuPDF
import os

def extract_markdown_from_pdf(pdf_path: str, output_md_path: str) -> None:
    """
    Extracts text from a PDF and writes it to a markdown (.md) file.
    """
    doc = fitz.open(pdf_path)
    markdown_lines = []

    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text")
        if text:
            markdown_lines.append(f"# Page {page_num}\n\n{text.strip()}\n")

    full_markdown = "\n\n".join(markdown_lines)

    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write(full_markdown)

    print(f"✅ Extracted markdown saved to: {output_md_path}")


if __name__ == "__main__":
    # You can adjust these paths
    pdf_file = "SBC_Code_201.pdf"
    output_file = "sbc_code_markdown.md"

    extract_markdown_from_pdf(pdf_file, output_file)
