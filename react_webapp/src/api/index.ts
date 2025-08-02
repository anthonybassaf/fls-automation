const API_BASE = "http://localhost:8000";

export async function apiGet(path: string) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function apiPost(path: string, body?: any) {
  const options: RequestInit = {
    method: "POST",
    headers: {},
  };

  if (body instanceof FormData) {
    options.body = body; // for file uploads
  } else if (body) {
    options.body = JSON.stringify(body);
    options.headers = { "Content-Type": "application/json" };
  }

  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
