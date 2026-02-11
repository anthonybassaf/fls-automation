import React, { useEffect, useMemo, useState } from "react";
import { Button } from "../../components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { Label } from "../../components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { PlayCircle, Upload, Settings, Download, Loader2, LogIn } from "lucide-react";
import { useToast } from "../../hooks/use-toast";

const BACKEND_URL = `http://${window.location.hostname}:8000`;

// --- Speckle auth settings ---
const SPECKLE_SERVER = "https://speckle.dar.com";
const CLIENT_ID = "d857abe00b";
const REDIRECT_URI = `${window.location.origin}/login`;

type AnyJson = Record<string, unknown>;

export const AutomationPanel: React.FC = () => {
  const { toast } = useToast();

  // --- auth / headers ---
  const proj =
    (typeof window !== "undefined" ? window.sessionStorage.getItem("project_id") : "") || "";
  const model =
    ((typeof window !== "undefined" ? window.sessionStorage.getItem("model_id") : "") || "").split("@", 1)[0];

  const [userId, setUserId] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return sessionStorage.getItem("user_id") || "";
  });
  const isAuthed = !!userId;

  useEffect(() => {
    const readAndValidate = async () => {
      const uid = sessionStorage.getItem("user_id") || "";
      if (!uid) {
        setUserId("");
        return;
      }
      try {
        const r = await fetch(`${BACKEND_URL}/auth/whoami`, {
          headers: { "X-User-Id": uid },
        });
        if (r.ok) {
          setUserId(uid);
        } else if (r.status === 404) {
          sessionStorage.removeItem("user_id");
          setUserId("");
        } else {
          setUserId(uid);
        }
      } catch {
        setUserId(uid);
      }
    };

    readAndValidate();
    const onAuth = () => readAndValidate();
    const onFocus = () => readAndValidate();
    const onVis = () => document.visibilityState === "visible" && readAndValidate();
    const onStorage = (e: StorageEvent) => {
      if (e.key === "SPECKLE_USER_ID") readAndValidate();
    };

    window.addEventListener("speckle-auth", onAuth);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("speckle-auth", onAuth);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const baseHeaders = useMemo(() => {
    const h: Record<string, string> = {};
    if (proj) h["X-Project-Id"] = proj;
    if (model) h["X-Model-Id"] = model;
    if (userId) h["X-User-Id"] = userId;
    return h;
  }, [proj, model, userId]);

  // --- fetch helpers ---
  const getJson = async <T = AnyJson>(path: string, init: RequestInit = {}): Promise<T> => {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      method: init.method ?? "GET",
      headers: { ...(baseHeaders ?? {}), ...(init.headers as Record<string, string> ?? {}) },
      ...init,
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}\n${text}`);
    try {
      return JSON.parse(text) as T;
    } catch {
      return { statusText: text } as T;
    }
  };

  const postJson = async <T = AnyJson>(path: string, body: unknown): Promise<T> => {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...baseHeaders },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    if (!res.ok) {
      let detail = text;
      try {
        detail = JSON.stringify(JSON.parse(text), null, 2);
      } catch {}
      throw new Error(`HTTP ${res.status} ${res.statusText}\n${detail}`);
    }
    try {
      return JSON.parse(text) as T;
    } catch {
      return { statusText: text } as T;
    }
  };

  const postForm = async <T = AnyJson>(path: string, formData: FormData): Promise<T> => {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      method: "POST",
      headers: { ...baseHeaders },
      body: formData,
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}\n${text}`);
    try {
      return JSON.parse(text) as T;
    } catch {
      return { statusText: text } as T;
    }
  };

  // --- UI state ---
  const [isRunningPipeline, setIsRunningPipeline] = useState(false);
  const [currentStep, setCurrentStep] = useState<string>("");

  const [pdfOptions, setPdfOptions] = useState<string[]>([]);
  const [loadingPdfs, setLoadingPdfs] = useState(false);
  const [isUploadingPDF, setIsUploadingPDF] = useState(false);
  const [selectedCode, setSelectedCode] = useState<string>("");

  const [reportAvailable, setReportAvailable] = useState(false);
  const [reportFilename, setReportFilename] = useState<string>("");
  const [isDownloadingReport, setIsDownloadingReport] = useState(false);

  const [flsRunFinished, setFlsRunFinished] = useState(false);

  // --- Speckle Auth Handler ---
  const handleSpeckleLogin = async () => {
    try {
      // 1) Get PKCE from backend
      const r = await fetch(`${BACKEND_URL}/auth/pkce/start`);
      if (!r.ok) {
        toast({
          title: "Authentication Error",
          description: "Failed to start authentication flow",
          variant: "destructive",
        });
        return;
      }
      const { verifier, challenge } = await r.json();

      // Save for exchange step
      sessionStorage.setItem("speckle_code_verifier", verifier);
      sessionStorage.setItem("speckle_code_challenge", challenge);

      // 2) Build the Speckle verify URL
      const server = SPECKLE_SERVER.replace(/\/+$/, "");
      const state = window.location.pathname + window.location.search;

      const loginUrl =
        `${server}/authn/verify/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(challenge)}` +
        `?redirect_uri=${encodeURIComponent(REDIRECT_URI)}` +
        `&state=${encodeURIComponent(state)}`;

      // 3) Redirect to Speckle
      window.location.href = loginUrl;
    } catch (e) {
      console.error("[AutomationPanel] Failed to start PKCE flow:", e);
      toast({
        title: "Authentication Error",
        description: "Failed to connect to Speckle",
        variant: "destructive",
      });
    }
  };

  // --- PDFs list on load ---
  useEffect(() => {
    const fetchPdfs = async () => {
      setLoadingPdfs(true);
      try {
        const data = (await getJson("/fls/pdfs")) as AnyJson & { pdfs?: string[] };
        setPdfOptions(data.pdfs ?? []);
      } catch (err) {
        console.error("Failed to load PDFs", err);
      } finally {
        setLoadingPdfs(false);
      }
    };
    if (isAuthed) fetchPdfs();
  }, [isAuthed]);

  // --- Report status (optional prefetch) ---
  useEffect(() => {
    const prefetchReportFilename = async () => {
      try {
        const data = (await getJson("/fls/report/status")) as AnyJson & {
          available?: boolean;
          filename?: string | null;
        };

        if (data.available && data.filename) setReportFilename(String(data.filename));
      } catch {
        // ignore
      }
    };

    if (isAuthed && proj && model) prefetchReportFilename();
  }, [isAuthed, proj, model]);

  // --- guards ---
  const guardAuthed = (action: () => void) => {
    if (!isAuthed) {
      toast({
        title: "Not signed in",
        description: "Please log into Speckle to continue.",
        variant: "destructive",
      });
      return;
    }
    action();
  };

  // --- CONSOLIDATED PIPELINE ACTION ---
  const handleRunFullPipeline = () =>
    guardAuthed(async () => {
      if (!selectedCode) {
        toast({ title: "Error", description: "Select a building code first", variant: "destructive" });
        return;
      }

      // Reset state
      setReportAvailable(false);
      setReportFilename("");
      setFlsRunFinished(false);
      setIsRunningPipeline(true);

      try {
        // ==================== STEP 1: Generate Grid ====================
        setCurrentStep("Generating Grid...");
        toast({ 
          title: "Step 1/3: Generating Grid", 
          description: "Creating spatial grid from model..." 
        });

        try {
          const gridResult = await postJson<{ 
            status?: string;
            stdout?: string;
            stderr?: string;
            returncode?: number;
          }>("/run/grid", {});
          
          console.log("[Pipeline] Grid result:", gridResult);
          
          if (gridResult.returncode !== 0) {
            throw new Error(`Grid generation failed with code ${gridResult.returncode}`);
          }
        } catch (err: any) {
          throw new Error(`Grid generation failed: ${err?.message ?? err}`);
        }

        // ==================== STEP 2: Compute Paths ====================
        setCurrentStep("Computing Paths...");
        toast({ 
          title: "Step 2/3: Computing Paths", 
          description: "Calculating travel distances and shortest paths..." 
        });

        try {
          // Verify graphs exist BEFORE attempting path computation
          const { floors: serverFloors } = await getJson<{ floors?: string[] }>("/graph/floors", {
            headers: {
              "X-Project-Id": proj,
              "X-Model-Id": model,
            },
          });

          if (!serverFloors || serverFloors.length === 0) {
            throw new Error("No floor graphs found. Grid generation may have failed or produced no valid floors.");
          }

          console.log(`[Pipeline] Found ${serverFloors.length} floor(s):`, serverFloors);

          // Actually run the paths computation
          const pathsResult = await postJson<{ 
            status?: string; 
            stdout?: string; 
            stderr?: string;
            returncode?: number;
          }>("/run/paths", {});

          console.log("[Pipeline] Paths result:", pathsResult);

          // Check if paths actually ran successfully
          if (pathsResult.returncode !== 0 || pathsResult.stderr) {
            throw new Error(
              `Paths computation returned errors:\nSTDOUT: ${pathsResult.stdout || '(empty)'}\nSTDERR: ${pathsResult.stderr || '(empty)'}`
            );
          }

          // Verify paths produced output (not just silent success)
          if (!pathsResult.stdout || pathsResult.stdout.trim().length === 0) {
            console.warn("[Pipeline] Paths computation produced no output - may have failed silently");
            // Don't throw here, just warn - paths might have succeeded but produced no logs
          }

        } catch (err: any) {
          throw new Error(`Path computation failed: ${err?.message ?? err}`);
        }

        // ==================== STEP 3: Run FLS Check ====================
        setCurrentStep("Running FLS Check...");
        toast({ 
          title: "Step 3/3: Running FLS Check", 
          description: "Analyzing compliance and generating report..." 
        });

        try {
          const data = (await postJson<AnyJson>(
            `/run/fls?pdf_id=${encodeURIComponent(selectedCode)}`,
            {}
          )) as AnyJson & {
            report?: { available?: boolean; filename?: string | null };
          };

          const available = !!data.report?.available;
          const filename = available && data.report?.filename ? String(data.report.filename) : "";
          setReportAvailable(available);
          setReportFilename(filename);
          setFlsRunFinished(true);

          // ==================== SUCCESS ====================
          setCurrentStep("");
          toast({
            title: "✅ Pipeline Complete",
            description: available 
              ? "All steps completed successfully. Report is ready to download." 
              : "All steps completed. Report generation in progress.",
          });
        } catch (err: any) {
          throw new Error(`FLS compliance check failed: ${err?.message ?? err}`);
        }

      } catch (err: any) {
        setCurrentStep("");
        toast({ 
          title: "Pipeline Failed", 
          description: String(err?.message ?? err), 
          variant: "destructive" 
        });
      } finally {
        setIsRunningPipeline(false);
        setCurrentStep("");
      }
    });

  const uploadPdfFile = async (file: File) => {
    setIsUploadingPDF(true);
    const formData = new FormData();
    formData.append("pdf", file);
    toast({ title: "Processing PDF", description: `Uploading ${file.name}` });
    try {
      const data = (await postForm("/fls/upload", formData)) as AnyJson & { status?: string };
      toast({ title: "Upload Complete", description: data.status ?? "Uploaded" });
      const refreshed = (await getJson("/fls/pdfs")) as AnyJson & { pdfs?: string[] };
      setPdfOptions(refreshed.pdfs ?? []);
    } catch (err) {
      toast({ title: "Error", description: String(err), variant: "destructive" });
    } finally {
      setIsUploadingPDF(false);
    }
  };

  const handleUploadPDF = () =>
    guardAuthed(() => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".pdf";
      input.onchange = async (e: Event) => {
        const target = e.target as HTMLInputElement | null;
        const file = target?.files?.[0];
        if (file) await uploadPdfFile(file);
      };
      input.click();
    });

  const handleDownloadReport = () =>
    guardAuthed(async () => {
      setIsDownloadingReport(true);
      try {
        const res = await fetch(`${BACKEND_URL}/fls/report/download`, {
          method: "GET",
          headers: { ...baseHeaders },
        });

        if (!res.ok) {
          const t = await res.text();
          throw new Error(`HTTP ${res.status} ${res.statusText}\n${t}`);
        }

        const blob = await res.blob();
        const cd = res.headers.get("content-disposition") || "";
        const m = /filename="?([^;"]+)"?/i.exec(cd);
        const filename = reportFilename || (m && m[1] ? m[1] : "fls_compliance_report.pdf");

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);

        toast({ title: "Download Started", description: filename });
      } catch (err: any) {
        toast({ title: "Download failed", description: String(err?.message ?? err), variant: "destructive" });
      } finally {
        setIsDownloadingReport(false);
      }
    });

  return (
    <div className="w-80 bg-dashboard-sidebar border-l border-border p-6 space-y-6 overflow-y-auto">
      {!isAuthed && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Sign in required</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Please authenticate with Speckle to use FLS automation features.
            </p>
            <Button 
              onClick={handleSpeckleLogin}
              className="w-full"
              variant="default"
            >
              <LogIn className="h-4 w-4" />
              Authenticate with Speckle
            </Button>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Settings className="h-5 w-5" />
            Fire & Life Safety Check
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="code-select">Building Code</Label>
            <Select value={selectedCode} onValueChange={(v) => setSelectedCode(v)}>
              <SelectTrigger id="code-select">
                <SelectValue placeholder={loadingPdfs ? "Loading PDFs..." : "Select building code..."} />
              </SelectTrigger>
              <SelectContent>
                {loadingPdfs ? (
                  <SelectItem value="loading" disabled>
                    Loading...
                  </SelectItem>
                ) : pdfOptions.length > 0 ? (
                  pdfOptions.map((pdf) => (
                    <SelectItem key={pdf} value={pdf}>
                      {pdf}
                    </SelectItem>
                  ))
                ) : (
                  <SelectItem value="none" disabled>
                    No PDFs available
                  </SelectItem>
                )}
              </SelectContent>
            </Select>
          </div>

          {/* Main Action Button */}
          <Button
            onClick={handleRunFullPipeline}
            disabled={isRunningPipeline || !selectedCode || !isAuthed}
            className="w-full"
            variant="default"
          >
            {isRunningPipeline ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {currentStep || "Running VeriFire..."}
              </>
            ) : (
              <>
                <PlayCircle className="h-4 w-4" />
                Run FLS Check
              </>
            )}
          </Button>

          {/* Progress Indicator */}
          {isRunningPipeline && currentStep && (
            <div className="text-xs text-muted-foreground text-center animate-pulse">
              {currentStep}
            </div>
          )}

          {/* Download Report Button */}
          {flsRunFinished && reportAvailable && (
            <Button
              onClick={handleDownloadReport}
              disabled={isDownloadingReport || !isAuthed}
              className="w-full"
              variant="secondary"
            >
              <Download className="h-4 w-4" />
              {isDownloadingReport ? "Downloading..." : "Download Report"}
            </Button>
          )}

          {/* Pipeline Steps Info */}
          <div className="text-xs text-muted-foreground space-y-1 pt-2 border-t">
            <p className="font-medium">VeriFire will:</p>
            <ol className="list-decimal list-inside space-y-0.5 pl-2">
              <li>Analyze building layout and egress paths</li>
              <li>Calculate travel distances to exits</li>
              <li>Run compliance check</li>
            </ol>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};