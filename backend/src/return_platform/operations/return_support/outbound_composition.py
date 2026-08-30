"""Composing the messages this slice writes, without letting text restructure them.

Three messages, and every one of them carries words a *person* typed:

* the **clarification prompt** shown to the branch associate, which carries the
  support question verbatim (contracts.md sect. 9);
* the **clarification relay** back to Support, which carries the associate's
  answer;
* the **reply draft**, which answers a support question on the case's behalf.

That makes this module the highest-risk composition surface in the slice, and
it is built to the carry-forward condition V1 phase 1 raised.

## What V1 found, and the rule that follows from it

V1 bound a raw `associate_notes` fact into the template renderer and silently
dropped `compose_support_handoff`'s neutralisation, so a note containing a line
like `BAY ASSIGNMENT:` reached the rendered handoff intact and could restructure
the outbound message for whoever read it next. `support_handoff.py` neutralises
via `_FRAMING` -- a regex over section-heading-shaped and separator-shaped lines
-> `[removed]`.

Composition `_safe`s exactly four values (`associate_notes` plus three
`contact_*`). **This module's rule is stronger than that parity, and
deliberately so:** here, *every interpolated value is neutralised, without
exception*. The parity argument -- "these four are the human-typed ones" --
requires someone to re-derive which values are human-typed every time a field is
added, and V1's defect is precisely what happens when that derivation is done
once and then not repeated. In this module the section text is a **code
constant** and the values are **all** `_safe`d, so the question "is this one
human-authored?" never has to be asked. `render_section` is the only way to
build a line, and it neutralises unconditionally.

`_safe` is **imported** from `support_handoff.py`, not re-implemented -- which
is what V1's fix established and what makes a second spelling of the rule
impossible. If `_FRAMING` widens, this module widens with it, in the same
commit, for free.

## "Verbatim" and neutralisation are not in conflict

Sect. 9 requires the clarification fact to carry `verbatim_question`, and the
associate must see Support's actual words rather than an agent's paraphrase.
Both hold:

* the **fact** stores the question exactly as Support sent it -- that is the
  audit record, and nothing here touches it;
* the **rendered message** neutralises, and neutralisation only ever rewrites a
  line that *is itself* a section heading or a separator. Ordinary prose --
  every real support question -- passes through byte for byte, which
  `test_outbound_composition.py` pins as an equality rather than asserting.

So "verbatim" in the sense that matters (not summarised, not reworded, not
re-ordered) is preserved exactly, and the only thing that changes is a line
whose entire content was an impersonation of the message's own structure. A
question cannot be *asked* by a bare `SHIPPING INSTRUCTION:` line, so nothing a
support agent could legitimately write is lost.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

# Imported, never re-implemented -- see the module docstring. This is the same
# neutraliser `compose_support_handoff` applies, so the two paths cannot drift.
from return_platform.operations.support_handoff import UNAVAILABLE, _safe

__all__ = [
    "ComposedMessage",
    "DisclosureLike",
    "compose_clarification_prompt",
    "compose_clarification_relay",
    "compose_reply",
    "neutralized",
    "render_configured_template",
    "render_section",
]

#: How a heading reads. A code constant, because a heading supplied by
#: configuration or by a fact is the defect this module exists to prevent.
_QUESTION_HEADING: Final = "SUPPORT IS ASKING YOU THIS:"
_ANSWER_HEADING: Final = "THE BRANCH ASSOCIATE ANSWERED:"
_CONTEXT_HEADING: Final = "WHY THE PLATFORM COULD NOT ANSWER IT:"
_TRIED_HEADING: Final = "WHAT THE PLATFORM TRIED:"
_NEEDED_HEADING: Final = "WHAT IS NEEDED:"
_CANDIDATES_HEADING: Final = "RECORDS THIS COULD BELONG TO:"


def neutralized(value: Any) -> str:
    """One value, safe to place in a composed message.

    Thin by design: the whole point is that there is exactly **one** way to put
    a value into a message in this module, and that way neutralises. A caller
    reaching for `str(value)` instead is a visible deviation rather than an
    easy default.
    """
    safe = _safe(value)
    return safe if safe is not None else UNAVAILABLE


def render_section(heading: str, *values: Any) -> str:
    """A heading (code constant) and its values (neutralised, always).

    The signature is the guarantee: values arrive through `*values` and every
    one of them goes through `neutralized`. There is no parameter for
    "pre-rendered text", so no caller can hand this function a string it has
    already formatted and thereby skip the neutraliser.
    """
    body = "\n".join(neutralized(value) for value in values) if values else UNAVAILABLE
    return f"{heading}\n{body}"


@dataclass(frozen=True, slots=True)
class ComposedMessage:
    """A message and the disclosure that must travel with it."""

    text: str
    #: True once the disclosure line has been appended. Carried rather than
    #: re-derived, so a caller cannot send the text without it by accident.
    discloses_agent: bool


class DisclosureLike:
    """Structural stand-in for `AgentDisclosureConfiguration`.

    Duck-typed rather than imported so this module does not depend on
    `configuration/`; the two attributes are the whole contract.
    """

    display_name: str
    disclosure_line: str


def _with_disclosure(body: str, disclosure: DisclosureLike | None) -> ComposedMessage:
    """Append the released disclosure line (contracts.md sect. 9).

    The disclosure is neutralised too. It is released configuration rather than
    a person's typing, so it is not the threat this module was built for -- but
    a released string that could restructure an outbound message is still a
    released string that can restructure an outbound message, and exempting it
    would reintroduce exactly one un-neutralised path.
    """
    if disclosure is None:
        return ComposedMessage(text=body, discloses_agent=False)
    line = neutralized(disclosure.disclosure_line)
    name = neutralized(disclosure.display_name)
    return ComposedMessage(text=f"{body}\n\n-- {name}\n{line}", discloses_agent=True)


def compose_clarification_prompt(
    *,
    verbatim_question: str,
    why_unresolvable: str,
    needed_field: str | None = None,
    resolution_attempts: Sequence[str] = (),
    candidate_record_references: Sequence[str] = (),
) -> str:
    """What the associate reads: Support's question, and why it reached them.

    Channel A, not B -- but neutralised on exactly the same basis. V1's defect
    was a *support-or-associate-authored* value restructuring a message for
    "whoever read it next", and here the next reader is the associate whose
    answer is about to be relayed back to Support. A question that could forge
    the panel's own headings is a question that could make the associate answer
    something other than what Support asked.
    """
    sections = [
        render_section(_QUESTION_HEADING, verbatim_question),
        render_section(_CONTEXT_HEADING, why_unresolvable),
    ]
    if resolution_attempts:
        sections.append(render_section(_TRIED_HEADING, *resolution_attempts))
    if needed_field:
        sections.append(render_section(_NEEDED_HEADING, needed_field))
    if candidate_record_references:
        sections.append(render_section(_CANDIDATES_HEADING, *candidate_record_references))
    return "\n\n".join(sections)


def compose_clarification_relay(
    *,
    verbatim_question: str,
    answer_text: str,
    disclosure: DisclosureLike | None = None,
) -> ComposedMessage:
    """What goes back to Support once the associate has answered.

    Both halves are human-typed -- Support's question and the associate's answer
    -- and both are neutralised. The question is quoted back because a support
    thread carries several open questions at once and an answer with no question
    beside it is an answer to whichever one the reader last remembers.
    """
    body = "\n\n".join(
        (
            render_section(_QUESTION_HEADING, verbatim_question),
            render_section(_ANSWER_HEADING, answer_text),
        )
    )
    return _with_disclosure(body, disclosure)


def compose_reply(
    *,
    answer_text: str,
    verbatim_question: str | None = None,
    disclosure: DisclosureLike | None = None,
) -> ComposedMessage:
    """The resolver's answer to a support question.

    `answer_text` is model-authored, and neutralised for the same reason the
    human-typed values are: a model that has just read a support message
    containing `SHIPPING INSTRUCTION:` is the most likely thing in the system to
    reproduce it. Trusting agent text because an agent wrote it would be
    trusting the output of the component with the untrusted input.
    """
    sections = []
    if verbatim_question:
        sections.append(render_section(_QUESTION_HEADING, verbatim_question))
    sections.append(neutralized(answer_text))
    return _with_disclosure("\n\n".join(sections), disclosure)


def render_configured_template(template: str, values: Mapping[str, Any]) -> str:
    """A `support_ingress.outbound_templates` entry, under sect. 8's grammar.

    Interpolation only -- `{field_id}`, literal braces as `{{`/`}}`, no
    expressions -- and **every interpolated value is neutralised** before it is
    substituted. An unknown field id is left as written rather than raising: a
    released template naming a field this message does not carry is a
    configuration mistake that should reach an operator as visible text, not an
    exception that swallows the whole outbound message.
    """
    safe_values = {key: neutralized(value) for key, value in values.items()}
    output: list[str] = []
    index = 0
    while index < len(template):
        character = template[index]
        if character in "{}" and template[index : index + 2] == character * 2:
            output.append(character)
            index += 2
            continue
        if character == "{":
            closing = template.find("}", index)
            if closing == -1:
                output.append(character)
                index += 1
                continue
            field_id = template[index + 1 : closing]
            output.append(safe_values.get(field_id, "{" + field_id + "}"))
            index = closing + 1
            continue
        output.append(character)
        index += 1
    return "".join(output)
