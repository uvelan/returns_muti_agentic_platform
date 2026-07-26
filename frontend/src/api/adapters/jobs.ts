import { type JobQueryPort, type ImportJobPort, type ExportJobPort } from "../ports/jobsPort";
import { HttpJobAdapter } from "./httpJobsAdapter";

export function createJobAdapters(): JobQueryPort & ImportJobPort & ExportJobPort {
  return new HttpJobAdapter();
}
