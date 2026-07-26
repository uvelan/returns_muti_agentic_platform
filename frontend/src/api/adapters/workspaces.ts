import { type WorkspaceMutationPort } from "../ports/workspacesPort";
import { HttpWorkspaceAdapter } from "./httpWorkspacesAdapter";

export function createWorkspaceAdapters(): WorkspaceMutationPort {
  return new HttpWorkspaceAdapter();
}
