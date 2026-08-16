import type { ReactNode } from "react";
import { COPILOT_TOKENS } from "../copilotTokens";

export type ReturnCopilotShellProps = {
  conversationPane: ReactNode;
  progressTruthPane: ReactNode;
  businessObjectPane: ReactNode;
};

/**
 * ReturnCopilotShell is the single authoritative layout owner of the 3-column
 * 40fr / 24fr / 36fr desktop workspace grid across all 8 lifecycle modes.
 */
export function ReturnCopilotShell({
  conversationPane,
  progressTruthPane,
  businessObjectPane,
}: ReturnCopilotShellProps) {
  return (
    <div className={COPILOT_TOKENS.layout.shell}>
      {conversationPane}
      {progressTruthPane}
      {businessObjectPane}
    </div>
  );
}
