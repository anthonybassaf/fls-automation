import { Shield } from "lucide-react";

export const Header = () => {
  return (
    <header className="bg-dashboard-header text-white px-6 py-4 shadow-panel">
      <div className="flex items-center gap-3">
        <Shield className="h-6 w-6" />
        <h1 className="text-xl font-semibold">Fire & Life Safety Compliance Dashboard</h1>
      </div>
    </header>
  );
};

// // Header.tsx
// import { Shield, LogIn, LogOut } from "lucide-react";
// import { useEffect, useState } from "react";
// import pkceChallenge from "pkce-challenge";

// const SPECKLE_SERVER = "https://speckle-stg.dar.com";
// const REDIRECT_URI = "http://localhost:8080/";
// const CLIENT_ID = "a9bae48e35"; // Your Speckle PKCE App ID

// export const Header = () => {
//   const [isAuthenticated, setIsAuthenticated] = useState(false);

//   useEffect(() => {
//     const token = localStorage.getItem("speckle_token");
//     if (token) setIsAuthenticated(true);
//   }, []);

//   const login = async () => {
//     const { code_challenge, code_verifier } = await pkceChallenge();
//     localStorage.setItem("pkce_verifier", code_verifier);

//     const loginUrl = `${SPECKLE_SERVER}/authn/verify/${CLIENT_ID}/${code_challenge}?redirect_uri=${encodeURIComponent(
//       REDIRECT_URI
//     )}`;

//     window.location.href = loginUrl;
//   };

//   const logout = () => {
//     localStorage.removeItem("speckle_token");
//     localStorage.removeItem("pkce_verifier");
//     window.location.reload();
//   };

//   return (
//     <header className="bg-dashboard-header text-white px-6 py-4 shadow-panel">
//       <div className="flex justify-between items-center">
//         <div className="flex items-center gap-3">
//           <Shield className="h-6 w-6" />
//           <h1 className="text-xl font-semibold">Fire & Life Safety Compliance Dashboard</h1>
//         </div>
//         <div>
//           {isAuthenticated ? (
//             <button onClick={logout} className="flex items-center gap-2 text-sm font-medium">
//               <LogOut className="h-5 w-5" />
//               Logout
//             </button>
//           ) : (
//             <button onClick={login} className="flex items-center gap-2 text-sm font-medium">
//               <LogIn className="h-5 w-5" />
//               Login with Speckle
//             </button>
//           )}
//         </div>
//       </div>
//     </header>
//   );
// };


