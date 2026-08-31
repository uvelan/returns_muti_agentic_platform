"""Render one support-template draft from released configuration (contracts.md sect. 8).

## What a render is

One template + one case's state -> `RenderedTemplate`: the subject, the text a
person reads, the per-field provenance a screen reads, and the `TemplateGap`s
that block review. The renderer is pure over its input -- every value arrives
in `TemplateDraftInput`, so a render is testable without a platform and a
field cannot quietly acquire a second source.

## Variant selection

First variant whose selector matches wins; no match renders the default. A
selector with no clauses matches nothing (see
`TemplateRuleConfiguration`) -- the catch-all position belongs to
`default_variant_id` alone.

## Binding, per source

- `case_fact:<name>` reads the scoped-fact mapping the caller assembled from
  `latest_case_facts_scoped` (S1): case-level sections read the `None`
  partition, per-record section groups read that record's partition -- never
  each other's. Entries are `{"value": ..., "factId": ...?}`; the assembler
  wraps snapshot values it derives (case id, workflow status at handoff, the
  selected-lines list) the same way, with no `factId`, so provenance says
  which lines trace to the fact log and which to the draft-time snapshot.
- `return_record:<attr>` reads one attribute off that record's projection
  (`ReturnRecordProjection` or an equivalent mapping); only meaningful inside
  a per-record section, which is any section holding such a binding. The
  attribute must be one the projection **declares** (contracts.md
  AMENDMENT-2): the reach is the projection's surface, not an open namespace.
- `graph:<source>/<path>` reads through `SupportTemplateGraphPort`. Missing
  values are **batched**: the render collects every missing graph binding,
  issues one `synchronize(source, paths)` per source, then retries them all
  together. The production port adapter wraps
  `OnDemandSyncCoordinator.synchronize`
  (`dynamic_knowledge/on_demand_sync/coordinator.py`) and must spend exactly
  one coordinator call per `synchronize` invocation -- that is the whole
  contract the batching exists for.
- `literal:<text>` is the text, verbatim. It cannot fail and cannot gap.

`fallback` applies only after the declared binding fails (absent value, a
formatter refusal, or a binding release validation would have refused -- which
should be unreachable, and is answered as a field-level failure rather than an
exception that takes the whole handoff down). A `required` field that still has nothing is a
`TemplateGap{field_id, reason}` -- review-blocking, and the only place gaps
come from. A non-required field with no fallback simply does not render,
because printing a guessed value is the failure mode this whole module
replaces.

## Re-rendering

`TemplateRenderCache` lives for one draft. It memoizes graph reads and the
sources already synchronized, so an edit-driven re-render never repeats a
sync for a path the draft has already chased.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Final, Protocol

from return_platform.configuration.support_template_configuration import (
    SupportTemplateConfiguration,
    TemplateFieldConfiguration,
    TemplateRuleConfiguration,
    TemplateSectionConfiguration,
    TemplateVariantConfiguration,
    binding_source,
    record_attributes,
)
from return_platform.operations.template_formatters import (
    UNAVAILABLE,
    TemplateFormatterError,
    format_value,
)

__all__ = [
    "RenderedField",
    "RenderedSection",
    "RenderedTemplate",
    "SupportTemplateGraphPort",
    "TemplateDraftInput",
    "TemplateGap",
    "TemplateNotConfiguredError",
    "TemplateRenderCache",
    "TemplateRenderContext",
    "render_support_template",
    "select_variant",
]


class TemplateNotConfiguredError(RuntimeError):
    """The release carries no variants; the caller falls back to the
    un-patched `compose_support_handoff` path."""


class SupportTemplateGraphPort(Protocol):
    """The renderer's whole view of the dynamic graph.

    `read` returns the value at one `graph:` path or `None`. `synchronize`
    receives one source and every missing path under it, **once per source
    per render** -- the adapter maps that to exactly one
    `OnDemandSyncCoordinator.synchronize` call.
    """

    async def read(self, path: str) -> Any: ...

    async def synchronize(self, source: str, paths: Sequence[str]) -> None: ...


@dataclass(frozen=True, slots=True)
class TemplateRenderContext:
    """What selectors and visibility rules are evaluated against."""

    shipping_modes: tuple[str, ...] = ()
    return_reason_classes: tuple[str, ...] = ()
    order_sources: tuple[str, ...] = ()
    item_count: int = 0


#: The absent-value marker inside the graph cache -- distinct from "never
#: read", which is simply not being in the cache.
_MISSING: Final = object()


@dataclass(slots=True)
class TemplateRenderCache:
    """Within-draft resolution memory. One instance per draft, shared across
    that draft's re-renders; never shared across drafts."""

    graph_values: dict[str, Any] = dataclass_field(default_factory=dict)
    synchronized_sources: set[str] = dataclass_field(default_factory=set)


