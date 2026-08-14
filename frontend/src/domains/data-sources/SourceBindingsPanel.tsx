import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { sourceBindingsApi, type SourceBinding } from "../../api/sourceBindings";
import { useCapabilities } from "../../hooks/capabilityContext";

/**
 * Where each dataset the schema names is read from, and how to move one.
 *
 * **Extracted, not written twice.** This was the analyzer's Sources tab. The
 * Data Sources domain needs exactly the same panel -- it is the platform's only
 * edit-a-source capability -- and a second copy would be two rebind forms with
 * one backend behind them, drifting until they disagreed about what "rebind"
 * sends. The analyzer keeps it too, because that is where someone reading a
 * draft asks "and where does that come from"; the answer is just no longer
 * implemented there.
 *
 * A change here reaches the platform at the *next publish*, which the panel
 * says plainly: a rebinding that silently re-pointed a running release would
 * make the approval on it meaningless.
 *
 * **`connectionRef` is a reference, never a credential.** It is a
 * `vault://return-platform/sources#...` pointer that the platform resolves
 * server-side. `RebindRequest` has no password, DSN or connection-string field
 * to send one through, and nothing that comes back carries a resolved value --
 * so this form asks for the pointer and offers nowhere to type a secret.
 *
 * **The panel asks the capability question itself, rather than taking the
 * answer as a prop.** `PUT`/`DELETE /api/source-bindings/{dataset}` require
 * `config.source.rebind`, and while the gate was a prop both call sites passed
 * something else: Data Sources passed `config.source.write` and the analyzer's
 * Sources tab passed `graph_schema.draft.write`. Two callers, two wrong answers
 * to one question with one right answer -- and a `WORKSPACE_EDITOR`, who holds
 * both of those and not the rebind, was offered a button that 403s. The panel
 * makes the two calls, so the panel names the grant, and there is nowhere left
 * to pass a different one from.
 */
export function SourceBindingsPanel() {
  const { can } = useCapabilities();
  const canRebind = can("config.source.rebind");
  const client = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [connectionRef, setConnectionRef] = useState("");

  const bindings = useQuery({
    queryKey: ["source-bindings"],
    queryFn: () => sourceBindingsApi.list(),
  });

  const rebind = useMutation({
    mutationFn: ({ dataset, from }: { dataset: string; from: SourceBinding }) =>
      sourceBindingsApi.rebind(dataset, {
        sourceAssetId: from.sourceAssetId,
        connectorType: from.connectorType,
        // Only the connection moves here. Changing the object reference is
        // pointing at *different data*, not at the same data somewhere else,
        // and it belongs with a schema change rather than a one-field edit.
        objectRef: from.objectRef,
        connectionRef,
        incrementalCursorField: from.incrementalCursorField,
      }),
    onSuccess: async () => {
      setEditing(null);
      await client.invalidateQueries({ queryKey: ["source-bindings"] });
    },
  });

  const reset = useMutation({
    mutationFn: async (dataset: string) => { await sourceBindingsApi.clear(dataset); },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["source-bindings"] });
    },
  });

  if (bindings.error) {
    return <p className="text-sm text-red-700">{bindings.error.message}</p>;
  }
  if (bindings.isPending) {
    return <p className="text-sm text-slate-600">Loading...</p>;
  }

  if (bindings.data.length === 0) {
    // Distinct from the failure above. No datasets resolved means the active
    // schema names none, which is something an operator would act on.
    return (
      <p className="text-sm text-slate-600">
        No datasets are bound. The active schema names no source assets.
      </p>
    );
  }

  return (
    <div>
      <p className="mb-3 text-sm text-slate-600">
        A change here reaches the platform at the next publish. The active release keeps the
        sources it was compiled with.
      </p>
      {/*
        Said once, above the list, rather than left as a row of dead buttons. A
        disabled control with no reason is indistinguishable from a broken one,
        and the reader's next move -- ask for the grant -- needs the grant's
        name.
      */}
      {canRebind ? null : (
        <p className="mb-3 text-sm text-slate-600">
          Repointing a dataset requires <code className="font-mono">config.source.rebind</code>,
          which you do not hold. Bindings are shown read-only.
        </p>
      )}
      {rebind.error ? <p className="mb-2 text-sm text-red-700">{rebind.error.message}</p> : null}
      <ul className="flex flex-col gap-2">
        {bindings.data.map((binding) => (
          <li key={binding.dataset} className="rounded-md border border-slate-200 p-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium text-slate-900">{binding.dataset}</span>
              <span className="font-mono text-xs text-slate-500">{binding.connectorType}</span>
              {binding.overridden ? (
                // Configured or deliberately changed is the first thing anyone
                // debugging a sync needs to know.
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-900">
                  rebound
                </span>
              ) : null}
            </div>
            <p className="mt-1 break-all font-mono text-xs text-slate-600">
              {binding.connectionRef}
            </p>

            {editing === binding.dataset ? (
              <form
                className="mt-2 flex flex-wrap items-center gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (connectionRef.trim().length > 0) {
                    rebind.mutate({ dataset: binding.dataset, from: binding });
                  }
                }}
              >
                <input
                  aria-label={`Connection for ${binding.dataset}`}
                  value={connectionRef}
                  onChange={(event) => { setConnectionRef(event.target.value); }}
                  className="min-w-64 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                />
                <button
                  type="submit"
                  disabled={connectionRef.trim().length === 0 || rebind.isPending}
                  className="rounded-md bg-slate-900 px-3 py-1 text-sm font-medium text-white disabled:bg-slate-300"
                >
                  Rebind
                </button>
                <button
                  type="button"
                  onClick={() => { setEditing(null); }}
                  className="text-sm text-slate-600"
                >
                  Cancel
                </button>
              </form>
            ) : (
              <div className="mt-2 flex gap-3">
                <button
                  type="button"
                  disabled={!canRebind}
                  onClick={() => {
                    setEditing(binding.dataset);
                    setConnectionRef(binding.connectionRef);
                  }}
                  className="text-sm text-slate-800 underline disabled:text-slate-400 disabled:no-underline"
                >
                  Rebind
                </button>
                {binding.overridden ? (
                  <button
                    type="button"
                    disabled={!canRebind || reset.isPending}
                    onClick={() => { reset.mutate(binding.dataset); }}
                    className="text-sm text-slate-600 underline disabled:text-slate-400 disabled:no-underline"
                  >
                    Follow configuration
                  </button>
                ) : null}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
