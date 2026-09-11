# Form primitives

Premium form controls for the typed configuration screens (CFG-4/5), built on the
existing kit -- `premium-panel`, `premium-kicker`, `premium-field`, the M3 teal
tokens in `tailwind.config.js`, `outline-control` for input edges. No new palette,
font, or dependency. Every control has a visible label, keyboard operation, a
visible focus ring (from `premium-field` / `:focus-visible`), `aria-describedby`
wiring hint and error text to the control, and `aria-invalid` when it is in error.

No `jest-axe`/`vitest-axe` integration exists in this repo (only
`@axe-core/playwright`, for the separate Playwright suite, which cannot run from
vitest/jsdom). Each primitive's test asserts role/name, the `aria-describedby` /
`aria-invalid` wiring, and keyboard operability directly instead.

`DocumentEditor` (`../../domains/config/DocumentEditor.tsx`) is the *generated*,
schema-agnostic form these primitives do not replace -- it stays the Advanced/JSON
escape hatch on every typed screen. These primitives are for screens that *do* know
their document's shape.

## Field

The shared label/hint/error/id wiring every other primitive here is built on.
`children` may be a render function; `Field` hands it the control's `id`,
`aria-describedby` (hint + error ids) and `aria-invalid`.

```tsx
import { Field } from "./Field";

<Field label="Max bays" hint="1 to 50" htmlFor="max-bays">
  {(control) => (
    <input {...control} type="number" value={maxBays} onChange={(e) => setMaxBays(Number(e.target.value))} />
  )}
</Field>
```

## FieldGroup

A named section -- kicker, title, optional description, optionally collapsible.

```tsx
import { FieldGroup } from "./FieldGroup";

<FieldGroup kicker="Discovery" title="Order discovery" description="How an order is found and confirmed.">
  {/* fields */}
</FieldGroup>
```

## Toggle

A labelled switch (never "Yes"/"No" as the accessible name). `reasonField` shows an
inline, required reason input only while the model requires one for the current state.

```tsx
import { Toggle } from "./Toggle";

<Toggle
  label="Policy evaluation"
  value={enabled}
  onChange={setEnabled}
  reasonField={{ value: reason, onChange: setReason, requiredWhen: "off" }}
/>
```

## NumberField

Tabular numerals, a unit suffix, the model's `min`/`max` shown as a hint, and
clamped on blur rather than mid-keystroke.

```tsx
import { NumberField } from "./NumberField";

<NumberField label="Max bays" value={maxBays} onChange={setMaxBays} min={1} max={50} unit="bays" />
```

## DurationField

An amount plus a s/min/h/day unit selector; the value that flows through
`onChange` is always seconds.

```tsx
import { DurationField } from "./DurationField";

<DurationField label="Wait before escalation" seconds={waitSeconds} onChange={setWaitSeconds} />
```

## EnumSelect

A dropdown over the model's enum that always keeps the release's current value
selectable, even once the schema no longer lists it (`allowUnknown={false}` turns
that state into a visible error instead of accepting it silently).

```tsx
import { EnumSelect } from "./EnumSelect";

<EnumSelect
  label="Derivation order"
  value={order}
  onChange={setOrder}
  options={[{ value: "FIFO", label: "First in, first out" }, { value: "LIFO", label: "Last in, first out" }]}
/>
```

## TagListInput

A string array as chips. Add on Enter or comma; each chip is one focusable
control -- click or Enter removes it, Alt+Left/Right reorders it, Backspace on an
empty draft removes the last chip.

```tsx
import { TagListInput } from "./TagListInput";

<TagListInput label="Ship via codes" values={codes} onChange={setCodes} suggestions={["CPU", "XPW"]} />
```

## OrderedList

A reorderable list -- stage sequences, status ladders, provider order. Up/down
buttons are the real, always-operable mechanism; drag-and-drop is layered on top
for a mouse.

```tsx
import { OrderedList } from "./OrderedList";

<OrderedList
  label="Stage sequence"
  items={stages}
  onChange={setStages}
  keyOf={(stage) => stage.id}
  renderItem={(stage) => <span>{stage.name}</span>}
/>
```

## KeyValueTable

The mapping editor for objects whose keys are data, not schema (`ship_via_methods`,
`dependencies`, `tasks`): add, rename in place (with duplicate/pattern detection),
delete, a value control per `valueKind`. Insertion order is preserved unless
`sortable` is set. Reuses `JsonValue`/`JsonRecord` from `../../api/mergePatch`
rather than a third copy of that recursive type.

