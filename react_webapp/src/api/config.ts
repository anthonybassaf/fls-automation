// src/api/config.ts

export const API_BASE = "http://localhost:8000"; // Backend base URL

export const SPECKLE_SERVER_URL = "https://speckle.dar.com"; // Your Speckle server URL
export const SPECKLE_CLIENT_ID = "d857abe00b"; // Your Speckle App ID

// Construct redirect URI based on current environment
export const SPECKLE_REDIRECT_URI = `${window.location.origin}/login`;
