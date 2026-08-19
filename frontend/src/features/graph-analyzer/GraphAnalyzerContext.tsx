/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { AgentContext } from "../../contracts/graphAnalyzer";

type AnalyzerUiState = {
  readonly selectedSourceId: string | null;
  readonly selectedObjectId: string | null;
  readonly selectedObjectIds: ReadonlySet<string>;
  readonly analysisContext: string;
  readonly chatOpen: boolean;
  readonly agentContext: AgentContext;
  readonly setSelectedSourceId: (value: string | null) => void;
  readonly setSelectedObjectId: (value: string | null) => void;
  readonly setSelectedObjectIds: (value: ReadonlySet<string>) => void;
  readonly setAnalysisContext: (value: string) => void;
  readonly openChat: (context: AgentContext) => void;
  readonly closeChat: () => void;
};

const AnalyzerContext = createContext<AnalyzerUiState | null>(null);

function readStoredContext(): string {
  try {
    return sessionStorage.getItem("graph-analyzer:context") ?? "";
  } catch {
    return "";
  }
}

export function GraphAnalyzerProvider({ children }: { readonly children: ReactNode }) {
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [selectedObjectIds, setSelectedObjectIds] = useState<ReadonlySet<string>>(() => new Set());
  const [analysisContext, setContext] = useState(readStoredContext);
  const [chatOpen, setChatOpen] = useState(false);
  const [agentContext, setAgentContext] = useState<AgentContext>({ workspace: "ANALYZER" });

  const setAnalysisContext = useCallback((value: string) => {
    setContext(value);
    try {
      sessionStorage.setItem("graph-analyzer:context", value);
    } catch {
      // Context still remains available for this mounted application session.
    }
  }, []);

  const openChat = useCallback((context: AgentContext) => {
    setAgentContext(context);
    setChatOpen(true);
  }, []);

  const closeChat = useCallback(() => { setChatOpen(false); }, []);

  const value = useMemo<AnalyzerUiState>(() => ({
    selectedSourceId,
    selectedObjectId,
    selectedObjectIds,
    analysisContext,
    chatOpen,
    agentContext,
    setSelectedSourceId,
    setSelectedObjectId,
    setSelectedObjectIds,
    setAnalysisContext,
    openChat,
    closeChat,
  }), [selectedSourceId, selectedObjectId, selectedObjectIds, analysisContext, chatOpen, agentContext, setAnalysisContext, openChat, closeChat]);

  return <AnalyzerContext.Provider value={value}>{children}</AnalyzerContext.Provider>;
}

export function useGraphAnalyzer(): AnalyzerUiState {
  const value = useContext(AnalyzerContext);
  if (value === null) throw new Error("useGraphAnalyzer must be used within GraphAnalyzerProvider");
  return value;
}
