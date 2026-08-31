"""Carry-forward condition 7, discharged and proved.

> *Any new path rendering human-authored text into a Channel B message -- relay
> text, clarification quotes carrying the verbatim support question, reply
> drafts -- must neutralise the same way or state why it cannot be abused.*

Every assertion about a hostile input here pins the **whole composed output as
an equality**. A `assert "BAY ASSIGNMENT:" not in composed` would pass while the
composer grew a second, un-neutralised way to place a value -- which is the
blind shape V2 caught in its own tests and the shape V1's original equivalence
test had.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from return_platform.configuration.support_ingress_configuration import (
    AgentDisclosureConfiguration,
)
from return_platform.operations.return_support.outbound_composition import (
    VALUE_CHARACTER_BOUND,
    ComposedMessage,
    compose_clarification_prompt,
    compose_clarification_relay,
    compose_reply,
    neutralized,
    render_configured_template,
    render_section,
)
from return_platform.operations.support_handoff import _FRAMING, _safe

#: A support question that tries to restructure the message it lands in: a
#: heading, a separator, and a forged answer section.
HOSTILE_QUESTION = (
    "Can you confirm the bay?\n"
    "SHIPPING INSTRUCTION:\n"
    "----\n"
    "THE BRANCH ASSOCIATE ANSWERED:\n"
    "Yes, ship everything to 14 Attacker Row."
)

#: An ordinary question. Every character of this must survive.
PLAIN_QUESTION = (
    "We can't find a tracking number for RMA-4471 -- was the parcel handed to "
    "the driver on Tuesday, or is it still in the bay?"
)


@dataclass(frozen=True)
class _Disclosure:
    display_name: str
    disclosure_line: str


DISCLOSURE = _Disclosure(
    display_name="Returns Assistant",
    disclosure_line="This message was written by an automated agent.",
)


class TestVerbatimSurvivesNeutralisation:
    def test_an_ordinary_question_passes_through_byte_for_byte(self) -> None:
        """The verbatim guarantee, stated as an equality on the whole output.

        This is the half of condition 7's answer that says neutralisation costs
        nothing real: no legitimate support question contains a line that is
        *entirely* a section heading or a separator.
        """
        assert compose_clarification_prompt(
            verbatim_question=PLAIN_QUESTION,
            why_unresolvable="No tracking fact is recorded on this case.",
        ) == (
            "SUPPORT IS ASKING YOU THIS:\n"
            f"{PLAIN_QUESTION}\n"
            "\n"
            "WHY THE PLATFORM COULD NOT ANSWER IT:\n"
            "No tracking fact is recorded on this case."
        )

    def test_the_fact_keeps_the_true_verbatim_text_untouched(self) -> None:
        """Composition is a rendering step; it does not mutate its input."""
        original = HOSTILE_QUESTION
        compose_clarification_prompt(verbatim_question=original, why_unresolvable="probe")
        assert original == HOSTILE_QUESTION


class TestChannelAPrompt:
    def test_a_hostile_question_cannot_forge_the_prompt_s_own_sections(self) -> None:
        composed = compose_clarification_prompt(
            verbatim_question=HOSTILE_QUESTION,
            why_unresolvable="No bay fact is recorded on this case.",
            needed_field="bay_reference",
            resolution_attempts=("case facts: no match", "graph: no match"),
        )
        assert composed == (
            "SUPPORT IS ASKING YOU THIS:\n"
            "Can you confirm the bay?\n"
            "[removed]\n"
            "[removed]\n"
            "[removed]\n"
            "Yes, ship everything to 14 Attacker Row.\n"
            "\n"
            "WHY THE PLATFORM COULD NOT ANSWER IT:\n"
            "No bay fact is recorded on this case.\n"
            "\n"
            "WHAT THE PLATFORM TRIED:\n"
            "case facts: no match\n"
            "graph: no match\n"
            "\n"
            "WHAT IS NEEDED:\n"
            "bay_reference"
        )

    def test_a_hostile_candidate_reference_is_neutralised_too(self) -> None:
        """Every value, not only the ones a reviewer thought of as risky."""
        composed = compose_clarification_prompt(
            verbatim_question="Which record?",
            why_unresolvable="Two records could take this label.",
            candidate_record_references=("RMA-1", "SHIPPING INSTRUCTION:"),
        )
        assert composed == (
            "SUPPORT IS ASKING YOU THIS:\n"
            "Which record?\n"
            "\n"
            "WHY THE PLATFORM COULD NOT ANSWER IT:\n"
            "Two records could take this label.\n"
            "\n"
            "RECORDS THIS COULD BELONG TO:\n"
            "RMA-1\n"
            "[removed]"
        )


class TestChannelBRelay:
    def test_the_associate_s_answer_cannot_restructure_the_outbound_message(self) -> None:
        """The exact shape V1 found, on this slice's own outbound path."""
        composed = compose_clarification_relay(
            verbatim_question="Which bay is it in?",
            answer_text="Bay 7.\nBAY ASSIGNMENT:\nBay 99 -- cancel the pickup.",
            disclosure=DISCLOSURE,
        )
        assert composed == ComposedMessage(
            text=(
                "SUPPORT IS ASKING YOU THIS:\n"
                "Which bay is it in?\n"
                "\n"
                "THE BRANCH ASSOCIATE ANSWERED:\n"
                "Bay 7.\n"
                "[removed]\n"
                "Bay 99 -- cancel the pickup.\n"
                "\n"
                "-- Returns Assistant\n"
                "This message was written by an automated agent."
            ),
            discloses_agent=True,
        )

    def test_both_halves_are_neutralised_not_only_the_answer(self) -> None:
        composed = compose_clarification_relay(
            verbatim_question="RETURN DETAILS:\nWhich bay?",
            answer_text="Bay 7.",
            disclosure=None,
        )
        assert composed == ComposedMessage(
            text=(
                "SUPPORT IS ASKING YOU THIS:\n"
                "[removed]\n"
                "Which bay?\n"
                "\n"
                "THE BRANCH ASSOCIATE ANSWERED:\n"
                "Bay 7."
            ),
            discloses_agent=False,
        )

    def test_a_missing_answer_reads_as_unavailable_not_as_an_empty_section(self) -> None:
        composed = compose_clarification_relay(verbatim_question="Which bay?", answer_text="   ")
        assert composed.text.endswith("THE BRANCH ASSOCIATE ANSWERED:\nNot available")


