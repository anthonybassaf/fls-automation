import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../../components/ui/button";
import { RefreshCcw } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";

// const BACKEND_URL = "http://172.31.3.48:8000";
const BACKEND_URL = `http://${window.location.hostname}:8000`;

type AnyJson = Record<string, unknown>;
type Project = { id: string; name: string };
type Model = { id: string; name: string };

export const SpeckleViewer = () => {
  const navigate = useNavigate();

  // persisted viewer URL
  const [speckleUrl, setSpeckleUrl] = useState<string | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);

  // new: projects/models state
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [selectedModelId, setSelectedModelId] = useState<string>("");

  // Load saved URL on page refresh
  useEffect(() => {
    const justLoggedIn = sessionStorage.getItem("vf_just_logged_in") === "1";
    if (justLoggedIn) {
      // show dropdown immediately after auth
      sessionStorage.removeItem("vf_just_logged_in");
      setSpeckleUrl(null);
      return;
    }

    const savedUrl = localStorage.getItem("speckleUrl");
    if (savedUrl) setSpeckleUrl(savedUrl);
  }, []);


  // When we have no model loaded, fetch projects for the user
  useEffect(() => {
    if (speckleUrl) return;
    void fetchProjects();
  }, [speckleUrl]);

  const getUserId = () =>
    sessionStorage.getItem("user_id") ||
    localStorage.getItem("speckle_user_id") ||
    localStorage.getItem("user_id") ||
    "";

  // Speckle server origin: let backend set it in storage if you already do so; else default to your host.
  const getSpeckleOrigin = () =>
    sessionStorage.getItem("speckle_server") ||
    localStorage.getItem("speckle_server") ||
    "https://speckle.dar.com";

  async function fetchProjects() {
    const userId = getUserId();
    if (!userId) {
      navigate("/login");
      return;
    }
    try {
      setLoadingProjects(true);
      // Backend should return: [{ id, name }, ...] projects the user can access
      const res = await fetch(
        `${BACKEND_URL}/speckle/projects?user_id=${encodeURIComponent(userId)}`
      );
      if (!res.ok) throw new Error("Failed to load projects");
      const data: AnyJson = await res.json();
      const items = (data as any).projects ?? data; // support either shape
      setProjects(items as Project[]);
    } catch (e) {
      console.error(e);
      setProjects([]);
    } finally {
      setLoadingProjects(false);
    }
  }

  async function fetchModels(projectId: string) {
    const userId = getUserId();
    if (!userId) {
      navigate("/login");
      return;
    }
    setSelectedModelId("");
    setModels([]);
    if (!projectId) return;

    try {
      setLoadingModels(true);
      // Backend should return: [{ id, name }, ...] models within this project for the user
      const res = await fetch(
        `${BACKEND_URL}/speckle/projects/${encodeURIComponent(
          projectId
        )}/models?user_id=${encodeURIComponent(userId)}`
      );
      if (!res.ok) throw new Error("Failed to load models");
      const data: AnyJson = await res.json();
      const items = (data as any).models ?? data; // support either shape
      setModels(items as Model[]);
    } catch (e) {
      console.error(e);
      setModels([]);
    } finally {
      setLoadingModels(false);
    }
  }

  async function handleConfirmSelection() {
    try {
      const userId = getUserId();
      if (!userId) {
        navigate("/login");
        return;
      }
      if (!selectedProjectId || !selectedModelId) {
        alert("Please select both a project and a model.");
        return;
      }

      const origin = getSpeckleOrigin();
      const trimmed = `${origin}/projects/${selectedProjectId}/models/${selectedModelId}`;
      const embed = `${trimmed}#embed=%7B%22isEnabled%22%3Atrue%7D`;

      // Optional: backend access check (same as your previous flow)
      const accessRes = await fetch(
        `${BACKEND_URL}/auth/check-access?user_id=${encodeURIComponent(
          userId
        )}&speckle_url=${encodeURIComponent(trimmed)}`
      );
      const accessData = await accessRes.json();
      if (!accessData.access) {
        setAccessDenied(true);
        return;
      }

      // Inform backend which project/model we’re working on
      await fetch(`${BACKEND_URL}/set-project`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: selectedProjectId, model_id: selectedModelId }),
      });

      // persist and load
      setSpeckleUrl(embed);
      localStorage.setItem("speckleUrl", embed);
      sessionStorage.setItem("project_id", selectedProjectId);
      sessionStorage.setItem("model_id", selectedModelId);
    } catch (err) {
      console.error(err);
      alert("Could not load the selected model.");
    }
  }

  const handleChangeModel = () => {
    localStorage.removeItem("speckleUrl");
    setSpeckleUrl(null);
    // reset form state for a fresh selection
    setSelectedProjectId("");
    setSelectedModelId("");
    setModels([]);
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
            <h2 className="text-lg font-semibold">Choose a Speckle Model</h2>
            <p className="text-sm text-gray-600">
              Select a project, then a model you want to work on.
            </p>

            {/* Project dropdown */}
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-600">Project</div>
              <Select
                value={selectedProjectId}
                onValueChange={(v) => {
                  setSelectedProjectId(v);
                  void fetchModels(v);
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue
                    placeholder={loadingProjects ? "Loading projects..." : "Select a project"}
                  />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name || p.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Model dropdown */}
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-600">Model</div>
              <Select
                value={selectedModelId}
                onValueChange={setSelectedModelId}
                disabled={!selectedProjectId || loadingModels}
              >
                <SelectTrigger className="w-full">
                  <SelectValue
                    placeholder={
                      !selectedProjectId
                        ? "Select a project first"
                        : loadingModels
                        ? "Loading models..."
                        : "Select a model"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.name || m.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Button
              onClick={handleConfirmSelection}
              className="w-full"
              disabled={!selectedProjectId || !selectedModelId}
            >
              Load Model
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 min-h-0 bg-dashboard-panel rounded-lg shadow-panel overflow-hidden relative">
          {/* Icon button for changing model */}
          <Button
            onClick={handleChangeModel}
            title="Change Model"
            className="absolute top-4 right-4 z-10 bg-white/90 border rounded-full px-3 py-1.5 shadow hover:bg-gray-100 text-sm"
            variant="secondary"
          >
            Change Model
          </Button>

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
