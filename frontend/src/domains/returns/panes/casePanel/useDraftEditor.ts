import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { asReviewConflict, casePanelApi, type ReviewPanelView } from "../../../../api/casePanel";

/**
 * One associate's edits to one draft: restore, autosave, and what happens when
 * the draft moves underneath them.
 *
 * **The rule this hook exists to enforce** (contracts.md sect. 3, frontend
 * outcome gates): a support artifact arriving mid-edit must steal no focus and
 * drop no edit. The panel polls every ten seconds, so a re-render, a second
 * actor's conflict marker or a newly bound artifact can land between two
 * keystrokes. None of them may reach into the field the associate is typing in.
 *
 * That is why the edited values live here, keyed by `review_id`, and are
 * **never** re-seeded from a poll while the associate holds unsaved work. The
 * incoming draft is remembered separately so the panel can say the draft moved
 * -- politely, in a live region, with the edits intact -- and the associate
 * decides whether to take it.
 */

export type DraftField = {
  readonly field_id: string;
  readonly label: string;
  readonly value: string;
  readonly source: string;
  readonly source_path: string;
  readonly fact_id: string | null;
  readonly applied_fallback: boolean;
};

export type DraftSection = {
  readonly section_id: string;
  readonly title: string;
  readonly return_record_id: string | null;
  readonly fields: readonly DraftField[];
};

export type DraftPayload = {
  readonly subject?: string;
  readonly text?: string;
  readonly body_override?: string;
  readonly sections?: readonly DraftSection[];
  /**
   * `TemplateGap{field_id, reason}` -- both required, because the renderer is
   * the only producer and sect. 8 fixes the shape. Optional fields here would
   * make the panel invent words for a gap the renderer always describes.
   */
  readonly gaps?: readonly { readonly field_id: string; readonly reason: string }[];
  readonly [key: string]: unknown;
};

export type SaveStatus = "idle" | "saving" | "saved" | "failed" | "stale";

/** How long after the last keystroke an autosave fires. */
const AUTOSAVE_DELAY_MS = 800;

function readPayload(review: ReviewPanelView): DraftPayload {
  return review.draft;
}

function fieldValues(payload: DraftPayload): Record<string, string> {
  const values: Record<string, string> = {};
  for (const section of payload.sections ?? []) {
    for (const field of section.fields) {
      // Keyed by section too: a per-record request repeats one `field_id`
      // across record groups, and a flat key would make two records' values
      // one value. That is the multi-RMA collision phase 1 fixed in the
      // renderer's subject map, in its editing clothes.
      values[fieldKey(section.section_id, field.field_id)] = field.value;
    }
  }
  return values;
}

export function fieldKey(sectionId: string, fieldId: string): string {
  return `${sectionId}::${fieldId}`;
}

/** A payload rebuilt from the edited values, ready to autosave or approve. */
export function applyEdits(
  payload: DraftPayload,
  values: Record<string, string>,
  bodyOverride: string | null,
): DraftPayload {
  const sections = (payload.sections ?? []).map((section) => ({
    ...section,
    fields: section.fields.map((field) => ({
      ...field,
      value: values[fieldKey(section.section_id, field.field_id)] ?? field.value,
    })),
  }));
  const next: Record<string, unknown> = { ...payload, sections };
  if (bodyOverride === null) delete next.body_override;
  else next.body_override = bodyOverride;
  return next;
}

export type DraftEditor = {
  readonly payload: DraftPayload;
  readonly values: Record<string, string>;
  readonly bodyOverride: string | null;
  readonly dirty: boolean;
  readonly saveStatus: SaveStatus;
  /** Set when the poll brought a draft newer than the one being edited. */
  readonly supersededDraftVersion: number | null;
  /** What the live region announces. Empty between announcements. */
  readonly announcement: string;
  readonly setField: (sectionId: string, fieldId: string, value: string) => void;
  readonly setBodyOverride: (value: string | null) => void;
  /** Drop this actor's edits and take the incoming draft. */
  readonly discardEdits: () => void;
  /** Flush any pending autosave. Used before an approval. */
  readonly flush: () => Promise<void>;
};

