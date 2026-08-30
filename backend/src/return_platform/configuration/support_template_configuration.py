"""Support-template configuration: the constrained grammar (contracts.md sect. 8).

The template a support handoff renders under is released configuration, pinned
per case by `configurationReleaseId` like every other section of
`ReturnPlatformConfiguration`. What may appear in it is deliberately narrow --
**no scripting from config**:

- `formatter` is an id from the fixed code-side allowlist
  (`operations/template_formatters.FORMATTER_IDS`), never an expression.
- `selector` and `visibility_rule` are declarative clause structs; all clauses
  a rule states must match, the first matching variant wins, otherwise the
  default variant renders.
- `subject_template` interpolates `{field_id}` only; an id the variant does
  not declare is refused at release validation, and literal braces are
  written `{{` / `}}`.
- `source_binding` names one source: `case_fact:<factName>`,
  `return_record:<attr>`, `graph:<path>`, or `literal:<text>`. The first
  three resolve against the case at render time (contracts.md sect. 8 binding
  rules); `literal:` is constant text -- the banner and fixed action lines of
  today's handoff, which no case-bound source could produce. It resolves to
  itself, can never gap, and carries no expression grammar of any kind.
- `return_record:<attr>` names **an attribute the record projection declares**
  and nothing else (contracts.md AMENDMENT-2). The allowlist is derived from
  `ReturnRecordProjection.model_fields`, so it cannot drift from the projection
  it describes, and an undeclared name is a refused release rather than a
  reach into an open namespace -- `return_record:__class__` used to resolve to
  a class object and render `<class '...'>` into a message a person reads.

Everything here is `extra="forbid"`: an unknown key in a release is a refused
release, not a silently ignored one.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from return_platform.operations.template_formatters import FORMATTER_IDS

__all__ = [
    "BINDING_SOURCES",
    "SupportTemplateConfiguration",
    "TemplateFieldConfiguration",
    "TemplateRuleConfiguration",
    "TemplateSectionConfiguration",
    "TemplateVariantConfiguration",
    "binding_source",
    "record_attributes",
    "subject_placeholders",
]

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]

#: The closed set of binding source prefixes. Order is documentation only.
BINDING_SOURCES: Final[tuple[str, ...]] = ("case_fact", "return_record", "graph", "literal")


@lru_cache(maxsize=1)
def record_attributes() -> frozenset[str]:
    """Every attribute a `return_record:` binding may name (AMENDMENT-2).

    **Derived, never hand-listed:** the projection is the declaration, so a
    field added there becomes bindable in the same commit and a field removed
    there stops being bindable. A hand-copied list would be a second definition
    of the record's surface, and the copy is always the one that drifts.

    Imported inside the function because the projection package pulls the agent
    registry, which imports `return_configuration`, which imports this module --
    a real cycle, not a style preference. Cached, because `binding_source` runs
    per field per render.
    """
    from return_platform.operations.case_projection.contract import ReturnRecordProjection

    return frozenset(ReturnRecordProjection.model_fields)


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def binding_source(source_binding: str) -> tuple[str, str]:
    """Split a binding into `(source, path)`, refusing an unknown source."""
    source, separator, path = source_binding.partition(":")
    if not separator or source not in BINDING_SOURCES:
        raise ValueError(
            f"unknown binding source in {source_binding!r}; "
            f"expected one of {', '.join(BINDING_SOURCES)}"
        )
    if not path.strip() and source != "literal":
        raise ValueError(f"binding {source_binding!r} names no path")
    if source == "return_record" and path not in record_attributes():
        # AMENDMENT-2. Refused here rather than at render time, so a template
        # that reaches outside the projection cannot become a release at all.
        raise ValueError(
            f"return_record:{path} names no attribute the record projection declares; "
            f"allowed: {', '.join(sorted(record_attributes()))}"
        )
    return source, path


def _declared_source(source_binding: str) -> str | None:
    """The binding's source prefix, or `None` when it is not a valid binding.

    `per_record` asks a structural question about a template that has already
    been validated, so a refusal is unreachable there -- but it is asked while
    *planning* a render, before any field is resolved, and a plan that raises
    would take down the whole handoff over one bad field. So the question is
    answered rather than refused, and the refusal happens per field, where it
    can become a gap.
    """
    source, separator, _ = source_binding.partition(":")
    return source if separator and source in BINDING_SOURCES else None


def subject_placeholders(subject_template: str) -> tuple[str, ...]:
    """The `{field_id}` placeholders a subject interpolates, escapes honoured.

    `{{` and `}}` are literal braces. A lone `{` or `}` is refused: a subject
    that renders differently from how it reads is a subject nobody can review.
    """
    placeholders: list[str] = []
    index = 0
    text = subject_template
    while index < len(text):
        char = text[index]
        if char == "{":
            if text[index + 1 : index + 2] == "{":
                index += 2
                continue
            closing = text.find("}", index + 1)
            if closing == -1:
                raise ValueError(f"unclosed placeholder in subject {subject_template!r}")
            name = text[index + 1 : closing]
            if not name or "{" in name:
                raise ValueError(f"malformed placeholder in subject {subject_template!r}")
            placeholders.append(name)
            index = closing + 1
            continue
        if char == "}":
            if text[index + 1 : index + 2] == "}":
                index += 2
                continue
            raise ValueError(f"unmatched '}}' in subject {subject_template!r}")
        index += 1
    return tuple(placeholders)


class TemplateRuleConfiguration(StrictConfigModel):
    """One declarative clause struct, used for both selectors and visibility.

    Every clause present must match. A list clause matches when the context
    offers at least one value and every offered value is in the list -- so a
    mixed parcel-and-LTL render matches neither a parcel-only nor an LTL-only
    variant and falls to the default, rather than one class's instructions
    being sent about the other's freight. A rule with **no** clauses at all
    matches nothing: it declares nothing, and a catch-all belongs to
    `default_variant_id`, not to whichever variant was listed first.
    """

    shipping_modes: tuple[NonBlank, ...] = ()
    return_reason_classes: tuple[NonBlank, ...] = ()
    order_sources: tuple[NonBlank, ...] = ()
    min_item_count: int | None = Field(default=None, ge=0)
    max_item_count: int | None = Field(default=None, ge=0)

    @property
    def declares_anything(self) -> bool:
        return bool(
            self.shipping_modes
            or self.return_reason_classes
            or self.order_sources
            or self.min_item_count is not None
            or self.max_item_count is not None
        )

    @model_validator(mode="after")
    def validate_bounds(self) -> TemplateRuleConfiguration:
        if (
            self.min_item_count is not None
            and self.max_item_count is not None
            and self.min_item_count > self.max_item_count
        ):
            raise ValueError("min_item_count exceeds max_item_count")
        return self


class TemplateFieldConfiguration(StrictConfigModel):
    """One rendered line: a binding, how it formats, and what failure means.

    `label` present renders `- <label>: <value>`; absent renders the formatted
    value verbatim (its own lines) -- the shape the item block and the fixed
    action bullets need. `fallback` applies only after the declared binding
    fails; a missing `required` field is a `TemplateGap`, never a blank.
    """

    field_id: NonBlank
    label: NonBlank | None = None
    source_binding: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1)]
    required: bool = False
    fallback: str | None = None
    formatter: NonBlank = "text"
    visibility_rule: TemplateRuleConfiguration | None = None

    @model_validator(mode="after")
    def validate_field(self) -> TemplateFieldConfiguration:
        binding_source(self.source_binding)
        if self.formatter not in FORMATTER_IDS:
            raise ValueError(
                f"unknown formatter {self.formatter!r} on field {self.field_id!r}; "
                f"allowed: {', '.join(sorted(FORMATTER_IDS))}"
            )
        return self


class TemplateSectionConfiguration(StrictConfigModel):
    """One titled block of fields.

    A section holding any `return_record:` binding is a **per-record**
    section: it renders once per return record, stamped with that record's
    `return_record_id`, and its `case_fact:` bindings read that record's
    scoped facts. The distinction is structural -- derived from the bindings,
    never a separate flag that could disagree with them.
    """

    section_id: NonBlank
    title: NonBlank | None = None
    fields: tuple[TemplateFieldConfiguration, ...] = ()
    visibility_rule: TemplateRuleConfiguration | None = None

    @property
    def per_record(self) -> bool:
        return any(_declared_source(field.source_binding) == "return_record" for field in self.fields)


class TemplateVariantConfiguration(StrictConfigModel):
    variant_id: NonBlank
    selector: TemplateRuleConfiguration = Field(default_factory=TemplateRuleConfiguration)
    subject_template: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    sections: tuple[TemplateSectionConfiguration, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_variant(self) -> TemplateVariantConfiguration:
        section_ids = [section.section_id for section in self.sections]
        duplicate_sections = {s for s in section_ids if section_ids.count(s) > 1}
        if duplicate_sections:
            raise ValueError(
                f"variant {self.variant_id!r} repeats section ids: "
                f"{', '.join(sorted(duplicate_sections))}"
            )
        field_ids = [
            field.field_id for section in self.sections for field in section.fields
        ]
        duplicate_fields = {f for f in field_ids if field_ids.count(f) > 1}
        if duplicate_fields:
            raise ValueError(
                f"variant {self.variant_id!r} repeats field ids: "
                f"{', '.join(sorted(duplicate_fields))}"
            )
        # A subject is one line for the whole request, and a per-record field
        # has one value per record -- so a subject naming one would have to pick
        # a record, and "the last one rendered" is not a decision a template
        # gets to make silently. It is refused here, where the operator can see
        # why, rather than resolved arbitrarily at render time.
        case_level_ids = {
            field.field_id
            for section in self.sections
            if not section.per_record
            for field in section.fields
        }
        placeholders = subject_placeholders(self.subject_template)
        unknown = [
            placeholder for placeholder in placeholders if placeholder not in set(field_ids)
        ]
        if unknown:
            raise ValueError(
                f"variant {self.variant_id!r} subject interpolates unknown field ids: "
                f"{', '.join(unknown)}"
            )
        per_record = [
            placeholder
            for placeholder in placeholders
            if placeholder not in case_level_ids
        ]
        if per_record:
            raise ValueError(
                f"variant {self.variant_id!r} subject interpolates per-record field ids: "
                f"{', '.join(per_record)}; a request covering several records has one "
                f"subject and would have to pick a record"
            )
        return self


class SupportTemplateConfiguration(StrictConfigModel):
    """The released template: variants, and which one renders when none match."""

    template_id: NonBlank = "support-handoff"
    variants: tuple[TemplateVariantConfiguration, ...] = ()
    default_variant_id: NonBlank = "default"

    @model_validator(mode="after")
    def validate_template(self) -> SupportTemplateConfiguration:
        if not self.variants:
            # The defaulted empty block: a release cut before templates
            # existed still loads, and the renderer falls back to the
            # un-patched composition path.
            return self
        variant_ids = [variant.variant_id for variant in self.variants]
        duplicates = {v for v in variant_ids if variant_ids.count(v) > 1}
        if duplicates:
            raise ValueError(f"duplicate variant ids: {', '.join(sorted(duplicates))}")
        if self.default_variant_id not in set(variant_ids):
            raise ValueError(
                f"default_variant_id {self.default_variant_id!r} names no variant"
            )
        return self

    def default_variant(self) -> TemplateVariantConfiguration | None:
        for variant in self.variants:
            if variant.variant_id == self.default_variant_id:
                return variant
        return None