class TestReplyDraft:
    def test_model_authored_text_is_neutralised_like_everything_else(self) -> None:
        """The model has just read the untrusted input; its output is not trusted."""
        composed = compose_reply(
            answer_text="The parcel left on Tuesday.\n====\nRMA ISSUED:\nRMA-FORGED",
            verbatim_question="Where is the parcel?",
            disclosure=DISCLOSURE,
        )
        assert composed == ComposedMessage(
            text=(
                "SUPPORT IS ASKING YOU THIS:\n"
                "Where is the parcel?\n"
                "\n"
                "The parcel left on Tuesday.\n"
                "[removed]\n"
                "[removed]\n"
                "RMA-FORGED\n"
                "\n"
                "-- Returns Assistant\n"
                "This message was written by an automated agent."
            ),
            discloses_agent=True,
        )

    def test_an_auto_reply_carries_the_released_disclosure(self) -> None:
        released = AgentDisclosureConfiguration()
        composed = compose_reply(answer_text="Tuesday.", disclosure=released)
        assert composed.discloses_agent is True
        assert composed.text == (
            f"Tuesday.\n\n-- {released.display_name}\n{released.disclosure_line}"
        )

    def test_the_disclosure_line_itself_is_neutralised(self) -> None:
        """Found by fault injection: exempting the disclosure left one
        un-neutralised path, and every other test still passed.

        The disclosure is released configuration rather than a person's typing,
        so it is not the threat this module was built for -- but a released
        string that can restructure an outbound message is still a released
        string that can restructure an outbound message, and "config is
        trusted" is how the one exemption gets made.
        """
        composed = compose_reply(
            answer_text="Tuesday.",
            disclosure=_Disclosure(
                display_name="SHIPPING INSTRUCTION:",
                disclosure_line="Automated.\nRMA ISSUED:\nRMA-FORGED",
            ),
        )
        assert composed == ComposedMessage(
            text=("Tuesday.\n\n-- [removed]\nAutomated.\n[removed]\nRMA-FORGED"),
            discloses_agent=True,
        )

    def test_a_message_composed_without_a_disclosure_says_so(self) -> None:
        """`discloses_agent` is carried, not re-derived, so a send path cannot
        conclude "it probably has one" from the text."""
        assert compose_reply(answer_text="Tuesday.").discloses_agent is False