@dataclass(frozen=True, slots=True)
class TemplateDraftInput:
    """Everything one render may read. Assembled by the caller, owned here.

    `facts` is keyed `(record_scope | None, fact_name)` exactly as
    `latest_case_facts_scoped` returns it; every entry is a mapping carrying
    at least `value` (and `factId` when it came off the fact log).
    `return_records` are `ReturnRecordProjection`s or equivalent mappings, in
    the order their section groups should render.
    """

    case_id: str
    context: TemplateRenderContext
    facts: Mapping[tuple[str | None, str], Mapping[str, Any]]
    return_records: Sequence[Any] = ()
    graph: SupportTemplateGraphPort | None = None


@dataclass(frozen=True, slots=True)
class TemplateGap:
    """A required field the case could not fill. Review-blocking; produced
    only here (panel degradation is never a gap)."""

    field_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RenderedField:
    field_id: str
    label: str | None
    value: str
    source: str
    source_path: str
    fact_id: str | None = None
    applied_fallback: bool = False


@dataclass(frozen=True, slots=True)
class RenderedSection:
    section_id: str
    title: str | None
    fields: tuple[RenderedField, ...]
    #: Stamped on every section of a per-record group; `None` on case-level
    #: sections. The stamp is what keeps two RMAs' sections from ever being
    #: read as one another's (multi-RMA integrity).
    return_record_id: str | None = None


@dataclass(frozen=True, slots=True)
class RenderedTemplate:
    template_id: str
    variant_id: str
    subject: str
    text: str
    sections: tuple[RenderedSection, ...]
    gaps: tuple[TemplateGap, ...]

    @property
    def review_blocked(self) -> bool:
        return bool(self.gaps)


def _rule_matches(rule: TemplateRuleConfiguration, context: TemplateRenderContext) -> bool:
    """All present clauses must match; a clause-less rule matches nothing."""
    if not rule.declares_anything:
        return False
    for offered, allowed in (
        (context.shipping_modes, rule.shipping_modes),
        (context.return_reason_classes, rule.return_reason_classes),
        (context.order_sources, rule.order_sources),
    ):
        if allowed:
            if not offered or not set(offered) <= set(allowed):
                return False
    if rule.min_item_count is not None and context.item_count < rule.min_item_count:
        return False
    if rule.max_item_count is not None and context.item_count > rule.max_item_count:
        return False
    return True


def _visible(rule: TemplateRuleConfiguration | None, context: TemplateRenderContext) -> bool:
    """No rule means always visible; a rule means it must match."""
    return rule is None or _rule_matches(rule, context)


def select_variant(
    template: SupportTemplateConfiguration, context: TemplateRenderContext
) -> TemplateVariantConfiguration:
    """First matching selector wins, else the default variant."""
    if not template.variants:
        raise TemplateNotConfiguredError(f"template {template.template_id!r} releases no variants")
    for variant in template.variants:
        if _rule_matches(variant.selector, context):
            return variant
    default = template.default_variant()
    if default is None:  # pragma: no cover - refused at release validation
        raise TemplateNotConfiguredError(
            f"template {template.template_id!r} names no default variant"
        )
    return default