export function useDraftEditor(
  caseId: string,
  review: ReviewPanelView | null,
  options: { readonly autosave?: boolean } = {},
): DraftEditor {
  const autosaveEnabled = options.autosave ?? true;
  const reviewId = review?.review_id ?? null;
  const incoming = useMemo(() => (review ? readPayload(review) : {}), [review]);

  const [values, setValues] = useState<Record<string, string>>({});
  const [bodyOverride, setBodyOverrideState] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [announcement, setAnnouncement] = useState("");
  const [supersededDraftVersion, setSuperseded] = useState<number | null>(null);

  /**
   * Which review, and which version of its draft, the values on screen came
   * from.
   *
   * State rather than a ref because it is *adjusted during render* below --
   * React's documented pattern for "reset some state when a prop changes", and
   * the reason none of this lives in an effect. An effect would paint the old
   * values first and then replace them, which for a text field means a visible
   * flash and, worse, a re-render arriving between the two.
   */
  const [seeded, setSeeded] = useState<{ reviewId: string | null; draftVersion: number }>({
    reviewId: null,
    draftVersion: 0,
  });

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<null | (() => Promise<void>)>(null);

  if (review && seeded.reviewId !== review.review_id) {
    // A different review is on screen. Everything resets, including the
    // announcement -- the last thing said about the previous draft is not true
    // of this one.
    setSeeded({ reviewId: review.review_id, draftVersion: review.draft_version });
    setValues(fieldValues(readPayload(review)));
    setBodyOverrideState(null);
    setDirty(false);
    setSaveStatus("idle");
    setSuperseded(null);
    setAnnouncement("");
  } else if (review && seeded.draftVersion !== review.draft_version) {
    /*
     * The draft moved under the editor.
     *
     * **Not applied while they are typing, and never focused.** If the
     * associate is clean, taking the new draft is obviously right. If they are
     * dirty, replacing what they typed because a poll landed is the "drops the
     * edit" failure the outcome gate names -- so the new version is recorded,
     * the live region says so politely, and the decision is theirs.
     */
    if (!dirty) {
      setSeeded({ reviewId: review.review_id, draftVersion: review.draft_version });
      setValues(fieldValues(readPayload(review)));
      setAnnouncement("The draft has been re-rendered with the latest case facts.");
    } else if (supersededDraftVersion !== review.draft_version) {
      setSuperseded(review.draft_version);
      setSaveStatus("stale");
      setAnnouncement(
        "A newer draft has arrived. Your edits are kept — review the new draft before sending.",
      );
    }
  }

  /**
   * Restore this actor's row (contracts.md sect. 9).
   *
   * A reload, a shift handover or a closed tab must not lose an autosaved
   * draft. `payload === null` means "you have not edited this", which is a
   * different answer from "you edited it to nothing" -- and the restore path is
   * the only thing that depends on telling them apart, which is why the
   * endpoint distinguishes them.
   *
   * This half genuinely is an effect: it is a request, and the answer arrives
   * later.
   */
  useEffect(() => {
    if (!reviewId) return;
    let cancelled = false;
    void casePanelApi
      .readEditState(caseId, reviewId)
      .then((row) => {
        if (cancelled || !row.payload) return;
        const restored = row.payload as DraftPayload;
        setValues(fieldValues(restored));
        setBodyOverrideState(
          typeof restored.body_override === "string" ? restored.body_override : null,
        );
        setDirty(true);
        setAnnouncement("Your unsent edits to this draft have been restored.");
      })
      .catch(() => {
        // A restore that fails leaves the agent's draft on screen, which is a
        // truthful thing to show. Announced rather than thrown: losing the
        // whole panel over an autosave row would be a worse trade.
        setAnnouncement("Your earlier edits could not be loaded. The original draft is shown.");
      });
    return () => {
      cancelled = true;
    };
  }, [caseId, reviewId]);

  const save = useCallback(
    async (nextValues: Record<string, string>, nextBody: string | null) => {
      if (!reviewId || !review) return;
      setSaveStatus("saving");
      try {
        await casePanelApi.saveEdit(caseId, reviewId, {
          // One id per keystroke batch, so a retry over a flaky connection is
          // a no-op rather than a version bump.
          client_edit_id: `${reviewId}:${String(seeded.draftVersion)}:${String(Date.now())}`,
          base_draft_version: seeded.draftVersion,
          payload: applyEdits(incoming, nextValues, nextBody),
        });
        setSaveStatus("saved");
        setAnnouncement("Draft saved.");
      } catch (error) {
        const conflict = asReviewConflict(error);
        if (conflict?.field === "base_draft_version") {
          setSaveStatus("stale");
          setAnnouncement(
            "A newer draft has arrived. Your edits are kept — review the new draft before sending.",
          );
          return;
        }
        setSaveStatus("failed");
        setAnnouncement("Your edits could not be saved. They are still on screen.");
      }
    },
    [caseId, incoming, review, reviewId, seeded.draftVersion],
  );

  const schedule = useCallback(
    (nextValues: Record<string, string>, nextBody: string | null) => {
      if (!autosaveEnabled) return;
      if (timer.current) clearTimeout(timer.current);
      const run = async () => {
        pending.current = null;
        await save(nextValues, nextBody);
      };
      pending.current = run;
      timer.current = setTimeout(() => void run(), AUTOSAVE_DELAY_MS);
    },
    [autosaveEnabled, save],
  );

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const setField = useCallback(
    (sectionId: string, fieldId: string, value: string) => {
      setValues((held) => {
        const next = { ...held, [fieldKey(sectionId, fieldId)]: value };
        schedule(next, bodyOverride);
        return next;
      });
      setDirty(true);
    },
    [bodyOverride, schedule],
  );

  const setBodyOverride = useCallback(
    (value: string | null) => {
      setBodyOverrideState(value);
      setDirty(true);
      schedule(values, value);
    },
    [schedule, values],
  );

  const discardEdits = useCallback(() => {
    if (!review) return;
    setSeeded({ reviewId: review.review_id, draftVersion: review.draft_version });
    setValues(fieldValues(readPayload(review)));
    setBodyOverrideState(null);
    setDirty(false);
    setSuperseded(null);
    setSaveStatus("idle");
    setAnnouncement("Your edits were discarded. The latest draft is shown.");
  }, [review]);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    const run = pending.current;
    pending.current = null;
    if (run) await run();
  }, []);

  return {
    payload: incoming,
    values,
    bodyOverride,
    dirty,
    saveStatus,
    supersededDraftVersion,
    announcement,
    setField,
    setBodyOverride,
    discardEdits,
    flush,
  };
}
