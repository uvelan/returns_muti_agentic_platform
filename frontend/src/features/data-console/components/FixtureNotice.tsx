export function FixtureNotice() {
  const fixtureMode = import.meta.env.MODE === "mock" || import.meta.env.VITE_MOCK_MODE === "true";
  if (!fixtureMode) return null;
  return (
    <div className="mb-4 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" role="status">
      <span className="font-bold">FIXTURE — NON-DURABLE:</span> This development-only mode uses deterministic network fixtures.
    </div>
  );
}