def _record_id(record: Any) -> str:
    value = _record_attribute(record, "returnRecordId")
    return str(value) if value is not None else ""


def _record_attribute(record: Any, attribute: str) -> Any:
    """One declared projection attribute, and nothing else (AMENDMENT-2).

    The allowlist is checked here as well as at release validation, and not
    because a validated release can get past that check -- it cannot. It is
    checked here because this function is the only thing standing between a
    name and `getattr`, and a reader auditing the reach should be able to
    settle the question by reading this function rather than by proving that
    every caller upstream validated first. An undeclared name resolves to
    `None`, which the caller turns into an ordinary binding failure -- a gap
    or a fallback, never an exception escaping to the render.
    """
    if attribute not in record_attributes():
        return None
    if isinstance(record, Mapping):
        return record.get(attribute)
    return getattr(record, attribute, None)


def _record_context(record: Any, base: TemplateRenderContext) -> TemplateRenderContext:
    """The context one record's section group is judged by: its own shipping
    class and its own line count, never a sibling record's."""
    method = _record_attribute(record, "returnMethod")
    items = _record_attribute(record, "approvedItems") or ()
    return TemplateRenderContext(
        shipping_modes=(str(method),) if method is not None else (),
        return_reason_classes=base.return_reason_classes,
        order_sources=base.order_sources,
        item_count=len(items),
    )


@dataclass(frozen=True, slots=True)
class _Resolution:
    value: Any
    fact_id: str | None = None
    failure: str | None = None

    @property
    def failed(self) -> bool:
        return self.failure is not None


def _resolve_case_fact(draft: TemplateDraftInput, name: str, scope: str | None) -> _Resolution:
    entry = draft.facts.get((scope, name))
    if entry is None:
        where = f"record {scope}" if scope is not None else "the case"
        return _Resolution(None, failure=f"case_fact:{name} absent on {where}")
    value = entry.get("value")
    if value is None:
        return _Resolution(None, failure=f"case_fact:{name} carries no value")
    fact_id = entry.get("factId")
    return _Resolution(value, fact_id=str(fact_id) if fact_id is not None else None)


def _resolve_return_record(record: Any, attribute: str) -> _Resolution:
    if record is None:
        return _Resolution(None, failure=f"return_record:{attribute} outside a per-record section")
    value = _record_attribute(record, attribute)
    if value is None:
        return _Resolution(None, failure=f"return_record:{attribute} absent")
    return _Resolution(value)


def _graph_source(path: str) -> str:
    return path.split("/", 1)[0]


class _GraphReads:
    """One render's graph traffic: cached reads, then one batched retry.

    First pass: every `graph:` binding reads through the cache and misses are
    *collected*, not chased one by one. `retry_missing` then issues one
    `synchronize` per source for the paths still missing and re-reads them
    together -- the shape the acceptance test pins (N missing bindings, one
    sync per source).
    """

    def __init__(self, port: SupportTemplateGraphPort | None, cache: TemplateRenderCache):
        self._port = port
        self._cache = cache
        self.missing: list[str] = []
        self.retriable: set[str] = set()

    async def read(self, path: str) -> _Resolution:
        if self._port is None:
            return _Resolution(None, failure=f"graph:{path} has no graph port")
        cached = self._cache.graph_values.get(path, _MISSING)
        if cached is _MISSING:
            cached = await self._port.read(path)
            self._cache.graph_values[path] = cached
        if cached is None:
            # Retriable only when this draft has not already spent its one
            # sync on the path's source -- a re-render must not sync again.
            if _graph_source(path) not in self._cache.synchronized_sources:
                if path not in self.retriable:
                    self.missing.append(path)
                    self.retriable.add(path)
                return _Resolution(None, failure=f"graph:{path} unresolved")
            return _Resolution(None, failure=f"graph:{path} unresolved after sync")
        return _Resolution(cached)

    async def retry_missing(self) -> dict[str, _Resolution]:
        if self._port is None or not self.missing:
            return {}
        by_source: dict[str, list[str]] = {}
        for path in self.missing:
            by_source.setdefault(_graph_source(path), []).append(path)
        for source, paths in by_source.items():
            await self._port.synchronize(source, paths)
            self._cache.synchronized_sources.add(source)
        retried: dict[str, _Resolution] = {}
        for path in self.missing:
            value = await self._port.read(path)
            self._cache.graph_values[path] = value
            retried[path] = (
                _Resolution(value)
                if value is not None
                else _Resolution(None, failure=f"graph:{path} unresolved after sync")
            )
        return retried