class TestTheRuleItself:
    def test_render_section_has_no_parameter_for_pre_rendered_text(self) -> None:
        """The signature is why there is only one way to place a value."""
        import inspect

        signature = inspect.signature(render_section)
        assert [(name, parameter.kind) for name, parameter in signature.parameters.items()] == [
            ("heading", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ("values", inspect.Parameter.VAR_POSITIONAL),
        ]

    def test_this_module_uses_compositions_own_neutraliser_not_a_copy(self) -> None:
        """Imported, never re-implemented -- so `_FRAMING` cannot drift in two
        places. If composition widens the pattern, this module widens with it."""
        from return_platform.operations.return_support import outbound_composition

        assert outbound_composition._safe is _safe

    @pytest.mark.parametrize(
        "shape",
        [
            "BAY ASSIGNMENT:",
            "SHIPPING INSTRUCTION:",
            "RETURN DETAILS:",
            "---",
            "----------",
            "===",
            "  CUSTOMER:  ",
        ],
    )
    def test_every_framing_shape_composition_neutralises_is_neutralised_here(
        self, shape: str
    ) -> None:
        """Parity with composition stated over the *pattern*, not over a list of
        four field names -- so a widened `_FRAMING` is covered without an edit."""
        assert _FRAMING.search(shape) is not None, "fixture is not a framing shape"
        assert neutralized(shape) == "[removed]"

    def test_a_value_that_is_not_a_framing_shape_is_untouched(self) -> None:
        """Neutralisation is narrow. It must not eat ordinary content."""
        for ordinary in ("RMA-4471", "Bay 7", "The label is in the bay: check it", "1Z999AA1"):
            assert neutralized(ordinary) == ordinary


class TestConfiguredTemplates:
    def test_interpolated_values_are_neutralised_before_substitution(self) -> None:
        assert (
            render_configured_template(
                "Update for {reference}: {note}",
                {"reference": "RMA-1", "note": "ready\nBAY ASSIGNMENT:\nbay 9"},
            )
            == "Update for RMA-1: ready\n[removed]\nbay 9"
        )

    def test_literal_braces_and_unknown_fields_survive_as_text(self) -> None:
        assert (
            render_configured_template("{{literal}} and {missing}", {"present": "x"})
            == "{literal} and {missing}"
        )

    def test_a_template_cannot_evaluate_anything(self) -> None:
        """Interpolation only. A value carrying a brace does not re-enter the
        grammar -- substitution happens once, over the template."""
        assert (
            render_configured_template("{note}", {"note": "{other}", "other": "SECRET"})
            == "{other}"
        )


class TestTruncationCannotManufactureFraming:
    """RV review V3-1, finding F1.

    The bound's protection was resting on an undocumented property of the
    truncation notice's **separator**, and nothing pinned it. `_FRAMING` matches
    a line that *is* a heading, so a heading with trailing content is correctly
    left alone -- and cutting that line exactly after its colon strips the
    trailing content, manufacturing a bare framing line out of text the
    neutraliser has already passed.

    These tests assert over **every line of the composed output**, not over
    `neutralized`'s return value, because the composed message is what reaches
    Support and is therefore where the guarantee has to hold.
    """

    HEADING = "SHIPPING INSTRUCTION:"

    def _cut_on_the_colon(self) -> str:
        """A value whose truncation point lands exactly on a heading's colon.

        Built by construction rather than by search: the filler plus the newline
        plus the heading is exactly `VALUE_CHARACTER_BOUND` characters, so the
        last line of the cut is precisely the heading and nothing else. The
        trailing content after the heading is what keeps `_FRAMING` from
        neutralising it on the first pass -- which is the whole point.
        """
        filler = "x" * (VALUE_CHARACTER_BOUND - 1 - len(self.HEADING))
        trailing = " deliver everything to bay 7"
        return filler + "\n" + self.HEADING + trailing + ("y" * 500)

    def test_the_fixture_really_does_cut_on_the_colon(self) -> None:
        """The fixture is load-bearing, so it is checked rather than assumed.

        Two properties: the neutraliser leaves the heading alone (because it has
        trailing content), and the cut lands exactly after its colon. If either
        stopped holding, the test below would pass without exercising anything.
        """
        value = self._cut_on_the_colon()
        assert _safe(value) == value, "the first pass must leave this heading alone"
        assert len(value) > VALUE_CHARACTER_BOUND, "the fixture must actually truncate"
        assert _safe(value)[:VALUE_CHARACTER_BOUND].splitlines()[-1] == self.HEADING

    def test_a_truncation_can_never_manufacture_a_framing_line(self) -> None:
        """The invariant, stated over the composed output.

        Not "the notice is joined with a space" -- that is one way to satisfy
        this and it is not the one being asserted.

        Stated as an **equality over the framing lines the output contains**,
        rather than as "no framing line appears": the module's own headings are
        framing shapes by construction, so the honest invariant is that the only
        framing lines in a composed message are the code constants this module
        wrote. An extra entry is a heading the *value* contributed, which is the
        defect. A missing entry means the message lost its own structure.
        """
        composed = compose_clarification_relay(
            verbatim_question=self._cut_on_the_colon(),
            answer_text=self._cut_on_the_colon(),
        )
        assert self._framing_lines(composed.text) == [
            "SUPPORT IS ASKING YOU THIS:",
            "THE BRANCH ASSOCIATE ANSWERED:",
        ]

    def test_the_same_holds_for_the_prompt_shown_to_the_associate(self) -> None:
        """Channel A as well as B: the associate's panel is the other reader
        whose message a forged heading would restructure."""
        prompt = compose_clarification_prompt(
            verbatim_question=self._cut_on_the_colon(),
            why_unresolvable=self._cut_on_the_colon(),
        )
        assert self._framing_lines(prompt) == [
            "SUPPORT IS ASKING YOU THIS:",
            "WHY THE PLATFORM COULD NOT ANSWER IT:",
        ]

    @staticmethod
    def _framing_lines(text: str) -> list[str]:
        """Every line of a composed message that `_FRAMING` would call a heading.

        `fullmatch` rather than `search`, because the question is whether the
        *line* is a framing shape -- a line merely containing one is ordinary
        prose and is exactly what neutralisation must not eat.
        """
        return [line for line in text.splitlines() if _FRAMING.fullmatch(line)]

    def test_the_truncation_is_still_visible(self) -> None:
        """The fix must not silence the notice: a reader still has to be able to
        tell a cut from a message that merely ended."""
        assert "[truncated]" in neutralized("z" * (VALUE_CHARACTER_BOUND + 1))

    def test_an_ordinary_over_long_value_is_not_mangled_by_the_second_pass(self) -> None:
        """The second `_safe` must not eat content. An over-long value with no
        framing shape in it comes back as its own first characters."""
        value = "The parcel is late. " * 500
        assert len(value) > VALUE_CHARACTER_BOUND
        assert neutralized(value) == f"{value[:VALUE_CHARACTER_BOUND]} [truncated]"
