# RV — CFG-3b @ f3a7340d — VERDICT: PASS

Round 2. Worktree `.claude/worktrees/cfg-3b`, branch `feat/cfg-3b-form-primitives`, head `f3a7340d` (code at `48ccbb79`, bookkeeping at `f3a7340d`), round-1 head `47340cc7`. Read-only; the three probe test files used for the behavioural evidence below were deleted and `git status --porcelain` in the worktree is empty.

Round-2 scope is six files — `DocumentEditor.tsx` (+219/−47), `DocumentEditor.test.tsx`, `KeyValueTable.test.tsx`, `forms/README.md`, the ledger and `drop.json`. Nothing outside the Owns list; `KeyValueTable.tsx` itself was not touched, which is right: the defect was never in the component.

## Round-1 findings — dispositions

| ID | Round-1 sev | Disposition | Evidence |
|---|---|---|---|
| B1 | **BLOCKING** | **FIXED** — verified by rerunning the P5 reproduction | `DocumentEditor.tsx:765-875` (`DataKeyedObjectNode`, `keyValueBlockReason`), `:222-246` (`reportBlocked`), `:338` (`onSave` guard), `:502-503` (Save disabled + reason). Probe Q1 below. |
| A1 | ADVISORY | **FIXED + documented** — `foo[3].bar` → `foo.3.bar` before matching | `DocumentEditor.tsx:53-64` `normalizeErrorPath`, `:391-393`; test `DocumentEditor.test.tsx:81-93`; `README.md` "Error path convention". |
| A2 | ADVISORY | **Accepted as a documented limit** — a data-keyed key containing `.` is not addressable | `README.md` "Error path convention", final paragraph. Reasonable: escaping rules on operator-entered keys would cost more than the case is worth. |
| A3 | ADVISORY | **Documented, not changed** — integer-like keys hoist on the object round-trip | `README.md` "Data-keyed key ordering is not preserved through a plain JS object", with the `{"2":…,"10":…,"b":…,"a":…}` example and an explicit "do not build a UI or a test that depends on submitted key order". Correct call — it is JS semantics, not an editor bug. |
| A4 | ADVISORY | **FIXED** — both invariants now have real tests | `KeyValueTable.test.tsx:125-158` (reindexing: two survivors each keep their *own* distinct, still-invalid JSON draft across a middle-row delete) and `:160-182` (in-place duplicate: both rows survive, both `aria-invalid`, both alerts). Neither is a tautology; both would have failed against the pre-fix code. |
| A5 | ADVISORY | Carried to CFG-4 — new-key input still has no error element | `KeyValueTable.tsx:210-228`. |
| A6 | ADVISORY | Carried to CFG-4 — `OrderedList`/`KeyValueTable` still take no `error` prop | — |
| A7 | ADVISORY | Carried to CFG-4 — `PublishBar` `disabledReason` still not `aria-describedby` | `PublishBar.tsx:144-155`. |
| A8 | ADVISORY | Carried — `/api/config/validate/{domain}` and `FieldGroup`-for-schema-objects belong to CFG-4/6 | Correctly out of this lease's brief. |

## New finding

| ID | Sev | file:line | What | Why | Fix |
|---|---|---|---|---|---|
| C1 | ADVISORY | `DocumentEditor.tsx:854-856` | The `blocked` entry survives the form editor unmounting. Switching to JSON mode while a data-keyed table holds a duplicate (`:532`, `mode === "json"` unmounts `formEditor`) leaves Save disabled with a `title` naming rows that are no longer on screen. The effect that clears the block only runs while the node is mounted; there is no unmount cleanup. | The brief keeps JSON as the escape hatch, and an operator who hits a collision and switches to JSON to fix it by hand finds Save dead with no on-screen explanation. It is **recoverable** (switch back to Key-value — probe R2 confirms the block clears on remount) and it fails *closed*: the document never held the duplicate, so refusing to publish is over-conservative, not lossy — the opposite direction from B1. Split mode keeps the form mounted and is unaffected (R3). | Return a cleanup from the effect: `return () => { reportBlocked(path, null); };`. Two lines, and it wants a test. |

## B1 — reproduction rerun (probe Q1, temporary test, since deleted)

Same starting document and same keystrokes as round 1's P5: `{ ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } }`, rename row 1's key to `XPW`.

```
Q1 rows after rename: ["XPW","XPW"]
Q1 values: ["COUNTER","PARCEL"]
Q1 alerts: This key is used more than once. | This key is used more than once.
Q1 Save disabled: true | title: "XPW" is used by more than one row here -- rename one before publishing.
Q1 onSubmit calls after clicking the disabled Save: 0
Q1 after resolving -- rows: ["XPW2","XPW"] | alerts: 0 | Save disabled: false
Q1 onSubmit arg: {"ship_via_methods":{"XPW2":"COUNTER","XPW":"PARCEL"}}
```

Round 1 produced `rows after rename: 1`, `duplicate alert: (none)`, and `onSubmit arg: {"ship_via_methods":{"XPW":"PARCEL"}}` — CPU's row and its `"COUNTER"` value destroyed. Both rows now survive with their own values, the duplicate detection `KeyValueTable` always had actually fires, Save is refused with the collision named, a forced click submits nothing, and the resolved document carries both entries. **B1 is fixed.**

