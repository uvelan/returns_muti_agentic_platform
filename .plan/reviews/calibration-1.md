# RV review — calibration, round 1

- **Branch:** `rv-calibration/seeded-hardcoding` (commit `78090cd`)
- **Base:** `a50c5500788f99e909f23099a81731b37c736b8c`
- **Diff reviewed:** `git diff a50c550..rv-calibration/seeded-hardcoding` — one new file, `backend/src/return_platform/operations/support_template_fields.py` (+18)
- **Reviewer:** RV
- **Date:** 2026-08-30

## Verdict: CHANGES_REQUIRED

## Findings

### F1 (BLOCKING) — Hardcoded template field binding in code

- **File:** `backend/src/return_platform/operations/support_template_fields.py`
- **Lines:** 11 (`CUSTOMER_NAME_SOURCE_PATH = "customerOutboundCDM.contact.fullName"`) and 16–17 (`.get("customerOutboundCDM", {}).get("contact", {})`, `.get("fullName")`)
- **Rule violated:** Blocking rule 1 — Hardcoding ("any field … literal in code instead of config"); contracts.md §8 (template config & renderer).
- **Why it matters:** Under the frozen contract, every template field is declared in `SupportTemplateConfiguration` as `field{field_id, label, source_binding, …}` with `source_binding` of the form `case_fact:<factName>` / `return_record:<attr>` / `graph:<path>`, riding the release lifecycle and pinned by the case's `configurationReleaseId`. This helper bakes the customer-name source path into code, so (a) the field cannot be re-bound, reformatted, or removed by a config release — it silently escapes the template version pin; (b) a missing value returns `None` instead of the declared `fallback` / `TemplateGap{field_id, reason}` path, so a required-field gap would never block review; (c) rendered-field provenance (`{source, source_path, fact_id?}`) is bypassed. The renderer with a config binding (`graph:customerOutboundCDM.contact.fullName` or equivalent) is the only permitted mechanism.
- **Aggravating detail:** lines 16–17 re-spell the same path as three separate inline string literals; the module-level constant on line 11 is not even used by the function, so the path is hardcoded twice over.

### F2 (non-blocking, folds into F1 remediation) — Dead code

- **File:** same file, line 11.
- **Rule:** standard dimensions (dead code). `CUSTOMER_NAME_SOURCE_PATH` has no reader. Moot once F1 is fixed by deleting the helper in favor of a config binding.

## Standing greps (this round)

- Fact-name string literals outside `operations/fact_names.py`: none in diff.
- New imports of frozen modules (`operations/associate_flow`, `agents/order_discovery`, `api/associate_returns`, `api/return_agents`): none.
- Template/section/intent/tool literals in code: **hit** — see F1.

## Required action

Delete `support_template_fields.py`; express the customer-name field as a `SupportTemplateConfiguration` field entry with a declarative `source_binding` (and `fallback`/`required` as appropriate) in the template config module + `production.yaml` default block, resolved by the renderer.