```tsx
import { KeyValueTable, type KeyValueEntry } from "./KeyValueTable";

<KeyValueTable
  label="Ship via methods"
  entries={entries} // KeyValueEntry[] -- [{ key: "CPU", value: "COUNTER" }, ...]
  onChange={setEntries}
  valueKind="string"
/>
```

This is also what `DocumentEditor` renders for any path listed in its
`dataKeyedPaths` prop, instead of one box per key.

## PathPicker

A source path, autocompleted from the active schema's known paths via
`<datalist>` but never limited to them -- free text is always accepted, since a
release can carry a binding the schema has not been re-synced to yet.

```tsx
import { PathPicker } from "./PathPicker";

<PathPicker label="Source path" value={path} onChange={setPath} paths={knownPaths} />
```

## DiffPreview

What Publish will actually send: `mergePatchOf(before, after)` (from
`../../api/mergePatch`), flattened into one row per changed path, deletions
flagged rather than shown as "changed to null". Says "Nothing changed" when the
patch is empty.

```tsx
import { DiffPreview } from "./DiffPreview";

<DiffPreview before={loadedDocument} after={draftDocument} title="Policy evaluation changes" />
```

## PublishBar

The sticky footer every typed screen ends with: staged-change count, an optional
Validate step, Publish (disabled with a visible reason when there is nothing to
publish or the operator lacks the capability), and the release's step progress via
the existing `PublishProgress`.

```tsx
import { PublishBar } from "./PublishBar";

<PublishBar
  dirtyCount={dirtyCount}
  onValidate={runValidate}
  onPublish={runPublish}
  steps={steps}
  disabledReason={canPublish ? undefined : "config.release.promote is required"}
/>
```

## ValidationErrors

The page-level list for a backend-reported error that a form could not place next
to a field. `DocumentEditor` uses this for any `errors` path that does not resolve
in the current document.

```tsx
import { ValidationErrors } from "./ValidationErrors";

<ValidationErrors
  errors={[{ path: "workflow.stages", message: "must not be empty" }]}
  onJump={(path) => scrollToField(path)}
/>
```

## Notes

### Error path convention

`DocumentEditor`'s `errors` prop, and any future `POST /api/config/validate/{domain}`
producer (CFG-3a/4/5), should use dot-plus-index paths: `fields.3.priority`, not
`fields[3].priority`. Internally every generated field's location is built that way
(`childPath` only ever joins with `.`, including through arrays -- an array item's
segment is its index as a plain string), so a dotted path is matched directly.

A bracket-index path is still accepted: `DocumentEditor` normalises `foo[3]` to
`foo.3` (`normalizeErrorPath`) before matching, since a validator built from pydantic
`loc` tuples is at least as likely to emit that shape. Both spellings reach the same
field; only the dotted form is guaranteed to match a `.`-free key exactly (a
data-keyed key that itself contains a `.`, e.g. `ship_via_methods["UPS.Ground"]`, is
not addressable by either convention -- a documented limit, not a bug, since it would
require escaping rules on operator-entered keys).

### Data-keyed key ordering is not preserved through a plain JS object

`KeyValueTable`'s own `entries` array preserves insertion order (or `sortable`
order) faithfully. But `DocumentEditor`'s `dataKeyedPaths` rendering has to turn
that array back into a plain `JsonObject` to write it into the document, and
JavaScript's own property-enumeration rules reorder **integer-like** string keys
(`"0"`, `"2"`, `"10"`, ...) to the front, in ascending numeric order, ahead of every
other key, regardless of insertion order -- `Object.entries`, `JSON.stringify`, and
every other object consumer see it that way, not just this editor. A `tasks` or
`dependencies` map keyed by small integers will round-trip with those keys moved to
the front: `{b: "x", a: "y"}` plus `10` then `2` submits as
`{"2": ..., "10": ..., "b": "x", "a": "y"}`.

This is cosmetic, not a correctness problem: a merge patch (`mergePatchOf`, what
every publish path actually sends) is a JSON object, and object key order is not
semantically significant to the backend or to JSON equality. Do not build a UI or a
test that depends on the *submitted* key order of a data-keyed object; `KeyValueTable`
row order is the only order that is meaningful, and it is preserved.
