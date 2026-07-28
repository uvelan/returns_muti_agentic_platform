import { useQuery } from "@tanstack/react-query";
import { fetchRuntimeConfig } from "../api/runtimeConfig";
import { LoadingState } from "./LoadingState";
import { AlertCircle } from "lucide-react";
import { RuntimeConfigContext } from "../hooks/useRuntimeConfig";

export function RuntimeConfigProvider({ children }: { children: React.ReactNode }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["runtime-config"],
    queryFn: fetchRuntimeConfig,
    staleTime: Infinity,
  });

  if (isLoading) {
    return <LoadingState message="Loading runtime configuration..." />;
  }

  if (error || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="max-w-md w-full bg-white p-6 rounded-lg shadow-sm border border-red-200">
          <div className="flex items-center space-x-3 text-red-600 mb-4">
            <AlertCircle className="w-6 h-6" />
            <h2 className="text-lg font-semibold">Configuration Error</h2>
          </div>
          <p className="text-slate-600 text-sm">
            Failed to load runtime configuration from the server.
          </p>
        </div>
      </div>
    );
  }

  return (
    <RuntimeConfigContext.Provider value={data}>
      {children}
    </RuntimeConfigContext.Provider>
  );
}
