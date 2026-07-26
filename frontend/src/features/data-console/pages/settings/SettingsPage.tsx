import { useConsoleSettings } from "../../../../api/consoleGovernanceQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";
import { PropertyList } from "../../components/PropertyList";

export function SettingsPage() {
  const { data, isLoading, isError, error } = useConsoleSettings();
  if (isLoading) return <LoadingState message="Loading runtime settings..." />;
  if (isError || !data) return <ErrorState title="Settings unavailable" message={error instanceof Error ? error.message : "No settings returned"} />;

  return (
    <div className="max-w-4xl p-6">
      <PageHeader title="Console Settings" description="Read-only safe projection of active backend configuration." />
      <div className="rounded border border-gray-200 bg-white p-6 shadow-sm">
        <PropertyList properties={[
          { label: "Environment", value: data.environment },
          { label: "Strict mode", value: data.strictMode ? "Enabled" : "Disabled" },
          { label: "Audit retention", value: `${String(data.retentionDays)} days` },
          { label: "Event retention", value: data.eventStreamRetention },
          { label: "AI providers", value: data.aiProviderOrder.join(" → ") || "None configured" },
          { label: "AI interception", value: data.aiInterceptionEnabled ? "Enabled" : "Disabled" },
          { label: "Seed version", value: data.seedVersion },
        ]} />
      </div>
      <p className="mt-4 text-sm text-gray-500">Secrets, DSNs, credentials, and provider keys are intentionally excluded.</p>
    </div>
  );
}
