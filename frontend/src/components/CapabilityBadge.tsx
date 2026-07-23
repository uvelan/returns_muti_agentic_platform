type CapabilityBadgeProps = {
  capability: "LIVE" | "FIXTURE" | "BLOCKED";
};

export function CapabilityBadge({ capability }: CapabilityBadgeProps) {
  if (capability === "LIVE") {
    return (
      <span className="inline-flex items-center rounded-md bg-green-50 px-2 py-1 text-xs font-medium text-green-700 ring-1 ring-inset ring-green-600/20">
        Live
      </span>
    );
  }
  if (capability === "FIXTURE") {
    return (
      <span className="inline-flex items-center rounded-md bg-yellow-50 px-2 py-1 text-xs font-medium text-yellow-800 ring-1 ring-inset ring-yellow-600/20">
        Fixture
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-md bg-slate-50 px-2 py-1 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-500/10">
      Blocked
    </span>
  );
}
