import { Redirect } from "wouter";

/**
 * `/sync` -- CFG-5 moved this domain's whole screen (source bindings' sync
 * trigger and run history, unchanged in behaviour) into
 * `/config/source-bindings` (`DataSourcesSection.tsx`). The domain entry
 * stays registered (`registry.ts`'s `DOMAINS`) rather than being deleted, so
 * a bookmark or an old link still lands somewhere real instead of bouncing
 * to the launcher's unknown-path fallback -- the same alias-for-one-release
 * shape `/config/support-template` uses for the same reason.
 */
export function SyncRedirect() {
  return <Redirect to="/config/source-bindings" replace />;
}