@dataclass(slots=True)
class _PendingField:
    """A field whose graph binding missed on the first pass, parked until the
    batched retry decides it."""

    section_index: int
    field_index: int
    configuration: TemplateFieldConfiguration
    path: str


def _formatted(
    configuration: TemplateFieldConfiguration,
    resolution: _Resolution,
    source: str,
    path: str,
) -> tuple[RenderedField | None, TemplateGap | None]:
    """One resolved binding through its formatter, fallback and gap rules."""
    failure = resolution.failure
    value: str | None = None
    if not resolution.failed:
        try:
            value = format_value(configuration.formatter, resolution.value)
        except TemplateFormatterError as refusal:
            failure = f"{source}:{path} {refusal}"
    if value is not None:
        return (
            RenderedField(
                field_id=configuration.field_id,
                label=configuration.label,
                value=value,
                source=source,
                source_path=path,
                fact_id=resolution.fact_id,
            ),
            None,
        )
    if configuration.fallback is not None:
        return (
            RenderedField(
                field_id=configuration.field_id,
                label=configuration.label,
                value=configuration.fallback,
                source=source,
                source_path=path,
                applied_fallback=True,
            ),
            None,
        )
    if configuration.required:
        return None, TemplateGap(
            field_id=configuration.field_id, reason=failure or f"{source}:{path} unresolved"
        )
    return None, None


def _render_subject(subject_template: str, values: Mapping[str, str]) -> str:
    """`{field_id}` interpolation with `{{`/`}}` escapes, nothing else."""
    parts: list[str] = []
    index = 0
    text = subject_template
    while index < len(text):
        char = text[index]
        if char == "{" and text[index + 1 : index + 2] == "{":
            parts.append("{")
            index += 2
            continue
        if char == "}" and text[index + 1 : index + 2] == "}":
            parts.append("}")
            index += 2
            continue
        if char == "{":
            closing = text.find("}", index + 1)
            if closing == -1:  # refused at validation; never trusted here
                parts.append(text[index:])
                break
            name = text[index + 1 : closing]
            parts.append(values.get(name, UNAVAILABLE))
            index = closing + 1
            continue
        parts.append(char)
        index += 1
    return "".join(parts)


def _text_of(sections: Sequence[RenderedSection]) -> str:
    blocks: list[str] = []
    for section in sections:
        lines: list[str] = []
        if section.title is not None:
            lines.append(section.title)
        for rendered in section.fields:
            if rendered.label is not None:
                lines.append(f"- {rendered.label}: {rendered.value}")
            else:
                lines.extend(rendered.value.splitlines() or [""])
        if lines:
            blocks.append("\n".join(lines))
    return ("\n\n".join(blocks)).rstrip() + "\n"


