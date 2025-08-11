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
const BACKEND_URL = "http://localhost:8000";

const ProtectedRoute = ({ children }: { children: JSX.Element }) => {
  const [authChecked, setAuthChecked] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    const userId = localStorage.getItem("speckle_user_id");
    if (!userId) {
      console.log("[ProtectedRoute] No user_id in localStorage");
      setAuthChecked(true);
      setIsLoggedIn(false);
      return;
    }

    // Validate the user with backend
    fetch(`${BACKEND_URL}/auth/whoami?user_id=${encodeURIComponent(userId)}`)
      .then((res) => res.json())
      .then((data) => {
        if (data?.email) {
          console.log("[ProtectedRoute] ✅ User validated:", data.email);
          setIsLoggedIn(true);
        } else {
          console.warn("[ProtectedRoute] ❌ User not recognized");
          localStorage.removeItem("speckle_user_id");
          localStorage.removeItem("speckle_user_email");
          localStorage.removeItem("speckle_user_name");
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