import os
from dotenv import load_dotenv
import httpx
from langchain_community.vectorstores import FAISS
from langchain_openai import AzureOpenAIEmbeddings, AzureChatOpenAI
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate

# ✅ Load environment variables
load_dotenv()

# ✅ Set proxy for OpenAI and HTTPX
os.environ["HTTP_PROXY"] = os.getenv("HTTP_PROXY", "")
os.environ["HTTPS_PROXY"] = os.getenv("HTTPS_PROXY", "")

# ✅ Debug print
print("🔑 AZURE_OPENAI_API_KEY:", os.getenv("AZURE_OPENAI_API_KEY"))
print("🔗 AZURE_OPENAI_ENDPOINT:", os.getenv("AZURE_OPENAI_ENDPOINT"))
print("📦 AZURE_DEPLOYMENT_NAME:", os.getenv("AZURE_DEPLOYMENT_NAME"))
print("📅 AZURE_API_VERSION:", os.getenv("AZURE_API_VERSION"))

# ✅ Connectivity Test Mode
if os.getenv("TEST_MODE", "false").lower() == "true":
    print("🌐 Running connectivity test to Azure OpenAI endpoint...")
    try:
        response = httpx.get(
            os.getenv("AZURE_OPENAI_ENDPOINT"),
            verify=False,
            timeout=10,
            proxies={
                "http://": os.environ["HTTP_PROXY"],
                "https://": os.environ["HTTPS_PROXY"]
            }
        )
        print(f"✅ Response status: {response.status_code}")
        print(response.text[:500])
    except Exception as e:
        print(f"❌ Connection failed: {e}")
    exit()

# ✅ Initialize Azure OpenAI embeddings
embedding = AzureOpenAIEmbeddings(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("AZURE_API_VERSION"),
    deployment=os.getenv("AZURE_DEPLOYMENT_NAME"),
)

# ✅ Initialize Azure Chat model
llm = AzureChatOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("AZURE_API_VERSION"),
    deployment_name=os.getenv("AZURE_DEPLOYMENT_NAME"),
    temperature=0,
)

# ✅ Load FAISS vectorstore
vectorstore = FAISS.load_local(
    "vector_db", index_name="faiss_index", embeddings=embedding, allow_dangerous_deserialization=True
)
retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

# ✅ Prompt template
prompt_template = PromptTemplate.from_template(
    """You are a helpful assistant knowledgeable in fire and building safety codes.
Only answer using the provided context. If the answer cannot be found in the context, say "not found in provided context."

Return your answer in the following JSON format:
{{
  "Room Function": "...",
  "Building Classification": "...",
  "Code Reference": "..."
}}

Context:
{context}

Question: {question}
Answer:"""
)

# ✅ Build Retrieval QA Chain
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=retriever,
    return_source_documents=False,
    chain_type_kwargs={"prompt": prompt_template}
)

def ask_openai_rag(query: str):
    return qa_chain.invoke({"query": query})["result"]

if __name__ == "__main__":
    while True:
        query = input("🧠 Ask a building code question (or type 'exit'): ")
        if query.lower() == "exit":
            break
        print("📝 Answer:")
        try:
            answer = ask_openai_rag(query)
            print(answer)
        except Exception as e:
            print(f"❌ Error during processing: {e}")