async def render_support_template(
    template: SupportTemplateConfiguration,
    draft: TemplateDraftInput,
    *,
    cache: TemplateRenderCache | None = None,
) -> RenderedTemplate:
    """The public render: draft input in, `RenderedTemplate` (and its gaps) out.

    Pass the same `cache` for every re-render of one draft; leave it `None`
    for a one-shot render.
    """
    cache = cache if cache is not None else TemplateRenderCache()
    variant = select_variant(template, draft.context)
    graph = _GraphReads(draft.graph, cache)

    # The flat render plan: case-level sections once, per-record sections once
    # per record, each judged by its own context.
    planned: list[tuple[TemplateSectionConfiguration, Any, TemplateRenderContext]] = []
    for section in variant.sections:
        if section.per_record and draft.return_records:
            for record in draft.return_records:
                planned.append((section, record, _record_context(record, draft.context)))
        else:
            planned.append((section, None, draft.context))

    sections: list[tuple[TemplateSectionConfiguration, Any, list[RenderedField | None]]] = []
    pending: list[_PendingField] = []
    gaps: list[TemplateGap] = []

    for section, record, context in planned:
        if not _visible(section.visibility_rule, context):
            continue
        rendered_fields: list[RenderedField | None] = []
        actual_index = len(sections)
        for field_configuration in section.fields:
            if not _visible(field_configuration.visibility_rule, context):
                continue
            try:
                source, path = binding_source(field_configuration.source_binding)
            except ValueError as refusal:
                # A binding release validation would have refused. Reaching it
                # here means something bypassed that gate, and the answer is a
                # field-level failure -- a gap, or a fallback -- rather than an
                # exception that takes the whole handoff down with it. The
                # render still says, per field, that it could not resolve.
                rendered, gap = _formatted(
                    field_configuration,
                    _Resolution(None, failure=str(refusal)),
                    "binding",
                    field_configuration.source_binding,
                )
                if gap is not None:
                    gaps.append(gap)
                rendered_fields.append(rendered)
                continue
            if source == "literal":
                resolution = _Resolution(path)
            elif source == "case_fact":
                scope = _record_id(record) if record is not None else None
                resolution = _resolve_case_fact(draft, path, scope or None)
            elif source == "return_record":
                resolution = _resolve_return_record(record, path)
            else:  # graph
                resolution = await graph.read(path)
                if resolution.failed and path in graph.retriable:
                    # Parked for the batched retry; decided after all sources
                    # have had their one synchronize.
                    pending.append(
                        _PendingField(
                            section_index=actual_index,
                            field_index=len(rendered_fields),
                            configuration=field_configuration,
                            path=path,
                        )
                    )
                    rendered_fields.append(None)
                    continue
            rendered, gap = _formatted(field_configuration, resolution, source, path)
            if gap is not None:
                gaps.append(gap)
            rendered_fields.append(rendered)
        sections.append((section, record, rendered_fields))

    retried = await graph.retry_missing()
    for parked in pending:
        resolution = retried.get(
            parked.path, _Resolution(None, failure=f"graph:{parked.path} unresolved")
        )
        rendered, gap = _formatted(parked.configuration, resolution, "graph", parked.path)
        if gap is not None:
            gaps.append(gap)
        sections[parked.section_index][2][parked.field_index] = rendered

    finished: list[RenderedSection] = []
    for section, record, rendered_fields in sections:
        finished.append(
            RenderedSection(
                section_id=section.section_id,
                title=section.title,
                fields=tuple(f for f in rendered_fields if f is not None),
                return_record_id=(_record_id(record) or None) if record is not None else None,
            )
        )

    # Case-level sections only. A per-record group renders the same `field_id`
    # once per record, so flattening every section made the subject state
    # whichever RMA happened to render last -- for a request covering several.
    # Release validation refuses such a subject outright; this is what keeps the
    # render from resolving one arbitrarily if it ever sees one anyway.
    values = {
        rendered.field_id: rendered.value
        for section in finished
        if section.return_record_id is None
        for rendered in section.fields
    }
    return RenderedTemplate(
        template_id=template.template_id,
        variant_id=variant.variant_id,
        subject=_render_subject(variant.subject_template, values),
        text=_text_of(finished),
        sections=tuple(finished),
        gaps=tuple(gaps),
    )
