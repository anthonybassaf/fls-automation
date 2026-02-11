# test_olf_faiss.py - Run this to verify your FAISS setup
import os
import sys
from pathlib import Path

# Test 1: Check FAISS file structure
print("="*60)
print("TEST 1: Verify FAISS Files Exist")
print("="*60)

VECTOR_DB_DIR = Path(r"C:\ProgramData\VeriFire\vector_db")
index_name = "SBC_Code_201"
index_folder = VECTOR_DB_DIR / index_name

print(f"Looking for FAISS index at: {index_folder}")
print(f"Folder exists: {index_folder.exists()}")

if index_folder.exists():
    files = list(index_folder.iterdir())
    print(f"Files in folder ({len(files)}):")
    for f in files:
        print(f"  - {f.name} ({f.stat().st_size / 1024:.1f} KB)")
    
    # Check for required files
    faiss_file = index_folder / f"{index_name}.faiss"
    pkl_file = index_folder / f"{index_name}.pkl"
    
    print(f"\nRequired files:")
    print(f"  {index_name}.faiss: {'✅ EXISTS' if faiss_file.exists() else '❌ MISSING'}")
    print(f"  {index_name}.pkl: {'✅ EXISTS' if pkl_file.exists() else '❌ MISSING'}")
else:
    print("❌ Folder does not exist!")
    print("\nTry running:")
    print(f"  python embedding\\embed_pdf_and_index.py \"C:\\ProgramData\\VeriFire\\pdfs\\SBC_Code_201.pdf\" --start-page 449 --end-page 450 --outdir \"C:\\ProgramData\\VeriFire\\vector_db\" --force")

print("\n" + "="*60)
print("TEST 2: Load FAISS Index")
print("="*60)

try:
    from langchain_community.vectorstores import FAISS
    from langchain_community.embeddings import HuggingFaceEmbeddings
    
    print("Loading embeddings model...")
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        encode_kwargs={"normalize_embeddings": True}
    )
    
    print(f"Loading FAISS index from: {index_folder}")
    print(f"Index name: {index_name}")
    
    db = FAISS.load_local(
        folder_path=str(index_folder),
        embeddings=embeddings,
        index_name=index_name,
        allow_dangerous_deserialization=True
    )
    
    print("✅ FAISS index loaded successfully!")
    
    # Test search
    print("\n" + "="*60)
    print("TEST 3: Search for TABLE 1004.1.2")
    print("="*60)
    
    query = "TABLE 1004.1.2 occupant load factor maximum floor area allowances per occupant Classroom Educational business"
    print(f"Query: {query}\n")
    
    docs = db.similarity_search(query, k=3)
    print(f"Found {len(docs)} documents\n")
    
    for i, doc in enumerate(docs, 1):
        content = doc.page_content[:500]  # First 500 chars
        print(f"Document {i}:")
        print(f"  Length: {len(doc.page_content)} chars")
        print(f"  Preview: {content}...")
        print()
        
        # Check if it contains table data
        has_table = "TABLE 1004.1.2" in doc.page_content
        has_classroom = "Classroom" in doc.page_content or "Educational" in doc.page_content
        has_values = any(val in doc.page_content for val in ["1.9", "9 gross", "4.6"])
        
        print(f"  Contains TABLE 1004.1.2: {'✅' if has_table else '❌'}")
        print(f"  Contains Classroom/Educational: {'✅' if has_classroom else '❌'}")
        print(f"  Contains OLF values: {'✅' if has_values else '❌'}")
        print()
    
except Exception as e:
    print(f"❌ Error loading FAISS: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("TEST 4: Run extract_olf.py")
print("="*60)

try:
    # Set environment to use the correct index
    os.environ["VECTOR_DB_DIR"] = str(VECTOR_DB_DIR)
    os.environ["SELECTED_CODE_PDF"] = "SBC_Code_201.pdf"
    
    print(f"VECTOR_DB_DIR: {os.environ['VECTOR_DB_DIR']}")
    print(f"SELECTED_CODE_PDF: {os.environ['SELECTED_CODE_PDF']}")
    print()
    
    from extract_olf import run_batch
    
    test_rooms = ["Classroom", "Office", "Business areas"]
    print(f"Testing rooms: {test_rooms}\n")
    
    results = run_batch(test_rooms)
    
    print("Results:")
    for room, (value, unit) in results.items():
        status = "✅" if value is not None and unit is not None else "❌"
        print(f"  {status} {room}: {value} {unit}")
    
except Exception as e:
    print(f"❌ Error running extract_olf: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("SUMMARY")
print("="*60)
print("If all tests pass ✅, your FAISS index is working correctly.")
print("If tests fail ❌, check:")
print("  1. FAISS files exist in correct location")
print("  2. Files were created from the VLM-extracted markdown")
print("  3. Markdown contains proper table formatting")