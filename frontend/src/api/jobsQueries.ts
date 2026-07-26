import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { Job } from "../contracts/jobs";
import { queryKeys } from "./queryKeyFactory";
import { createJobAdapters } from "./adapters/jobs";

const adapter = createJobAdapters();

export function useJobsList(options?: { type?: string; status?: string }) {
  return useQuery({
    queryKey: queryKeys.jobs.list(options?.type, options?.status),
    queryFn: ({ signal }) => adapter.listJobs({ ...options, signal }),
    select: (response) => response.data ?? [],
    refetchInterval: 2_000,
  });
}

export function useJobDetail(jobId: string) {
  return useQuery({
    queryKey: queryKeys.jobs.detail(jobId),
    queryFn: ({ signal }) => adapter.getJob(jobId, { signal }),
    enabled: Boolean(jobId),
    refetchInterval: (query) =>
      query.state.data?.status === "PENDING" || query.state.data?.status === "RUNNING" ? 1_000 : false,
  });
}

export function useSubmitImport() {
  const queryClient = useQueryClient();
  return useMutation<
    Job,
    Error,
    {
      target: string;
      format: string;
      duplicatePolicy: string;
      fieldMapping: Record<string, string>;
      content: string;
    }
  >({
    mutationFn: (payload) => adapter.submitImport(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
    },
  });
}

export function useSubmitExport() {
  const queryClient = useQueryClient();
  return useMutation<Job, Error, { source: string; format: string; fields: string[] }>({
    mutationFn: (payload) => adapter.submitExport(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
    },
  });
}

export function useCancelJob(jobId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => adapter.cancelJob(jobId),
    onSuccess: (job) => {
      queryClient.setQueryData(queryKeys.jobs.detail(jobId), job);
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
    },
  });
}

export function useRetryJob(jobId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => adapter.retryJob(jobId),
    onSuccess: (job) => {
      queryClient.setQueryData(queryKeys.jobs.detail(jobId), job);
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
    },
  });
}
