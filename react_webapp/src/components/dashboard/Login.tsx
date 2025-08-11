// src/components/dashboard/Login.tsx
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import pkceChallenge from "pkce-challenge";

const SPECKLE_SERVER = "https://speckle-stg.dar.com";
const CLIENT_ID = "a9bae48e35";
const REDIRECT_URI = "http://localhost:8080/login"; // must match Speckle app settings exactly
const BACKEND_URL = "http://localhost:8000"; // FastAPI backend

const Login = () => {
  const navigate = useNavigate();

  useEffect(() => {
    console.log("[Login] Component mounted. Current URL:", window.location.href);
    const params = new URLSearchParams(window.location.search);
    const code = params.get("access_code");
    const challenge = localStorage.getItem("pkce_challenge");

    console.log("[Login] access_code from URL:", code);
    console.log("[Login] Retrieved PKCE challenge:", challenge);

    if (code) {
      if (!challenge) {
        console.error("[Login] ❌ Missing PKCE challenge in localStorage");
        return;
      }

      // Call backend for secure token exchange + profile retrieval
      fetch(`${BACKEND_URL}/auth/speckle/exchange`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, challenge }),
      })
        .then((res) => {
          console.log("[Login] Backend HTTP status:", res.status);
          return res.json();
        })
        .then((data) => {
          console.log("[Login] Backend returned JSON:", data);
          if (data?.user_id) {
            localStorage.setItem("speckle_user_id", data.user_id);
            localStorage.setItem("speckle_user_email", data.email || "");
            localStorage.setItem("speckle_user_name", data.name || "");
            console.log("[Login] ✅ User stored, navigating to /");
            navigate("/", { replace: true });
          } else {
            console.error("[Login] ❌ No user_id in backend response");
          }
        })
        .catch((err) => {
          console.error("[Login] 💥 Fetch error:", err);
        });
    } else {
      console.log("[Login] No access_code in URL — waiting for user login");
    }
  }, [navigate]);

  const handleLogin = async () => {
    const { code_challenge } = await pkceChallenge();
    console.log("[Login] Generated PKCE challenge:", code_challenge);

    // Save challenge so we can send it after redirect
    localStorage.setItem("pkce_challenge", code_challenge);

    const loginUrl = `${SPECKLE_SERVER}/authn/verify/${CLIENT_ID}/${code_challenge}?redirect_uri=${encodeURIComponent(
      REDIRECT_URI
    )}`;

    console.log("[Login] Redirecting to Speckle login:", loginUrl);
    window.location.href = loginUrl;
  };

//   return (
//     <div className="flex flex-col items-center justify-center h-screen">
//       <h1 className="text-2xl font-bold mb-6">Login with Speckle</h1>
//       <button
//         onClick={handleLogin}
//         className="bg-blue-500 text-white px-4 py-2 rounded"
//       >
//         Authenticate with Speckle
//       </button>
//     </div>
//   );
return (
  <div className="flex flex-col items-center justify-center h-screen">
    <h1 className="text-2xl font-bold mb-4">Authenticate with Speckle</h1>
    <p className="text-sm text-gray-500 mb-6 text-center max-w-md">
      Please note: You must already be signed in to Speckle in your browser for authentication to work.
    </p>
    <button
      onClick={handleLogin}
      className="bg-blue-500 text-white px-4 py-2 rounded"
    >
      Authenticate
    </button>
  </div>
);
};

export default Login;




