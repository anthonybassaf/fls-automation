import { useEffect, useState } from "react";
import { Button } from "../../components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { 
  Grid3x3, 
  Route, 
  PlayCircle, 
  Upload, 
  FileText,
  Settings
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../../components/ui/dialog";
import { useToast } from "../../hooks/use-toast";
import { apiGet, apiPost } from "../../api";

export const AutomationPanel = () => {
  const { toast } = useToast();
  const [selectedCode, setSelectedCode] = useState("");
  const [isGeneratingGrid, setIsGeneratingGrid] = useState(false);
  const [isComputingPaths, setIsComputingPaths] = useState(false);
  const [isRunningCheck, setIsRunningCheck] = useState(false);

  const [isComputePathsDialogOpen, setComputePathsDialogOpen] = useState(false);
  const [floors, setFloors] = useState<string[]>([]);
  const [doorInputs, setDoorInputs] = useState<Record<string, string>>({});
  const [stairInputs, setStairInputs] = useState<Record<string, string>>({}); 
  const [currentFloorIndex, setCurrentFloorIndex] = useState(0);

  const [isUploadingPDF, setIsUploadingPDF] = useState(false);

  const uploadPdfFile = async (file: File) => {
    setIsUploadingPDF(true);
    const formData = new FormData();
    formData.append("pdf", file);
    toast({ title: "Processing PDF", description: `Uploading ${file.name}` });

    try {
      const data = await apiPost("/fls/upload", formData);
      toast({ title: "Upload Complete", description: data.status });
      const refreshed = await apiGet("/fls/pdfs");
      setPdfOptions(refreshed.pdfs || []);
    } catch (err) {
      toast({
        title: "Error",
        description: String(err),
        variant: "destructive",
      });
    } finally {
      setIsUploadingPDF(false);
      setTimeout(() => setIsUploadingPDF(false), 0);
    }
  };



  const goToNextFloor = () => {
    if (currentFloorIndex < floors.length - 1) {
      setCurrentFloorIndex(currentFloorIndex + 1);
    } else {
      handleSubmitPaths(); // Final submission on last floor
    }
  };

  const goToPreviousFloor = () => {
    if (currentFloorIndex > 0) {
      setCurrentFloorIndex(currentFloorIndex - 1);
    }
  };

  const [pdfOptions, setPdfOptions] = useState<string[]>([]);
  const [loadingPdfs, setLoadingPdfs] = useState(false);

  useEffect(() => {
    const fetchPdfs = async () => {
      setLoadingPdfs(true);
      try {
        const data = await apiGet("/fls/pdfs");
        setPdfOptions(data.pdfs || []);
      } catch (err) {
        console.error("Failed to load PDFs", err);
      } finally {
        setLoadingPdfs(false);
      }
    };

    fetchPdfs();
  }, []);

  const handleGenerateGrid = async () => {
    setIsGeneratingGrid(true);
    toast({ title: "Generating Grid", description: "Running grid script..." });
    try {
      const data = await apiPost("/run/grid");
      toast({ title: "Grid Generated", description: data.status });
    } catch (err) {
      toast({ title: "Error", description: String(err), variant: "destructive" });
    }
    setIsGeneratingGrid(false);
  };

  const handleOpenComputePaths = async () => {
    setIsComputingPaths(true);
    try {
      const res = await fetch("http://localhost:8000/graph/floors");
      const { floors } = await res.json();
      setIsComputingPaths(false);

      if (!floors || floors.length === 0) {
        toast({
          title: "No Graphs",
          description: "No floor graphs found to compute paths.",
          variant: "destructive",
        });
        return;
      }

      setFloors(floors);
      setDoorInputs({});
      setStairInputs({});
      setComputePathsDialogOpen(true);
    } catch (err) {
      setIsComputingPaths(false);
      toast({
        title: "Error",
        description: String(err),
        variant: "destructive",
      });
    }
  };

  const handleSubmitPaths = async () => {
    setComputePathsDialogOpen(false);
    setIsComputingPaths(true);

    try {
      const userInputs: Record<string, string[]> = {};
      floors.forEach((floor) => {
        const doors = (doorInputs[floor] || "").split(",").map((s) => s.trim());
        const stairs = (stairInputs[floor] || "").split(",").map((s) => s.trim());
        const allIds = [...doors, ...stairs].filter(Boolean);
        userInputs[floor] = allIds;
      });

      await fetch("http://localhost:8000/save-user-inputs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(userInputs),
      });

      const runRes = await fetch("http://localhost:8000/run/paths", { method: "POST" });
      const data = await runRes.json();

      toast({
        title: "Paths Computed",
        description: data.status || "Shortest paths generated successfully",
      });
    } catch (err) {
      toast({
        title: "Error",
        description: String(err),
        variant: "destructive",
      });
    }

    setIsComputingPaths(false);
  };

  const handleRunFLSCheck = async () => {
    if (!selectedCode) {
      toast({ title: "Error", description: "Select a code first", variant: "destructive" });
      return;
    }
    setIsRunningCheck(true);
    toast({ title: "FLS Check Started", description: "Running compliance check..." });
    try {
      const data = await apiPost(`/run/fls?pdf_id=${selectedCode}`);
      toast({ title: "FLS Check Complete", description: data.status });
    } catch (err) {
      toast({ title: "Error", description: String(err), variant: "destructive" });
    }
    setIsRunningCheck(false);
  };

  const handleUploadPDF = async () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (file) {
        const formData = new FormData();
        formData.append("pdf", file);
        toast({ title: "Processing PDF", description: `Uploading ${file.name}` });
        try {
          const data = await apiPost("/fls/upload", formData);
          toast({ title: "Upload Complete", description: data.status });
        } catch (err) {
          toast({ title: "Error", description: String(err), variant: "destructive" });
        }
      }
    };
    input.click();
  };

  return (
    <div className="w-80 bg-dashboard-sidebar border-l border-border p-6 space-y-6 overflow-y-auto">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Settings className="h-5 w-5" />
            FLS Automation
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3">
            <Button
              onClick={handleGenerateGrid}
              disabled={isGeneratingGrid}
              className="w-full"
              variant="default"
            >
              <Grid3x3 className="h-4 w-4" />
              {isGeneratingGrid ? "Generating..." : "Generate Grid"}
            </Button>

            <Button
              onClick={handleOpenComputePaths}
              disabled={isComputingPaths}
              className="w-full"
              variant="secondary"
            >
              <Route className="h-4 w-4" />
              {isComputingPaths ? "Computing..." : "Compute Paths"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Select Code PDF</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="code-select">Rules and Regulations</Label>
            <Select value={selectedCode} onValueChange={setSelectedCode}>
              <SelectTrigger>
                <SelectValue placeholder={loadingPdfs ? "Loading PDFs..." : "Select building code..."} />
              </SelectTrigger>
              <SelectContent>
                {loadingPdfs ? (
                  <SelectItem value="loading" disabled>Loading...</SelectItem>
                ) : pdfOptions.length > 0 ? (
                  pdfOptions.map((pdf) => (
                    <SelectItem key={pdf} value={pdf}>
                      {pdf}
                    </SelectItem>
                  ))
                ) : (
                  <SelectItem value="none" disabled>No PDFs available</SelectItem>
                )}
              </SelectContent>
            </Select>
          </div>
              
          {/* Drag-and-drop Upload placed here */}
          <div
            onDrop={async (e) => {
              e.preventDefault();
              const file = e.dataTransfer.files[0];
              if (file && file.type === "application/pdf") {
                await uploadPdfFile(file);
              } else {
                toast({
                  title: "Invalid File",
                  description: "Please upload a PDF file.",
                  variant: "destructive",
                });
              }
            }}
            onDragOver={(e) => e.preventDefault()}
            className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center cursor-pointer hover:border-gray-400 transition"
            onClick={async () => {
              const input = document.createElement("input");
              input.type = "file";
              input.accept = ".pdf";
              input.onchange = async (e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (file) await uploadPdfFile(file);
              };
              input.click();
            }}
          >
            {isUploadingPDF ? (
              <div className="flex flex-col items-center gap-2">
                <div className="w-6 h-6 border-2 border-gray-300 border-t-primary rounded-full animate-spin"></div>
                <p className="text-sm text-gray-600">Uploading & embedding PDF...</p>
              </div>
            ) : (
              <>
                <Upload className="mx-auto h-6 w-6 text-gray-500" />
                <p className="mt-2 text-sm font-medium text-gray-700">
                  Upload files <span className="text-gray-500">or drag and drop</span>
                </p>
                <p className="mt-1 text-xs text-gray-400">.pdf files only</p>
              </>
            )}
          </div>
          
          <Button
            onClick={handleRunFLSCheck}
            disabled={isRunningCheck || !selectedCode}
            className="w-full"
            variant="default"
          >
            <PlayCircle className="h-4 w-4" />
            {isRunningCheck ? "Running..." : "Run FLS Check"}
          </Button>
        </CardContent>
      </Card>

      <Dialog open={isComputePathsDialogOpen} onOpenChange={setComputePathsDialogOpen}>
        <DialogContent className="max-w-lg w-full">
          <DialogHeader>
            <DialogTitle>
              Compute Paths – Floor {floors[currentFloorIndex] || ""}
            </DialogTitle>
            <p className="text-sm text-muted-foreground">
              Step {currentFloorIndex + 1} of {floors.length}: Enter Door and Stair IDs for this floor.
            </p>
          </DialogHeader>

          {floors.length > 0 && (
            <div className="space-y-4">
              <Input
                placeholder="Door IDs (comma-separated)"
                value={doorInputs[floors[currentFloorIndex]] || ""}
                onChange={(e) =>
                  setDoorInputs((prev) => ({
                    ...prev,
                    [floors[currentFloorIndex]]: e.target.value,
                  }))
                }
              />
              <Input
                placeholder="Stair IDs (comma-separated)"
                value={stairInputs[floors[currentFloorIndex]] || ""}
                onChange={(e) =>
                  setStairInputs((prev) => ({
                    ...prev,
                    [floors[currentFloorIndex]]: e.target.value,
                  }))
                }
              />
            </div>
          )}

          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => setComputePathsDialogOpen(false)}
            >
              Cancel
            </Button>
        
            {currentFloorIndex > 0 && (
              <Button variant="outline" onClick={goToPreviousFloor}>
                Previous
              </Button>
            )}

            <Button
              onClick={goToNextFloor}
              disabled={
                !(
                  doorInputs[floors[currentFloorIndex]]?.trim() ||
                  stairInputs[floors[currentFloorIndex]]?.trim()
                )
              }
            >
              {currentFloorIndex === floors.length - 1 ? "Finish & Run Paths" : "Next"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  );
};