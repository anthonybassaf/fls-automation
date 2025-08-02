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