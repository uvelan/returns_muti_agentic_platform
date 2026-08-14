# Dynamic identification fields

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The requirement

> Adding the tenth identification field must require zero Python edits.

Colour and ZIP are ordinary configured fields. They are not special cases and
there is no code path that names them.

## What was wrong

Seven separate sites hardcoded the field list: the anchor tuple, the ranking
weights, the clarification order, the search planner, the intent schema, the
frontend anchor selector, and the AI prompt. Adding a field meant editing all
seven, in three languages, and getting them consistent. Anyone who edited six of
seven produced a field the agent could accept and never search on.

## What exists now

One catalogue: `discovery.identification_fields`, a tuple of
`IdentificationFieldConfiguration` in
`backend/src/return_platform/configuration/return_configuration.py`.

It is a runtime configuration release. Publishing a release that adds a field
gives the agent a new searchable signal — no Python, no TypeScript, no prompt
edit, no deployment.

The empty tuple is legal and means "this deployment has configured no
identification fields", which the agent reports rather than papering over with a
built-in list. **A hardcoded fallback here is exactly the defect the catalogue
exists to remove.**

## One field definition

| Key | Meaning |
|---|---|
| `field_id` | Stable identity. Unique across the catalogue (validated). |
| `intent_key` | The key the AI intent schema uses. Unique across the catalogue (validated). |
| `entity` | Which graph entity the field identifies. |
| `label` | What an operator sees. |
| `description` | Free text for the operator and the model. |
| `aliases` | Other words an associate or the model may use. Shown to the model, so a newly configured field is recognizable **without a prompt edit**. |
| `value_type` | Scalar kind, including `DATE_LOWER_BOUND`, `DATE_UPPER_BOUND`, `DATE_POINT`. |
| `multiple` | Whether several values may be supplied. A date bound cannot be `multiple` (validated). |
| `normalization` | `NONE`, `LOWER_ALPHANUMERIC`, `DIGITS` or `TRIM`. Applied before ranking comparison, **never** before the value goes to the graph — the graph gets the form each search asks for. |
| `validation_pattern` | A value failing this is reported invalid rather than searched, so a mistyped ZIP does not silently return nothing. |
| `sensitivity` | `NONE`, `CONTACT` or `PERSONAL`. Read by clarification and redaction policy. Nothing here decides disclosure by itself. |
| `ranking_weight_millionths` | What a match on this field is worth when candidates are ranked. |
| `exact_match_bonus_millionths` | The extra an exact match is worth over a partial one. |
| `clarification_priority` | How badly this field is wanted when the agent must ask for something. |
| `searches` | The search strategies this field supports. |

Weights are integers in millionths rather than floats, because integers compare
and serialize identically across Python, JSON, Neo4j and TypeScript and floats do
not.

## Validation the model enforces

- `field_id` values are unique.
- `intent_key` values are unique.
- A date-bound field cannot be `multiple`.
- A `FULLTEXT` search cannot declare `narrow_with` — the index *is* the
  predicate, and narrowing it would reintroduce the bounded-corpus defect that
  [`../optimization/order-discovery-search.md`](../optimization/order-discovery-search.md)
  exists to prevent.

Each of these is a rule an operator would otherwise have to remember. They fail
at release validation, before publication, not at search time in front of an
associate.

## How each consumer reads it

| Consumer | How |
|---|---|
| Search planning | `search_strategy.py` builds plans from `searches`, per field |
| Ranking | weights read from the field, not from a parallel table |
| Clarification | ordered by `clarification_priority` and selectivity |
| AI intent schema | `contextJson.identification_fields` is the authoritative list of the signals this deployment can extract — see `backend/config/ai_gateway.yaml` |
| Agent node | `identification_fields=tuple(deps.identification.describe())` at `graph_nodes.py:311` |
| Runtime wiring | `dynamic_knowledge/integration/runtime_factory.py:211` |

The prompt reads the catalogue rather than listing fields, which is what makes
"no prompt edit" true rather than aspirational.

## Adding a field

1. Clone the active configuration release.
2. Append an `IdentificationFieldConfiguration` to
   `discovery.identification_fields`.
3. Give it at least one entry in `searches`. A field with no searches is
   accepted, extracted and never searched on — legal, and almost never what was
   meant.
4. If it should count as a strong anchor, add its `field_id` to
   `discovery.strong_anchors`.
5. Validate the release. The uniqueness and search-shape rules above run here.
6. Publish against the current head revision.
7. Confirm adoption: `GET /api/config/adoption` must report `LIVE`, not
   `ACTIVATING`. See [`configuration-adoption.md`](configuration-adoption.md).

Step 7 is not optional bookkeeping. Until every required process class reports
the new release, some workers are still searching on the old catalogue.

## Related

- [`order-discovery.md`](order-discovery.md)
- [`configuration-adoption.md`](configuration-adoption.md)
- [`../configuration/families.md`](../configuration/families.md)
