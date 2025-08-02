from openai import AzureOpenAI
from dotenv import load_dotenv
import os

load_dotenv()

print(os.getenv("AZURE_API_KEY"))
print(os.getenv("API_VERSION"))
print(os.getenv("AZURE_ENDPOINT"))
print(os.getenv("DEPLOYMENT"))


client = AzureOpenAI(api_key=os.getenv("AZURE_API_KEY"), api_version=os.getenv("API_VERSION"), azure_endpoint=os.getenv("AZURE_ENDPOINT"))
response = client.chat.completions.create(
    model=os.getenv("DEPLOYMENT"),
    messages=[{"role": "user", "content": "Hello, what model are you?"}]
)

print(response.choices[0].message.content)
