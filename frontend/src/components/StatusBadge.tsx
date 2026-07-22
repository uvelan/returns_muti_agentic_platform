import {
  AlertCircle,
  CheckCircle2,
  HelpCircle,
  Loader2,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import type { DependencyStatus } from "../contracts/api";


type StatusBadgeProps = {
  readonly status: DependencyStatus;
};


type StatusConfiguration = {
  readonly label: string;
  readonly className: string;
  readonly icon: LucideIcon;
};


const statusConfig = {
  HEALTHY: {
    label: "Healthy",
    className:
      "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    icon: CheckCircle2,
  },
  DEGRADED: {
    label: "Degraded",
    className:
      "bg-amber-50 text-amber-700 ring-amber-600/20",
    icon: AlertCircle,
  },
  UNAVAILABLE: {
    label: "Unavailable",
    className:
      "bg-red-50 text-red-700 ring-red-600/20",
    icon: XCircle,
  },
  STARTING: {
    label: "Starting",
    className:
      "bg-blue-50 text-blue-700 ring-blue-600/20",
    icon: Loader2,
  },
  UNKNOWN: {
    label: "Unknown",
    className:
      "bg-slate-100 text-slate-700 ring-slate-600/20",
    icon: HelpCircle,
  },
} satisfies Readonly<
  Record<DependencyStatus, StatusConfiguration>
>;


export function StatusBadge({
  status,
}: StatusBadgeProps) {
  const configuration = statusConfig[status];
  const Icon = configuration.icon;

  return (
    <span
      className={`
        inline-flex whitespace-nowrap items-center gap-1.5
        rounded-md px-2 py-1 text-xs font-medium
        ring-1 ring-inset
        ${configuration.className}
      `}
    >
      <Icon
        size={14}
        className={
          status === "STARTING"
            ? "animate-spin motion-reduce:animate-none"
            : undefined
        }
        aria-hidden="true"
      />

      {configuration.label}
    </span>
  );
}