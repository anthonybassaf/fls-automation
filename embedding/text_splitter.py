from pathlib import Path
from typing import List, Dict
from langchain_text_splitters import MarkdownHeaderTextSplitter
import tiktoken


def num_tokens_from_string(string: str, encoding_name: str = "cl100k_base") -> int:
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(string))

def recursive_token_split(text: str, token_limit: int, encoding_name="cl100k_base") -> List[str]:
    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(text)

    if len(tokens) <= token_limit:
        return [text]

    split_points = text.split("\n\n")
    if len(split_points) == 1:
        split_points = text.split("\n")
    if len(split_points) == 1:
        split_points = text.split(" ")

    chunks = []
    current_chunk = ""

    for part in split_points:
        temp_chunk = current_chunk + ("\n\n" if "\n\n" in text else " ") + part
        if len(encoding.encode(temp_chunk)) > token_limit:
            if current_chunk:
                chunks += recursive_token_split(current_chunk.strip(), token_limit, encoding_name)
                current_chunk = part
            else:
                chunks += recursive_token_split(part.strip(), token_limit, encoding_name)
                current_chunk = ""
        else:
            current_chunk = temp_chunk

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks

class TextSplitter:
    def __init__(self):
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3")
            ]
        )

    def split_markdown_file(self, md_file_path: str) -> List[Dict]:
        path = Path(md_file_path)
        if not path.exists():
            raise FileNotFoundError(f"Markdown file not found: {path.resolve()}")

        content = path.read_text(encoding="utf-8")
        documents = [{
            "file_name": path.name,
            "page_content": content
        }]

        chunks = self.split_text(documents)
        return chunks

    def split_text(self, documents: List[Dict]) -> List[Dict]:
        final_chunks = []

        for doc in documents:
            file_name = doc["file_name"]
            page_content = doc["page_content"]
            header_sections = self.header_splitter.split_text(page_content)

            current_headers = None
            current_chunk = ""

            for section in header_sections:
                headers_key = tuple(sorted(section.metadata.items()))

                if headers_key == current_headers:
                    current_chunk += "\n\n" + section.page_content
                else:
                    if current_chunk:
                        if num_tokens_from_string(current_chunk) > 8000:
                            chunks = recursive_token_split(current_chunk, 8000)
                            for i, split_chunk in enumerate(chunks):
                                final_chunks.append({
                                    "file_name": file_name,
                                    "chunk_index": i,
                                    "page_content": split_chunk,
                                    "headers": dict(current_headers) if current_headers else {}
                                })
                        else:
                            final_chunks.append({
                                "file_name": file_name,
                                "chunk_index": 0,
                                "page_content": current_chunk,
                                "headers": dict(current_headers) if current_headers else {}
                            })
                    current_headers = headers_key
                    current_chunk = section.page_content

            if current_chunk:
                final_chunks.append({
                    "file_name": file_name,
                    "chunk_index": 0,
                    "page_content": current_chunk,
                    "headers": dict(current_headers) if current_headers else {}
                })

        # Deduplicate by content
        seen = set()
        unique_chunks = []
        for chunk in final_chunks:
            content = chunk["page_content"]
            if content not in seen:
                seen.add(content)
                unique_chunks.append(chunk)

        return unique_chunks
    
def markdown_splitter(pdf_path: str, output_md_path: str) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(pdf_path)
    full_text = "\n\n".join(page.extract_text() or "" for page in reader.pages)

    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    print(f"[INFO] Extracted markdown saved to: {output_md_path}")
    return output_md_path


if __name__ == "__main__":
    splitter = TextSplitter()
    chunks = splitter.split_markdown_file("sbc_code_markdown.md")
    print(f"[INFO] Total chunks created: {len(chunks)}")
    for i, chunk in enumerate(chunks[:5]):
        print(f"\n--- Chunk {i + 1} ---")
        print(f"Headers: {chunk['headers']}")
        print(chunk["page_content"][:300])
