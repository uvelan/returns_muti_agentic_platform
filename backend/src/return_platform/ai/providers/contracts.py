"""Stable provider contracts shared by all AI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """Normalized provider failure that is safe to persist and expose."""

    def __init__(self, code: str, message: str = "AI provider request failed") -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    system_prompt: str
    user_payload: dict[str, Any]
    max_output_tokens: int | None = None
    temperature: float = 0.0
    response_schema: dict[str, Any] | None = None

    #: Which task this request belongs to, for providers that must say so.
    #:
    #: Model providers have no use for it. A human-in-the-loop provider does: it
    #: is built once per *route*, at `build_routes` time, while one route can
    #: serve several tasks -- so a provider-construction-time task id is
    #: necessarily wrong for all but one of them, and an operator answering a
    #: held request sees the wrong label or none. The dispatcher already knows
    #: the answer per invocation (`DispatchRequest.task_id`); this carries it the
    #: one hop that was missing.
    #:
    #: Optional because every existing caller predates it and a model provider
    #: that never reads it must not be forced to supply one.
    task_id: str | None = None


#: The third response identity, and the reason it is a *third* one.
#:
#: The platform already distinguishes two: a model answered (`provider` names the
#: model), or a human answered in the model's place (`MANUAL` /
#: `manual-human-v1`). A response a human *edited* is neither. Labelling it with
#: the originating model would let an evaluation set absorb human edits as model
#: quality; labelling it `MANUAL` would lose that a model produced the substance.
#:
#: So it gets its own provider name, and `HumanEdit` carries the origin. The
#: choice of which half goes in `provider` is deliberate and is about how a
#: consumer that has never heard of this feature behaves: with `HUMAN_EDITED`
#: there, a query for `provider == "GOOGLE"` excludes the edited row and a query
#: for `provider == "MANUAL"` excludes it too, so it falls out of both sets
#: rather than silently into one. Putting the origin in `provider` and the edit
#: in a field would have made the *default* reading "pure model output", which is
#: precisely the misattribution this exists to prevent.
HUMAN_EDITED_PROVIDER = "HUMAN_EDITED"
HUMAN_EDITED_MODEL = "human-edited-v1"


@dataclass(frozen=True, slots=True)
class HumanEdit:
    """A model produced the substance; a named human changed it.

    Both digests are recorded rather than either text: `origin_digest` is the
    model's output exactly as it arrived, `delivered_digest` is what the caller
    actually received. Equal digests would mean an edit that changed nothing;
    different ones prove an edit happened and let a later audit line the two up
    against the sealed interception record, which is where the actual text lives
    (sealed, because a response can carry the same customer rows the prompt did).
    """

    origin_provider: str
    origin_model: str
    origin_digest: str
    delivered_digest: str
    edited_by: str
    interception_id: str


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider: str
    model: str
    text: str
    # `input_tokens` is the prompt the provider actually processed -- the
    # *uncached* part -- and `cached_input_tokens` is what it served from a
    # prompt cache. They are separate because they are billed at different
    # rates, and adapters normalise to this split rather than passing through
    # whichever convention their vendor uses: Anthropic reports cache reads as a
    # sibling of `input_tokens`, OpenAI reports them as a subset of the prompt
    # count, and adding a subset to its own superset doubles the bill.
    #
    # `None` means the provider said nothing about caching, which is not the
    # same as "nothing was cached" -- it is why this is not defaulted to 0.
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    #: Set only when a human modified this response after the provider produced
    #: it, in which case `provider`/`model` are `HUMAN_EDITED`/`human-edited-v1`
    #: and this names the model whose answer was edited. `None` -- the default,
    #: and the only value any provider adapter ever produces -- means the text is
    #: exactly what `provider` returned.
    #:
    #: The token counts are deliberately *not* recomputed for an edit. They are
    #: what the provider billed for its own output; a human rewriting the text
    #: afterwards does not change the invoice, and adjusting them would make the
    #: cost column disagree with the bill in order to make it agree with the
    #: text.
    human_edit: HumanEdit | None = None


class AIProvider(Protocol):
    name: str
    model: str

    @property
    def configured(self) -> bool: ...

    async def generate(self, request: ProviderRequest) -> ProviderResponse: ...
