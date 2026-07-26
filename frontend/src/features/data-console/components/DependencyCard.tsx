import {
  Activity,
  Clock,
} from "lucide-react";

import { StatusBadge } from "../../../components/StatusBadge";
import type { DependencyProbeResult } from "../../../contracts/api";


type DependencyCardProps = {
  readonly name: string;
  readonly result?: DependencyProbeResult;
};


function formatCheckedTime(
  checkedAt: string,
): string {
  const checkedDate = new Date(checkedAt);

  if (Number.isNaN(checkedDate.getTime())) {
    return "Unknown";
  }

  return checkedDate.toLocaleTimeString(
    undefined,
    {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    },
  );
}


function DependencyCardSkeleton({
  name,
}: {
  readonly name: string;
}) {
  return (
    <article
      className="
        flex min-h-40 flex-col justify-between
        rounded-xl border border-slate-200
        bg-white p-5 shadow-sm
      "
      aria-busy="true"
      aria-label={`Loading ${name} status`}
    >
      <span className="sr-only">
        Loading {name} dependency status.
      </span>

      <div
        className="animate-pulse motion-reduce:animate-none"
        aria-hidden="true"
      >
        <div className="flex items-center justify-between gap-4">
          <div className="h-5 w-24 rounded bg-slate-200" />
          <div className="h-6 w-20 rounded-md bg-slate-200" />
        </div>

        <div className="mt-8 space-y-3">
          <div className="h-4 w-1/2 rounded bg-slate-100" />
          <div className="h-4 w-1/3 rounded bg-slate-100" />
        </div>
      </div>
    </article>
  );
}


export function DependencyCard({
  name,
  result,
}: DependencyCardProps) {
  if (result === undefined) {
    return (
      <DependencyCardSkeleton name={name} />
    );
  }

  const isHealthy =
    result.status === "HEALTHY";

  const formattedTime = formatCheckedTime(
    result.checked_at,
  );

  return (
    <article
      className="
        flex min-h-40 flex-col justify-between
        rounded-xl border border-slate-200
        bg-white p-5 shadow-sm
        transition-shadow hover:shadow-md
      "
      aria-labelledby={`dependency-${name}`}
    >
      <div className="flex items-start justify-between gap-4">
        <h3
          id={`dependency-${name}`}
          className="text-base font-semibold text-slate-900"
        >
          {name}
        </h3>

        <StatusBadge status={result.status} />
      </div>

      <div className="mt-4 flex flex-1 flex-col gap-3">
        {!isHealthy && result.safe_message !== null ? (
          <div
            className="
              rounded-md bg-slate-50 p-3
              text-sm leading-5 text-slate-700
              ring-1 ring-inset ring-slate-200
            "
          >
            <span className="font-medium text-slate-900">
              Details:
            </span>{" "}
            {result.safe_message}
          </div>
        ) : null}

        {!isHealthy && result.error_code !== null ? (
          <p className="text-xs text-slate-500">
            Error code:{" "}
            <span className="font-mono text-slate-700">
              {result.error_code}
            </span>
          </p>
        ) : null}

        <div
          className="
            mt-auto flex flex-wrap items-center
            gap-x-4 gap-y-2 pt-2 text-xs text-slate-500
          "
        >
          <div
            className="flex items-center gap-1.5"
            title="Probe latency"
          >
            <Activity
              size={14}
              aria-hidden="true"
            />

            <span>
              {result.latency_ms === null
                ? "Latency unavailable"
                : `${String(result.latency_ms)} ms`}
            </span>
          </div>

          <div
            className="flex items-center gap-1.5"
            title="Last checked"
          >
            <Clock
              size={14}
              aria-hidden="true"
            />

            <time dateTime={result.checked_at}>
              {formattedTime}
            </time>
          </div>
        </div>
      </div>
    </article>
  );
}
