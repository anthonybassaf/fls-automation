// import { useState, useEffect } from "react";
// import { Button } from "../../components/ui/button";
// import { Input } from "../../components/ui/input";
// import { RefreshCcw } from "lucide-react";


// export const SpeckleViewer = () => {
//   const [inputUrl, setInputUrl] = useState("");
//   const [speckleUrl, setSpeckleUrl] = useState<string | null>(null);

//   // Load saved URL on page refresh
//   useEffect(() => {
//     const savedUrl = localStorage.getItem("speckleUrl");
//     if (savedUrl) {
//       setSpeckleUrl(savedUrl);
//     }
//   }, []);

//   const handleLoadModel = async () => {
//     try {
//       const url = new URL(inputUrl);

//       const parts = url.pathname.split("/");
//       const projectIndex = parts.indexOf("projects");
//       const modelsIndex = parts.indexOf("models");

//       if (projectIndex === -1 || modelsIndex === -1) {
//         alert("Invalid Speckle URL. Please paste a valid project/model URL.");
//         return;
//       }

//       const projectId = parts[projectIndex + 1];
//       const modelId = parts[modelsIndex + 1];

//       // Update backend environment
//       await fetch("http://localhost:8000/set-project", {
//         method: "POST",
//         headers: { "Content-Type": "application/json" },
//         body: JSON.stringify({ project_id: projectId, model_id: modelId }),
//       });

//       const embed = `${url.origin}/projects/${projectId}/models/${modelId}#embed=%7B%22isEnabled%22%3Atrue%7D`;


//       setSpeckleUrl(embed);
//       localStorage.setItem("speckleUrl", embed);
//     } catch (err) {
//       alert("Invalid URL format.");
//     }
//   };

//   const handleChangeModel = () => {
//     localStorage.removeItem("speckleUrl");
//     setSpeckleUrl(null);
//   };

//   if (!speckleUrl) {
//     return (
//       <div className="flex flex-1 items-center justify-center bg-dashboard-panel rounded-lg shadow-panel">
//         <div className="bg-white rounded-lg p-8 shadow-lg max-w-md w-full space-y-4">
//           <h2 className="text-lg font-semibold">Enter Speckle Model URL</h2>
//           <p className="text-sm text-gray-600">
//             Paste the Speckle model link (projects/.../models/...) you want to work on.
//           </p>
//           <Input
//             type="text"
//             placeholder="https://speckle-stg.dar.com/projects/xxx/models/yyy"
//             value={inputUrl}
//             onChange={(e) => setInputUrl(e.target.value)}
//           />
//           <Button onClick={handleLoadModel} className="w-full">
//             Load Model
//           </Button>
//         </div>
//       </div>
//     );
//   }

//   return (
//       <div className="flex flex-1 min-h-0 bg-dashboard-panel rounded-lg shadow-panel overflow-hidden relative">
//         {/* Icon button for changing model */}
//         <button
//           onClick={handleChangeModel}
//           title="Change Model"
//           className="absolute top-4 right-4 z-10 bg-white/90 border rounded-full p-2 shadow hover:bg-gray-100"
//         >
//           <RefreshCcw className="h-5 w-5 text-gray-700" />
//         </button>

//         <iframe
//           src={speckleUrl}
//           className="w-full h-full rounded-lg"
//           frameBorder="0"
//           title="Speckle 3D Model Viewer"
//           allowFullScreen
//         />
//       </div>
//     );
// };


import { useState, useEffect } from "react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { RefreshCcw } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../../components/ui/dialog";

const BACKEND_URL = "http://localhost:8000";

export const SpeckleViewer = () => {
  const [inputUrl, setInputUrl] = useState("");
  const [speckleUrl, setSpeckleUrl] = useState<string | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);

  // Load saved URL on page refresh
  useEffect(() => {
    const savedUrl = localStorage.getItem("speckleUrl");
    if (savedUrl) {
      setSpeckleUrl(savedUrl);
    }
  }, []);

  const handleLoadModel = async () => {
    try {
      const url = new URL(inputUrl);

      const parts = url.pathname.split("/");
      const projectIndex = parts.indexOf("projects");
      const modelsIndex = parts.indexOf("models");

      if (projectIndex === -1 || modelsIndex === -1) {
        alert("Invalid Speckle URL. Please paste a valid project/model URL.");
        return;
      }

      const projectId = parts[projectIndex + 1];
      const modelId = parts[modelsIndex + 1];

      // 🔹 Check access with backend
      const userId = localStorage.getItem("speckle_user_id") || "";
      const accessRes = await fetch(
        `${BACKEND_URL}/auth/check-access?user_id=${encodeURIComponent(
          userId
        )}&speckle_url=${encodeURIComponent(inputUrl)}`
      );
      const accessData = await accessRes.json();

      if (!accessData.access) {
        alert(accessData.reason || "Access denied");
        setAccessDenied(true);
        return;
      }


      // 🔹 Update backend environment
      await fetch(`${BACKEND_URL}/set-project`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId, model_id: modelId }),
      });

      const embed = `${url.origin}/projects/${projectId}/models/${modelId}#embed=%7B%22isEnabled%22%3Atrue%7D`;

      setSpeckleUrl(embed);
      localStorage.setItem("speckleUrl", embed);
    } catch (err) {
      alert("Invalid URL format.");
    }
  };

  const handleChangeModel = () => {
    localStorage.removeItem("speckleUrl");
    setSpeckleUrl(null);
  };

  return (
    <>
      {/* Access Denied Dialog */}
      <Dialog open={accessDenied}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Access Denied</DialogTitle>
          </DialogHeader>
          <p>
            You do not have the required permissions to access this project.
            Please contact the Project Owner.
          </p>
          <DialogFooter>
            <Button
              onClick={() => {
                setAccessDenied(false);
                handleChangeModel();
              }}
            >
              OK
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {!speckleUrl ? (
        <div className="flex flex-1 items-center justify-center bg-dashboard-panel rounded-lg shadow-panel">
          <div className="bg-white rounded-lg p-8 shadow-lg max-w-md w-full space-y-4">
            <h2 className="text-lg font-semibold">Enter Speckle Model URL</h2>
            <p className="text-sm text-gray-600">
              Paste the Speckle model link (projects/.../models/...) you want to
              work on.
            </p>
            <Input
              type="text"
              placeholder="https://speckle-stg.dar.com/projects/xxx/models/yyy"
              value={inputUrl}
              onChange={(e) => setInputUrl(e.target.value)}
            />
            <Button onClick={handleLoadModel} className="w-full">
              Load Model
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 min-h-0 bg-dashboard-panel rounded-lg shadow-panel overflow-hidden relative">
          {/* Icon button for changing model */}
          <button
            onClick={handleChangeModel}
            title="Change Model"
            className="absolute top-4 right-4 z-10 bg-white/90 border rounded-full p-2 shadow hover:bg-gray-100"
          >
            <RefreshCcw className="h-5 w-5 text-gray-700" />
          </button>

          <iframe
            src={speckleUrl}
            className="w-full h-full rounded-lg"
            frameBorder="0"
            title="Speckle 3D Model Viewer"
            allowFullScreen
          />
        </div>
      )}
    </>
  );
};



