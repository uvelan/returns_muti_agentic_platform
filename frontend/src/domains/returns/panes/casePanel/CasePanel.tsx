import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import {
  casePanelApi,
  casePanelKeys,
  panelRefetchInterval,
  type CasePanelView,
} from "../../../../api/casePanel";
import { COPILOT_TOKENS } from "../../copilotTokens";
import { readSupportReplyDraft } from "./sections/supportReplyDraft";
import { StatusTimersSection } from "./StatusTimersSection";
import { TemplateReviewSection } from "./TemplateReviewSection";
import { panelSectionRenderers, unrenderedSectionLabel } from "./panelSectionRegistry";

/**
 * The case panel: one poll, one payload, every section.
 *
 * The same `CasePanelView` feeds this and `CaseOperationsPage` (brief item 8),
 * which is why the composition lives here rather than inside the copilot's
 * pane: two screens deriving the same thing from the same payload in two places
 * is two places for them to start disagreeing about what a held review means.
 *
 * **Sections V2 and V3 contribute are drawn through the registry**, never by
 * this file naming them.
 *
 * The two built-ins are *not* registered, and the honest reason is that neither
 * fits the contributor shape: the status section reads `CasePanelView`'s own
 * fields rather than a contributed payload, and the review section iterates
 * `reviews[]` and dispatches mutations, which a section renderer takes no
 * argument for. Widening the renderer contract to fit them would put V1's needs
 * into the seam V2 and V3 have to live with -- the opposite of what the seam is
 * for. The registry is exercised instead by `CasePanel.test.tsx`, which
 * registers a section, asserts it renders in the right place, and asserts an
 * unregistered contributed section shows as a labelled placeholder.
 */

type Props = {
  readonly caseId: string;
  /** `CaseOperationsPage` reads the same payload and takes no actions on it. */
  readonly readOnly?: boolean;
};

export function CasePanel({ caseId, readOnly = false }: Props) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: casePanelKeys.panel(caseId),
    queryFn: () => casePanelApi.read(caseId),
    refetchInterval: (state) => panelRefetchInterval(state.state.error),
  });

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: casePanelKeys.panel(caseId) });
  }, [caseId, queryClient]);

  /**
   * Nothing to say, so nothing said.
   *
   * The copilot mounts this under every mode, because the platform can need to
   * ask Support something at almost any point in a return. On the great
   * majority of cases it never does -- and a pane that announced "nothing is
   * waiting for Support" on every one of those would be a permanent piece of
   * furniture reporting an absence.
   *
   * The operations view keeps the empty state: somebody auditing a case needs
   * to be told the difference between "no review" and "this screen did not
   * load", and they arrived here deliberately to find out.
   */
  const quiet =
    !readOnly &&
    query.data?.reviews.length === 0 &&
    query.data.timers.template_review_deadline_iso === null;

  if (quiet) return null;

  if (query.isPending) {
    return readOnly ? (
      <p role="status" className={COPILOT_TOKENS.typography.caption}>
        Loading the support review…
      </p>
    ) : null;
  }

  if (query.isError) {
    return (
      <p role="alert" className={COPILOT_TOKENS.review.gap}>
        {query.error instanceof Error
          ? query.error.message
          : "The support review could not be loaded."}
      </p>
    );
  }

  return <CasePanelBody caseId={caseId} panel={query.data} readOnly={readOnly} onChanged={refresh} />;
}

export function CasePanelBody({
  caseId,
  panel,
  readOnly,
  onChanged,
}: {
  readonly caseId: string;
  readonly panel: CasePanelView;
  readonly readOnly: boolean;
  readonly onChanged: () => void;
}) {
  const contributed = new Map(panel.sections.map((section) => [section.section_id, section]));
  const renderers = panelSectionRenderers();
  const drawn = new Set(renderers.map((renderer) => renderer.sectionId));

  return (
    <div className="space-y-5">
      <StatusTimersSection panel={panel} />

      {panel.reviews.length === 0 ? (
        <p className={COPILOT_TOKENS.typography.caption}>
          {/*
            An empty state that says what will happen, not "no data". Nothing
            has gone wrong here -- the case simply has not needed to ask Support
            anything yet.
          */}
          Nothing is waiting for Support on this return. If the platform needs to ask them
          something, the draft will appear here for you to check before it is sent.
        </p>
      ) : (
        panel.reviews.map((review) =>
          readOnly ? (
            <ReadOnlyReview key={review.review_id} review={review} />
          ) : (
            <TemplateReviewSection
              key={review.review_id}
              caseId={caseId}
              review={review}
              onChanged={onChanged}
            />
          ),
        )
      )}

      {renderers.map((renderer) => (
        <div key={renderer.sectionId}>
          {renderer.render({ section: contributed.get(renderer.sectionId), panel, caseId })}
        </div>
      ))}

      {/*
        A section the server composed and this bundle cannot draw. Shown as a
        labelled placeholder rather than dropped: silently discarding it hides a
        deployment skew -- the server is newer than the console -- that nobody
        would otherwise see.
      */}
      {panel.sections
        .filter((section) => !drawn.has(section.section_id))
        .map((section) => (
          <p key={section.section_id} className={COPILOT_TOKENS.typography.caption}>
            {unrenderedSectionLabel(section)}
          </p>
        ))}
    </div>
  );
}

/**
 * The same review, for a screen that reports rather than acts.
 *
 * `CaseOperationsPage` is an operations view over somebody else's case: the
 * associate holding the box is the one who decides what Support is told, and an
 * operations screen that offered Send would put that decision in front of the
 * wrong person. It reads the identical payload -- brief item 8 -- and draws
 * fewer affordances, which is a different thing from a second contract.
 */
function ReadOnlyReview({ review }: { readonly review: CasePanelView["reviews"][number] }) {
  const sections = (review.draft as { sections?: readonly Record<string, unknown>[] }).sections ?? [];
  /*
   * A `SUPPORT_REPLY` review has no `sections[]` (V3, `reply_gating.py`), so the
   * list below is empty for one and the audit view showed a heading and nothing
   * else. Somebody auditing a case needs to read the reply that went out at
   * least as much as they need to read the request that went out.
   *
   * `whitespace-pre-wrap`: the paragraph breaks are the sender's own, and this
   * is a record of what was sent rather than a question being quoted.
   */
  const reply = readSupportReplyDraft(review);
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className={COPILOT_TOKENS.typography.subheading}>
          {reply === null ? "Message to Support" : "Reply to Support"}
        </h3>
        <span className={COPILOT_TOKENS.typography.badge}>{review.state}</span>
      </div>
      {review.approved_by !== null ? (
        <p className={COPILOT_TOKENS.typography.caption}>Approved by {review.approved_by}.</p>
      ) : null}
      {reply === null ? null : (
        <p className={`${COPILOT_TOKENS.review.field.value} whitespace-pre-wrap`}>
          {reply.messageText === "" ? "This reply was empty." : reply.messageText}
        </p>
      )}
      <ul className="space-y-0.5">
        {sections.map((section) => {
          const held = section as { section_id: string; title: string };
          return (
            <li key={held.section_id} className={COPILOT_TOKENS.typography.body}>
              {held.title}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