## Render-phase reset — does it re-reset while the operator is typing?

`DataKeyedObjectNode` holds `pending: KeyValueEntry[] | null` and resyncs with `if (value !== trackedValue) { setTrackedValue(value); setPending(null); }` during render (`:849-852`). The correctness question is whether `value` can change identity for reasons that are not a real document change. It cannot: `draft` is `useState(loaded)` and is never resynced from `loaded` by an effect, so a parent re-render — even one passing a brand-new `loaded` object literal — does not touch it; and a sibling edit rebuilds the parent with `{...value, [key]: next}`, which preserves the untouched data-keyed child's reference.

```
Q2a before parent re-render -- rows: ["XPW","XPW"] | alerts: 2
Q2a AFTER parent re-render  -- rows: ["XPW","XPW"] | alerts: 2 | Save disabled: true
Q2b after sibling edit      -- rows: ["XPW","XPW"] | alerts: 2 | Save disabled: true
Q2b sibling value: hello!
```

The pending rename survives both, and the sibling edit itself lands. The reset fires only on a genuine external change:

```
R1 blocked      -- rows: ["XPW","XPW"] | alerts: 2 | Save disabled: true | title: "XPW" is used by more than one row here -- ...
R1 after Reset  -- rows: ["CPU","XPW"] | alerts: 0 | Save disabled: true | title: (none)
R1 after sibling-- rows: ["CPU","XPW"] | alerts: 0 | Save disabled: false | title: (none)
```

Reset restores the baseline keys, clears both alerts and clears the block; Save is then disabled only because the editor is no longer dirty, and a subsequent sibling edit re-enables it with no stale block. (An earlier probe appeared to show Reset failing — that run had not stubbed `window.confirm`, which jsdom leaves unimplemented, so the handler's own guard returned early. Not a defect.)

Switching documents by changing the editor's React `key` remounts the whole tree and starts clean:

```
Q3b blocked before switch -- Save disabled: true
Q3b after key switch -- rows: ["FDX"] | alerts: 0 | Save disabled: true
```

No React warnings in any probe run — no "Cannot update a component while rendering a different component", no "Maximum update depth exceeded". The conditional `setState`-during-own-render is React's sanctioned pattern and is applied to this component's own state; the cross-component `reportBlocked` correctly stays in an effect. The ledger's note that `eslint-plugin-react-hooks@7`'s `set-state-in-effect` rule forced this shape is accurate and worth keeping.

Empty keys block on the same path, as intended: `R4 empty key -- rows: ["",​"XPW"] | Save disabled: true | title: Every key must be filled in before publishing.`

One behaviour worth naming, not a defect: intermediate *unique* rename states do commit to the document keystroke by keystroke, so a rename abandoned mid-way leaves the last unique prefix (`CPU` → `XP` on the way to `XPW`). That is ordinary controlled-input behaviour and matches every other field in this editor.

## Commands

```
$ npx vitest run src/components/forms src/domains/config src/api/mergePatch.test.ts
 Test Files  21 passed (21)
      Tests  145 passed (145)
   Duration  19.23s

$ npm run typecheck        # tsc -b --pretty false
(no output, exit 0)

$ npm run lint             # eslint . --max-warnings=0
(no output, exit 0)

$ npx vitest run
 FAIL  src/domains/registry.test.ts > declares exactly the canonical domains   (expected 8, received 9: "/shipments")
 FAIL  src/domains/registry.test.ts > shares a visibility capability only where that is deliberate
 Test Files  1 failed | 78 passed (79)
      Tests  2 failed | 966 passed (968)
   Duration  54.79s
```

Exactly the two known pre-existing `registry.test.ts` failures; nothing else fails. Scoped count is 141 → 145, matching the four new tests. `drop.json` now records `head_sha: 48ccbb79` (the code commit, one behind the bookkeeping head — correct, and the round-1 staleness note is discharged) and the whole-suite line.

## Judgement

The fix is the right one rather than the cheap one. The round-1 defect was not a missing guard but a shape mismatch — `KeyValueTable` speaks in a list, which can hold two rows with the same key while a rename is in flight, and the document is a plain object, which cannot — and flattening on every keystroke forced every intermediate through the narrower shape, which is why the collision was destructive before the component that detects collisions ever rendered. `DataKeyedObjectNode` fixes that at the seam: the list stays a list until it is safe to be an object, the collision stays visible in both rows for as long as it exists, and `onChange` can only ever receive a document that could not have lost anything. The belt-and-braces `onSave` guard alongside the disabled button is the right instinct given what the failure mode was. The supporting work is honest too: A4's two tests exercise the invariant rather than restate the implementation — the reindexing test gives each surviving row a *distinct, still-invalid* draft so a mix-up would be visible, which is exactly the assertion round 1 said was missing — and A1's normalisation comes with a test and a README section that finally pins the path convention CFG-4/5 will have to produce against. A2 and A3 were correctly answered with documentation rather than code; A3 in particular resists the temptation to invent an ordering mechanism for something JSON does not guarantee. C1 is the one loose end: the block survives the form editor unmounting, so a duplicate in a table you can no longer see disables Save in JSON mode. It fails closed, it is recoverable by switching back, and it is a two-line unmount cleanup — advisory, not a second round. PASS, with C1 and the five carried advisories going to CFG-4.
