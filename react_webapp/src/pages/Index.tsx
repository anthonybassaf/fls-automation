import { useState } from "react";
import { Header } from "../components/dashboard/Header";
import { SpeckleViewer } from "../components/dashboard/SpeckleViewer";
import { AutomationPanel } from "../components/dashboard/AutomationPanel";
import { ChevronLeft, ChevronRight } from "lucide-react";

const Index = () => {
  const [isPanelOpen, setIsPanelOpen] = useState(true);

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <Header />

      <div className="flex-1 flex relative">
        {/* Speckle Viewer */}
        <SpeckleViewer />

        {/* Sliding Automation Panel */}
        <div
          className={`transition-transform duration-300 ease-in-out transform ${
            isPanelOpen ? "translate-x-0" : "translate-x-full"
          }`}
          style={{ width: "20rem" }}
        >
          <AutomationPanel />
        </div>

        {/* Toggle Button with icons */}
        <button
          onClick={() => setIsPanelOpen(!isPanelOpen)}
          className="absolute top-4 right-4 z-10 bg-white border rounded-full p-2 shadow hover:bg-gray-100"
          title={isPanelOpen ? "Hide Panel" : "Show Panel"}
        >
          {isPanelOpen ? (
            <ChevronRight className="h-5 w-5" />
          ) : (
            <ChevronLeft className="h-5 w-5" />
          )}
        </button>
      </div>
    </div>
  );
};

export default Index;
