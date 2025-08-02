from marker.pdf_parser import PDFParser
from marker.marker import Marker
from pathlib import Path

def convert_pdf_to_markdown(pdf_path: str, output_md_path: str):
    parser = PDFParser()
    pages = parser.parse(pdf_path)

    marker = Marker()
    document = marker.mark(pages)

    # Optional: inspect table blocks
    num_tables = sum(1 for block in document.blocks if block.category == "table")
    print(f"✅ Found {num_tables} tables in the PDF.")

    # Convert to Markdown
    markdown = document.to_markdown(heading_style="atx")  # use '#' headings

    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"📄 Markdown file written to: {output_md_path}")


# ✅ Test
if __name__ == "__main__":
    input_pdf = "data/SBC_Code_201.pdf"          # Change path if needed
    output_md = "data/sbc_code_markdown.md"      # Overwrites previous .md file
    convert_pdf_to_markdown(input_pdf, output_md)
