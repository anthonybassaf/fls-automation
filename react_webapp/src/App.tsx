// src/App.tsx
import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Index from "./pages/Index";
import NotFound from "./pages/NotFound";
import Login from "./components/dashboard/Login";
import { Toaster } from "./components/ui/toaster";
import { Toaster as Sonner } from "./components/ui/sonner";
import { TooltipProvider } from "./components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const queryClient = new QueryClient();
// Use the VM host dynamically (no localhost)
// const BACKEND_URL = `http://172.31.3.48:8000`;
const BACKEND_URL = `http://${window.location.hostname}:8000`;

const ProtectedRoute = ({ children }: { children: JSX.Element }) => {
  const [authChecked, setAuthChecked] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    // Prefer the new sessionStorage key set by Login.tsx
    let userId =
      sessionStorage.getItem("user_id") ||
      // Backward-compat: support old key and migrate it
      localStorage.getItem("speckle_user_id");

    if (!userId) {
      console.log("[ProtectedRoute] No user_id found (session/local storage)");
      setAuthChecked(true);
      setIsLoggedIn(false);
      return;
    }

    // Migrate old localStorage keys to sessionStorage (ephemeral)
    if (!sessionStorage.getItem("user_id") && localStorage.getItem("speckle_user_id")) {
      sessionStorage.setItem("user_id", userId);
      const email = localStorage.getItem("speckle_user_email") || "";
      const name = localStorage.getItem("speckle_user_name") || "";
      sessionStorage.setItem("user_email", email);
      sessionStorage.setItem("user_name", name);
      // Optionally clear old keys
      localStorage.removeItem("speckle_user_id");
      localStorage.removeItem("speckle_user_email");
      localStorage.removeItem("speckle_user_name");
    }

    // Always read from sessionStorage after migration
    userId = sessionStorage.getItem("user_id") || "";

    // Validate the user with backend
    fetch(`${BACKEND_URL}/auth/whoami?user_id=${encodeURIComponent(userId)}`)
      .then((res) => res.json())
      .then((data) => {
        if (data?.email) {
          console.log("[ProtectedRoute] ✅ User validated:", data.email);
          setIsLoggedIn(true);
        } else {
          console.warn("[ProtectedRoute] ❌ User not recognized");
          sessionStorage.removeItem("user_id");
          sessionStorage.removeItem("user_email");
          sessionStorage.removeItem("user_name");
          setIsLoggedIn(false);
        }
      })
      .catch((err) => {
        console.error("[ProtectedRoute] 💥 Error validating user:", err);
        setIsLoggedIn(false);
      })
      .finally(() => {
        setAuthChecked(true);
      });
  }, []);

  if (!authChecked) {
    return <div className="p-4">🔄 Checking login...</div>;
  }

  return isLoggedIn ? children : <Navigate to="/login" replace />;
};

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Toaster />
        <Sonner />
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Index />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </BrowserRouter>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
