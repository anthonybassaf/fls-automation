// src/components/dashboard/Login.tsx
import React, { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

// --- Speckle settings ---
const SPECKLE_SERVER = "https://speckle.dar.com"; // your Speckle server
const CLIENT_ID = "d857abe00b";                   // <-- put your Speckle App ID here

// --- VM-aware endpoints (no localhost) ---
const BACKEND_URL = `http://${window.location.hostname}:8000`; // FastAPI on VM (reverse-proxy to HTTPS in prod)
const REDIRECT_URI = `${window.location.origin}/login`;         // e.g. http://<VM-IP>:3000/login

const WELCOME_KEY = "verifire_welcome_seen";

const Login: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();

  // UI state
  const [welcomeOpen, setWelcomeOpen] = useState<boolean>(() => {
    // Show on first visit unless previously dismissed
    return localStorage.getItem(WELCOME_KEY) !== "true";
  });
  const [dontShowAgain, setDontShowAgain] = useState(false);

  // Handle redirect back from Speckle (code/access_code + optional state)
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const code = params.get("code") || params.get("access_code");
    const state = params.get("state") || "/";

    if (!code) return; // not back from Speckle yet

    const challenge = sessionStorage.getItem("speckle_code_challenge");
    if (!challenge) {
      console.error("[Login] Missing PKCE challenge in sessionStorage; cannot exchange code.");
      navigate("/", { replace: true });
      return;
    }

    fetch(`${BACKEND_URL}/auth/speckle/exchange`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, challenge, state })
    })
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok || (data as any).error) {
          console.error("[Login] Token exchange failed:", data);
          navigate("/", { replace: true });
          return;
        }
        // Minimal, ephemeral session
        sessionStorage.setItem("user_id", (data as any).user_id);
        sessionStorage.setItem("user_email", (data as any).email || "");
        sessionStorage.setItem("user_name", (data as any).name || "");
        sessionStorage.setItem("access_token", (data as any).token || "");
        sessionStorage.setItem("refresh_token", (data as any).refresh_token || "");

        sessionStorage.setItem("vf_just_logged_in", "1");
        localStorage.removeItem("speckleUrl");
        sessionStorage.removeItem("project_id");
        sessionStorage.removeItem("model_id");
        navigate(state || "/", { replace: true });
      })
      .catch((err) => {
        console.error("[Login] Exchange request error:", err);
        navigate("/", { replace: true });
      });
  }, [location.search, navigate]);

  const handleLogin = async () => {
    try {
      // 1) Get PKCE from backend (works over HTTP behind your proxy)
      const r = await fetch(`${BACKEND_URL}/auth/pkce/start`);
      if (!r.ok) {
        console.error("[Login] /auth/pkce/start failed with status", r.status);
        return;
      }
      const { verifier, challenge } = await r.json();

      // Save for exchange step
      sessionStorage.setItem("speckle_code_verifier", verifier);
      sessionStorage.setItem("speckle_code_challenge", challenge);

      // 2) Build the Speckle verify URL (path form, not query form)
      const server = SPECKLE_SERVER.replace(/\/+$/, "");
      const state = window.location.pathname + window.location.search;

      const loginUrl =
        `${server}/authn/verify/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(challenge)}` +
        `?redirect_uri=${encodeURIComponent(REDIRECT_URI)}` +
        `&state=${encodeURIComponent(state)}`;

      // 3) Redirect to Speckle
      window.location.href = loginUrl;
    } catch (e) {
      console.error("[Login] Failed to start PKCE flow:", e);
    }
  };

  const closeWelcome = () => {
    if (dontShowAgain) localStorage.setItem(WELCOME_KEY, "true");
    setWelcomeOpen(false);
  };

  // --- Copy: Welcome / README ---
  const welcomeBody = useMemo(
    () => (
      <div className="space-y-4 text-sm leading-6 text-gray-700 dark:text-gray-200">
        <p>
          <span className="font-semibold">VeriFire.ai</span> is an AI-powered platform that
          automates Fire &amp; Life Safety (FLS) compliance checks directly from your BIM data.
          It ingests key project elements (Rooms, Walls, Doors, Stairs, Furniture, Levels) and
          runs code-aware validations to surface non-compliance early—so you ship safer designs, faster.
        </p>

        <div>
          <h3 className="font-semibold text-gray-900 dark:text-white mb-1">What VeriFire.ai checks</h3>
          <ul className="list-disc ml-5 space-y-1">
            <li><span className="font-medium">Travel distance</span> from each room to its nearest emergency exit (with pathfinding that respects walls and doors).</li>
            <li><span className="font-medium">Minimum number of exits</span> required per room/classification.</li>
            <li><span className="font-medium">Occupancy intelligence</span>: Classification, Occupant Load Factor (OLF), Occupant Load, and Maximum Occupancy—predicted by a domain-tuned AI model and cross-checked against your code source.</li>
            <li>Automatic <span className="font-medium">highlighting of non-compliant rooms</span> with clear, actionable reasons.</li>
          </ul>
        </div>

        <div>
          <h3 className="font-semibold text-gray-900 dark:text-white mb-1">How it works</h3>
          <ol className="list-decimal ml-5 space-y-1">
            <li><span className="font-medium">From VeriFire interface Connect with Speckle.</span> Authenticate and select the project/model you want to review.</li>
            <li><span className="font-medium">We extract BIM context.</span> We read room boundaries, doors, stairs, and barriers, then build a navigable graph for egress analysis.</li>
            <li><span className="font-medium">AI-assisted compliance.</span> We compute distances, exit counts, and run AI classification &amp; OLF estimates—mapping results to your code set.</li>
            <li><span className="font-medium">Review and export.</span> Non-compliant spaces are flagged with reasons why.</li>
          </ol>
        </div>

        <div>
          <h3 className="font-semibold text-gray-900 dark:text-white mb-1">Requirements</h3>
          <ul className="list-disc ml-5 space-y-1">
            <li>A Speckle account on <span className="font-mono">{SPECKLE_SERVER}</span>.</li>
            <li>Your projects set to Public to be accessible through VeriFire.</li>
            <li>Models should include Rooms, Walls, Doors, Stairs (and ideally modeled Furniture).</li>
          </ul>
        </div>

        <div>
          <h3 className="font-semibold text-gray-900 dark:text-white mb-1">Data &amp; privacy</h3>
          <p>
            You can only use this tool on the projects you own or collaborate on via Speckle.
          </p>
        </div>

        <div className="rounded-md border p-3 bg-gray-50 dark:bg-zinc-900/50">
          <p className="text-xs text-gray-600 dark:text-gray-300">
            <span className="font-semibold">Tip:</span> If authentication fails, ensure you’re already logged in to Speckle in the same browser,
            at <span className="font-mono">{new URL(SPECKLE_SERVER).hostname}</span>.
          </p>
        </div>
      </div>
    ),
    []
  );

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-gray-50 to-white dark:from-zinc-900 dark:to-zinc-950 px-4">
      {/* Card */}
      <div className="w-full max-w-2xl rounded-2xl border shadow-sm bg-white dark:bg-zinc-900">
        {/* Header */}
        <div className="px-6 py-5 border-b flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-white">VeriFire.ai</h1>
            <p className="text-xs text-gray-500 dark:text-gray-400">AI-powered FLS compliance for BIM.</p>
          </div>
          <button
            onClick={() => setWelcomeOpen(true)}
            className="text-sm px-3 py-1.5 rounded-md border hover:bg-gray-50 dark:hover:bg-zinc-800"
            title="Open Welcome / README"
          >
            README
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-8">
          <h2 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
            Authenticate with Speckle
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-300 mb-6">
            Sign in to Speckle to let VeriFire.ai read your project data and run automated FLS checks.
            You should already be logged in to Speckle in this browser. 
          </p>

          <div className="flex items-center gap-3">
            <button
              onClick={handleLogin}
              className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2.5 rounded-md shadow-sm transition"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 1 0 10 10A10.011 10.011 0 0 0 12 2Zm1 15h-2v-4H7l5-6 5 6h-4Z"/></svg>
              Continue with Speckle
            </button>
          </div>
        </div>

        {/* Footer */}
         <div className="px-6 py-4 border-t text-xs text-gray-500 dark:text-gray-400 flex items-center justify-between">
          <span>v1.0</span>
        </div>
      </div>

      {/* Welcome / README Modal */}
      {welcomeOpen && (
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={closeWelcome} />
          <div className="absolute inset-0 flex items-center justify-center p-4">
            <div className="w-full max-w-3xl rounded-2xl border shadow-xl bg-white dark:bg-zinc-900 relative">
              {/* Header with blue shield icon */}
              <div className="px-6 py-5 border-b flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {/* Blue shield icon (pure SVG, no libs) */}
                  <svg
                    className="h-6 w-6 text-blue-600 dark:text-blue-400"
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path
                      fillRule="evenodd"
                      d="M12 1.5l8.485 3.182a.75.75 0 0 1 .515.711v5.57a12 12 0 0 1-6.343 10.62l-1.742.954a.75.75 0 0 1-.73 0l-1.742-.955A12 12 0 0 1 3 10.964V5.393a.75.75 0 0 1 .515-.711L12 1.5zm3.53 7.72a.75.75 0 0 0-1.06-1.06l-4.22 4.22-1.72-1.72a.75.75 0 1 0-1.06 1.06l2.25 2.25a.75.75 0 0 0 1.06 0l4.75-4.75z"
                      clipRule="evenodd"
                    />
                  </svg>

                  <div>
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                      Welcome to VeriFire.ai
                    </h2>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      FLS compliance, accelerated.
                    </p>
                  </div>
                </div>

                <button
                  onClick={closeWelcome}
                  className="rounded-md p-2 hover:bg-gray-100 dark:hover:bg-zinc-800"
                  aria-label="Close"
                  title="Close"
                >
                  ✕
                </button>
              </div>

              <div className="px-6 py-6 max-h-[70vh] overflow-auto">
                {welcomeBody}
              </div>

              <div className="px-6 py-4 border-t flex items-center justify-between">
                <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                  <input
                    type="checkbox"
                    className="rounded border-gray-300"
                    checked={dontShowAgain}
                    onChange={(e) => setDontShowAgain(e.target.checked)}
                  />
                  Don’t show this again
                </label>
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => setWelcomeOpen(false)}
                    className="text-sm px-3 py-2 rounded-md border hover:bg-gray-50 dark:hover:bg-zinc-800"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Login;
