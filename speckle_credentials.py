# speckle_credentials.py

from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Speckle credentials
SPECKLE_SERVER_URL = os.getenv("SPECKLE_SERVER_URL")
PROJECT_ID = os.getenv("PROJECT_ID")
MODEL_ID = os.getenv("MODEL_ID")
VERSION_ID = os.getenv("VERSION_ID")
BRANCH_NAME = os.getenv("BRANCH_NAME")

# Speckle token for authentication
SPECKLE_TOKEN_STG = os.getenv("SPECKLE_TOKEN_STG")
SPECKLE_TOKEN_PATHS = os.getenv("SPECKLE_TOKEN_PATHS")
SPECKLE_TOKEN_FLS = os.getenv("SPECKLE_TOKEN_FLS")
SPECKLE_TOKEN_CORRECTION = os.getenv("SPECKLE_TOKEN_CORRECTION")

# Optional: Validate that all required env vars are set
def validate_credentials():
    missing = [
        key for key in ["SPECKLE_SERVER_URL", "PROJECT_ID", "MODEL_ID", "BRANCH_NAME", "SPECKLE_TOKEN_STG", "SPECKLE_TOKEN_PATHS", "SPECKLE_TOKEN_FLS"]
        if not globals().get(key)
    ]
    if missing:
        raise ValueError(f"Missing required Speckle credentials: {', '.join(missing)}")

validate_credentials()
