import { describe, expect, it } from "vitest";
import { placedByTime, saidAt } from "./chatTime";
import type { ChatHistoryEntry } from "./panes/ConversationPane";

const said = (id: string, at?: string): ChatHistoryEntry => ({
  role: "restored",
  id,
  author: "associate",
  text: id,
  ...(at === undefined ? {} : { at }),
});
const update = (id: string, at?: string): ChatHistoryEntry => ({
  role: "system",
  id,
  kicker: "Update from the platform",
  text: id,
  ...(at === undefined ? {} : { at }),
});

describe("placedByTime", () => {
  // "what is the status" (07:30) sat above the item pane's record (07:28) and
  // Support's RMA (07:29) on 2026-09-10, because entries were appended after
  // the last thing typed. With instants on both sides they land in order.
  it("puts a platform entry before the message said after it", () => {
    const history = [
      said("confirm", "2026-09-10T07:26:55Z"),
      said("status", "2026-09-10T07:30:27Z"),
    ];
    const arriving = [
      update("recorded", "2026-09-10T07:28:10Z"),
      update("rma", "2026-09-10T07:29:40Z"),
    ];
    expect(placedByTime(history, arriving).map((entry) => entry.id)).toEqual([
      "confirm",
      "recorded",
      "rma",
      "status",
    ]);
  });

  it("keeps an undated entry, and one later than everything, at the end", () => {
    const history = [said("a", "2026-09-10T07:00:00Z"), said("b")];
    const arriving = [
      update("undated"),
      update("later", "2026-09-10T09:00:00Z"),
    ];
    expect(placedByTime(history, arriving).map((entry) => entry.id)).toEqual([
      "a",
      "b",
      "undated",
      "later",
    ]);
  });

  it("does not touch the history when nothing arrives", () => {
    const history = [said("a", "2026-09-10T07:00:00Z")];
    expect(placedByTime(history, [])).toEqual(history);
  });
});

describe("saidAt", () => {
  it("shows a clock time for an instant and nothing for the absence of one", () => {
    expect(saidAt("2026-09-10T07:30:27Z")).toMatch(/\d/);
    expect(saidAt(undefined)).toBeNull();
    expect(saidAt("not an instant")).toBeNull();
  });
});
