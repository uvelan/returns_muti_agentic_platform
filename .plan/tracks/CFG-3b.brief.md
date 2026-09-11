# CFG-3b — Premium form primitives and a real key/value editor

Base: the RV-approved head of CFG-0 (file-disjoint from CFG-1 and CFG-3a). Branch `feat/cfg-3b-form-primitives`. Worktree `.claude/worktrees/cfg-3b` (node_modules junctioned; run vitest/tsc/eslint from `frontend/`).

Owns: `frontend/src/components/forms/**` (new), `frontend/src/domains/config/DocumentEditor.tsx` (path-mapped errors, `KeyValueTable` for data-keyed objects), `frontend/src/api/mergePatch.ts`, `frontend/src/index.css` (component-layer classes only, no new tokens), tests beside each file. Must not touch: page components, routes, backend.

Design constraints: the existing M3 teal kit (`tailwind.config`, `premium-panel`, `premium-kicker`, `premium-field`, `outline-control` for input edges) is the palette and type; add no font and no colour. Every control: visible label, hint, error slot, keyboard operable, focus ring from `premium-field`, `aria-describedby` for hint and error, `aria-invalid` when in error. Dense but breathing: 8px grid, labels 11px uppercase kicker, values 14px, tabular numerals for numbers.

Budget: 300k; stop rule at 240k with `drop.json` PARTIAL.

## Primitives (one file, one test each)

| Component | Props (essentials) | Behaviour |
|---|---|---|
| `Field` | label, hint, error, required, htmlFor, children | layout + a11y wiring |
| `FieldGroup` | kicker, title, description, children, collapsible? | section framing |
| `Toggle` | value, onChange, label, reasonField? {value, onChange, requiredWhen: "off"|"on"} | switch with an inline reason input when the model requires one (policy_evaluation) |
| `NumberField` | value, onChange, min, max, step, unit | tabular numerals, unit suffix, clamps on blur, shows the model's range |
| `DurationField` | seconds, onChange | s / min / h / d selector, stores seconds, shows the human form |
| `EnumSelect` | value, options, onChange, allowUnknown | current value always present even if not in options |
| `TagListInput` | values, onChange, suggestions? | chips; add on Enter/comma, remove, reorder with keyboard (Alt+arrow) |
| `OrderedList<T>` | items, onChange, renderItem, keyOf | drag and keyboard reorder; used for stages, ladder rungs, provider order |
| `KeyValueTable` | entries {key, value}, onChange, valueKind: "string"\|"number"\|"boolean"\|"json", keyPattern?, sortable | add, rename (with duplicate detection), delete, inline validation, insertion order preserved unless sortable |
| `PathPicker` | value, onChange, paths (from the active schema) | text input with autocomplete; free text allowed |
| `DiffPreview` | before, after | renders `mergePatchOf(before, after)` as a path → before → after table, deletions flagged; empty state says "Nothing changed" |
| `PublishBar` | dirtyCount, onValidate, onPublish, steps, disabledReason | sticky footer; disabled with reason when not dirty or no capability |
| `ValidationErrors` | errors [{path, message}], onJump(path) | page-level list; each row jumps to and highlights the field |

## DocumentEditor changes
- Accept `errors: {path, message}[]` and render each under the matching generated field (path match on the dotted key); unknown paths go to a page-level list.
- Objects whose keys are data (no fixed schema: `ship_via_methods`, `dependencies`, `tasks`, `source_mirror`) render as `KeyValueTable`; the caller passes `dataKeyedPaths: string[]` to say which. Default behaviour unchanged for everyone else.
- Keep the Form / Split / JSON modes; JSON stays the escape hatch.

## Acceptance
- Every primitive: vitest render + interaction test and an axe (`vitest-axe` or `jest-axe`, whichever the repo has) check with zero violations.
- `DocumentEditor` tests for path-mapped errors and `KeyValueTable` rendering; existing Agents/Support Template/Business tests unchanged and green.
- `npm run typecheck`, `npm run lint`, `npx vitest run src/components/forms src/domains/config` clean.
- A `frontend/src/components/forms/README.md` with one example per primitive (used by CFG-4/5 as the reference).
