import {
  AlertCircle,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";

import { APIError } from "../../../api/client";
import { useInfrastructureOverview } from "../../../api/queries";
import type { InfrastructureOverview } from "../../../contracts/api";
import { DependencyCard } from "../components/DependencyCard";


const KNOWN_DEPENDENCIES = {
  mongodb: "MongoDB",
  neo4j: "Neo4j",
  sqlserver: "SQL Server",
  temporal: "Temporal",
  valkey: "Valkey",
} as const satisfies Readonly<Record<string, string>>;


const KNOWN_DEPENDENCY_KEYS = Object.keys(
  KNOWN_DEPENDENCIES,
);


function getRenderKeys(
  overview: InfrastructureOverview | null,
  isPending: boolean,
): readonly string[] {
  if (isPending) {
    return KNOWN_DEPENDENCY_KEYS;
  }

  if (overview === null) {
    return [];
  }

  const availableKnownKeys = KNOWN_DEPENDENCY_KEYS.filter(
    (key) => key in overview,
  );

  const additionalKeys = Object.keys(overview).filter(
    (key) => !(key in KNOWN_DEPENDENCIES),
  );

  return [
    ...availableKnownKeys,
    ...additionalKeys,
  ];
}


function getDependencyLabel(
  dependencyKey: string,
): string {
  return dependencyKey in KNOWN_DEPENDENCIES
    ? KNOWN_DEPENDENCIES[
        dependencyKey as keyof typeof KNOWN_DEPENDENCIES
      ]
    : dependencyKey;
}


function getErrorMessage(
  error: unknown,
): string {
  if (error instanceof APIError) {
    return error.message;
  }

  return "The infrastructure overview could not be loaded.";
}


export function OverviewPage() {
  const {
    data,
    error,
    isError,
    isFetching,
    isPending,
    refetch,
  } = useInfrastructureOverview();

  const overview = data?.data ?? null;
  const metadata = data?.meta;

  const renderKeys = getRenderKeys(
    overview,
    isPending,
  );

  const hasUsableData = overview !== null;
  const hasBackgroundError =
    isError && hasUsableData;

  if (isError && !hasUsableData) {
    const correlationId =
      error instanceof APIError
        ? error.correlationId
        : undefined;

    return (
      <section
        className="
          flex min-h-[50vh] flex-col items-center justify-center
          gap-4 rounded-xl border border-red-200
          bg-red-50 p-6 text-center text-red-950 shadow-sm
        "
        role="alert"
        aria-labelledby="overview-error-heading"
      >
        <AlertCircle
          size={40}
          className="text-red-600"
          aria-hidden="true"
        />

        <h1
          id="overview-error-heading"
          className="text-lg font-semibold"
        >
          Failed to load infrastructure overview
        </h1>

        <p className="max-w-md text-sm leading-6 text-red-800">
          {getErrorMessage(error)}
        </p>

        {correlationId !== undefined ? (
          <p className="font-mono text-xs text-red-700">
            Trace: {correlationId}
          </p>
        ) : null}

        <button
          type="button"
          onClick={() => {
            void refetch();
          }}
          className="
            mt-2 inline-flex items-center gap-2 rounded-md
            bg-white px-4 py-2 text-sm font-medium text-red-700
            shadow-sm ring-1 ring-inset ring-red-300
            transition hover:bg-red-100
            focus-visible:outline-none focus-visible:ring-2
            focus-visible:ring-red-500 focus-visible:ring-offset-2
          "
        >
          <RefreshCw
            size={16}
            aria-hidden="true"
          />
          Retry connection
        </button>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <header
        className="
          flex flex-col gap-4
          sm:flex-row sm:items-start sm:justify-between
        "
      >
        <div>
          <h1
            className="
              text-2xl font-bold tracking-tight text-slate-900
            "
          >
            Infrastructure Overview
          </h1>

          <p className="mt-1 text-sm text-slate-500">
            Live health and latency metrics for core platform
            dependencies.
          </p>
        </div>

        <div
          className="
            flex flex-col gap-1 text-xs text-slate-500
            sm:items-end
          "
        >
          <div className="flex items-center gap-2">
            {isFetching ? (
              <span
                className="inline-flex items-center gap-1.5"
                role="status"
              >
                <RefreshCw
                  size={14}
                  className="
                    animate-spin text-slate-400
                    motion-reduce:animate-none
                  "
                  aria-hidden="true"
                />
                <span className="sr-only">
                  Refreshing infrastructure data
                </span>
              </span>
            ) : null}

            {metadata !== undefined ? (
              <span>
                Freshness:{" "}
                <span className="font-medium text-slate-700">
                  {metadata.freshness}
                </span>
              </span>
            ) : null}
          </div>

          {metadata !== undefined ? (
            <>
              <time
                dateTime={metadata.generated_at}
                className="text-slate-400"
              >
                Generated{" "}
                {new Date(
                  metadata.generated_at,
                ).toLocaleTimeString()}
              </time>

              <span className="font-mono text-[10px] text-slate-400">
                Trace: {metadata.request_id}
              </span>
            </>
          ) : null}
        </div>
      </header>

      {hasBackgroundError ? (
        <div
          className="
            flex items-start gap-3 rounded-lg border
            border-amber-200 bg-amber-50 p-4
            text-sm text-amber-900
          "
          role="status"
        >
          <TriangleAlert
            size={18}
            className="mt-0.5 shrink-0"
            aria-hidden="true"
          />

          <div className="flex-1">
            <p className="font-medium">
              Latest refresh failed
            </p>

            <p className="mt-1 text-amber-800">
              Previously loaded infrastructure data remains
              visible.
            </p>
          </div>

          <button
            type="button"
            onClick={() => {
              void refetch();
            }}
            className="
              shrink-0 rounded-md px-3 py-1.5
              text-xs font-medium text-amber-900
              ring-1 ring-inset ring-amber-300
              hover:bg-amber-100
              focus-visible:outline-none focus-visible:ring-2
              focus-visible:ring-amber-500
            "
          >
            Retry
          </button>
        </div>
      ) : null}

      {metadata?.partial === true ? (
        <div
          className="
            rounded-lg border border-amber-200
            bg-amber-50 p-4 text-sm text-amber-900
          "
          role="status"
        >
          <p className="font-medium">
            Partial infrastructure response
          </p>

          <p className="mt-1 text-amber-800">
            {metadata.warnings.length} dependency warning
            {metadata.warnings.length === 1 ? "" : "s"} reported.
          </p>
        </div>
      ) : null}

      {renderKeys.length > 0 ? (
        <div
          className="
            grid grid-cols-1 gap-4
            sm:grid-cols-2 lg:grid-cols-3
          "
          role="list"
          aria-label="Infrastructure services"
        >
          {renderKeys.map((dependencyKey) => (
            <div
              key={dependencyKey}
              role="listitem"
            >
              <DependencyCard
                name={getDependencyLabel(dependencyKey)}
                result={
                  overview?.[dependencyKey]
                }
              />
            </div>
          ))}
        </div>
      ) : (
        <div
          className="
            rounded-xl border border-dashed border-slate-300
            bg-white p-10 text-center
          "
        >
          <p className="text-sm font-medium text-slate-700">
            No infrastructure services were returned.
          </p>

          <p className="mt-1 text-sm text-slate-500">
            Refresh the page or inspect the backend response.
          </p>
        </div>
      )}
    </div>
  );
}