"""V2: two doors, one internal event (contracts.md sect. 5).

The claim under test is the one DR-2 settled: the structured endpoint and the
natural-language endpoint do not compete, they *normalize*. So the test that
matters is not that each produces something -- it is that when they say the
same thing they produce the same business event, and that when they say
different things they do not.
"""

from __future__ import annotations

import pytest

from return_platform.configuration.support_ingress_configuration import (
    SupportIngressConfiguration,
)
from return_platform.operations.artifact_binding import ArtifactType, ExtractedArtifact
from return_platform.operations.return_support.ingress import (
    STRUCTURED_ISSUED_INTENT,
    STRUCTURED_REJECTION_INTENT,
    STRUCTURED_TRANSPORT_ID,
    NormalizedSupportEvent,
    ReturnRecordBinding,
    SupportInboundMessage,
    SupportSender,
    coerce_intent,
    derive_support_event_id,
    extracted_artifacts,
    normalize_inbound_message,
    normalize_return_outcome,
    record_bindings_from_extraction,
)

CASE = "case-1"
WORK_ITEM = "wi-1"

STRUCTURED_RECORD = {
    "returnReference": "RMA-1",
    "trackingReference": "1Z-AAA",
    "labelReference": "LBL-1",
    "returnLocation": None,
    "shippingInstructionReference": None,
    "returnMethod": "carrier_pickup",
    "carrier": "UPS",
    "orderLineReferences": ("line-1", "line-2"),
}


def _structured(**overrides: object) -> NormalizedSupportEvent:
    kwargs: dict[str, object] = {
        "case_id": CASE,
        "work_item_id": WORK_ITEM,
        "support_event_id": "sev-1",
        "records": [STRUCTURED_RECORD],
        "rejected": False,
        "reason": None,
        "sender": SupportSender(sender_id="support-agent-7"),
    }
    kwargs.update(overrides)
    return normalize_return_outcome(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The equivalence
# --------------------------------------------------------------------------- #


def test_structured_and_natural_language_normalize_to_the_same_business_event() -> None:
    """The DR-2 property, stated as an equality on the canonical form.

    The natural-language side goes the long way round on purpose: it arrives as
    a message with no analysis at all, and the analysis is folded in through
    `with_analysis` exactly as the dispatcher will fold in an accepted
    extraction. If those two routes did not converge, every downstream consumer
    would need to know which door a case's data came from.
    """
    structured = _structured()

    message = SupportInboundMessage(
        external_message_id="email-99",
        body_text="RMA-1 is issued; label LBL-1, tracking 1Z-AAA, UPS pickup.",
        sender="support-agent-7",
        channel_hint="email",
    )
    inbound = normalize_inbound_message(message, case_id=CASE, work_item_id=WORK_ITEM)
    assert inbound.intent is None, "the raw message must carry no analysis"

    analysed = inbound.with_analysis(
        intent=STRUCTURED_ISSUED_INTENT,
        bindings=record_bindings_from_extraction({"records": [STRUCTURED_RECORD]}),
    )

    assert analysed.canonical_business_form() == structured.canonical_business_form()


def test_the_canonical_form_still_separates_events_that_say_different_things() -> None:
    """The equivalence above is worthless if the form collapses everything.

    Each mutation below changes exactly one business statement, and each must
    move the form. Without this, a canonical form that returned `{}` would pass
    the equivalence test.
    """
    baseline = _structured().canonical_business_form()

    assert _structured(rejected=True).canonical_business_form() != baseline
    assert _structured(reason="out of policy").canonical_business_form() != baseline
    assert (
        _structured(
            records=[{**STRUCTURED_RECORD, "trackingReference": "1Z-BBB"}]
        ).canonical_business_form()
        != baseline
    )
    assert (
        _structured(records=[{**STRUCTURED_RECORD, "returnReference": "RMA-2"}])
        .canonical_business_form()
        != baseline
    )
    assert _structured(records=[]).canonical_business_form() != baseline


def test_the_canonical_form_ignores_only_how_the_message_arrived() -> None:
    """Transport identity is out; anything a reader would act on is in."""
    one = _structured(support_event_id="sev-1", external_message_id="a")
    two = _structured(support_event_id="sev-2", external_message_id="b")
    assert one.support_event_id != two.support_event_id
    assert one.canonical_business_form() == two.canonical_business_form()


def test_record_group_order_does_not_change_the_business_form() -> None:
    """Two RMAs in the other order are the same reply.

    A form that sorted nothing would make the equivalence test depend on the
    order a model happened to list records in, which is not a business fact.
    """
    second = {**STRUCTURED_RECORD, "returnReference": "RMA-2"}
    forwards = _structured(records=[STRUCTURED_RECORD, second])
    backwards = _structured(records=[second, STRUCTURED_RECORD])
    assert forwards.canonical_business_form() == backwards.canonical_business_form()


# --------------------------------------------------------------------------- #
# The structured path asks nothing
# --------------------------------------------------------------------------- #


def test_the_structured_path_assigns_its_intent_from_the_payloads_own_shape() -> None:
    assert _structured().intent == STRUCTURED_ISSUED_INTENT
    assert _structured(rejected=True).intent == STRUCTURED_REJECTION_INTENT
    assert _structured().transport_id == STRUCTURED_TRANSPORT_ID


def test_the_structured_path_carries_the_record_keys_the_workflow_reads() -> None:
    """Built through `support_return_record`, so the snake-case contract holds."""
    (record,) = _structured().support_records()
    assert record["return_reference"] == "RMA-1"
    assert record["tracking_reference"] == "1Z-AAA"
    assert record["return_method"] == "carrier_pickup"
    assert record["carrier"] == "UPS"
    assert record["order_line_references"] == ["line-1", "line-2"]


def test_blank_strings_are_read_as_silence_not_as_erasure() -> None:
    """`""` is not a statement -- the merge rule `support_events` documents."""
    (record,) = _structured(
        records=[{**STRUCTURED_RECORD, "carrier": "   ", "returnLocation": ""}]
    ).support_records()
    assert record["carrier"] is None
    assert record["return_location"] is None


# --------------------------------------------------------------------------- #
# The dedupe identity
# --------------------------------------------------------------------------- #


def test_the_internal_id_is_derived_from_the_contracts_three_part_identity() -> None:
    base = derive_support_event_id(
        case_id=CASE, transport_id="email", external_message_id="m-1"
    )
    assert base == derive_support_event_id(
        case_id=CASE, transport_id="email", external_message_id="m-1"
    )
    # Each part is load-bearing.
    assert base != derive_support_event_id(
        case_id="case-2", transport_id="email", external_message_id="m-1"
    )
    assert base != derive_support_event_id(
        case_id=CASE, transport_id="teams", external_message_id="m-1"
    )
    assert base != derive_support_event_id(
        case_id=CASE, transport_id="email", external_message_id="m-2"
    )


def test_the_derivation_cannot_be_confused_by_a_shifted_separator() -> None:
    """`("ab","c")` and `("a","bc")` must not derive the same id.

    A plain join would collide them, and the collision would silently make two
    different transports' messages one event.
    """
    assert derive_support_event_id(
        case_id=CASE, transport_id="ab", external_message_id="c"
    ) != derive_support_event_id(case_id=CASE, transport_id="a", external_message_id="bc")


def test_the_same_words_on_two_transports_are_two_events() -> None:
    """Contracts.md sect. 5: distinct transports = distinct messages."""
    body = "Tracking is 1Z-AAA."
    email = normalize_inbound_message(
        SupportInboundMessage(
            external_message_id="shared", body_text=body, sender="s", channel_hint="email"
        ),
        case_id=CASE,
        work_item_id=WORK_ITEM,
    )
    chat = normalize_inbound_message(
        SupportInboundMessage(
            external_message_id="shared", body_text=body, sender="s", channel_hint="teams"
        ),
        case_id=CASE,
        work_item_id=WORK_ITEM,
    )
    assert email.support_event_id != chat.support_event_id


# --------------------------------------------------------------------------- #
# Ordering fields belong to the store
# --------------------------------------------------------------------------- #


def test_a_normalizer_never_invents_an_ordering_field() -> None:
    """Sect. 7: `stream_sequence` and causation come from the enqueuing store."""
    for event in (
        _structured(),
        normalize_inbound_message(
            SupportInboundMessage(
                external_message_id="m", body_text="hi", sender="s", channel_hint="email"
            ),
            case_id=CASE,
            work_item_id=WORK_ITEM,
        ),
    ):
        assert event.stream_sequence is None
        assert event.causation is None


# --------------------------------------------------------------------------- #
# The closed taxonomy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("rma_issued", "rma_issued"),
        ("  RMA_ISSUED  ", "rma_issued"),
        ("escalate_to_legal", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_out_of_set_intents_collapse_to_other(candidate: str | None, expected: str) -> None:
    assert coerce_intent(candidate, SupportIngressConfiguration()) == expected


def test_a_release_that_narrows_the_taxonomy_narrows_what_is_recognised() -> None:
    narrow = SupportIngressConfiguration(intents=("info_request",))
    assert coerce_intent("info_request", narrow) == "info_request"
    assert coerce_intent("rma_issued", narrow) == "other"


# --------------------------------------------------------------------------- #
# Reading an accepted extraction
# --------------------------------------------------------------------------- #


def test_unknown_artifact_types_are_dropped_rather_than_carried() -> None:
    artifacts = extracted_artifacts(
        {
            "artifacts": [
                {"artifactType": "TRACKING", "value": "1Z-AAA", "binding": "RMA-1"},
                {"artifactType": "tracking", "value": "1Z-BBB"},
                {"artifactType": "TELEPORTER", "value": "nope"},
                {"artifactType": "LABEL", "value": "   "},
                "not-a-mapping",
            ]
        }
    )
    assert artifacts == (
        ExtractedArtifact(ArtifactType.TRACKING, "1Z-AAA", "RMA-1"),
        ExtractedArtifact(ArtifactType.TRACKING, "1Z-BBB", None),
    )


def test_a_group_without_a_return_reference_is_not_a_group() -> None:
    """DR-11's create-never rule, at the point it would first break.

    An extraction that mentions a tracking number with no RMA is a *loose
    artifact*; turning it into a record group with an empty reference would
    create a record, which the contract forbids outright.
    """
    bindings = record_bindings_from_extraction(
        {
            "records": [
                {"returnReference": "RMA-1", "trackingReference": "1Z"},
                {"returnReference": "  ", "trackingReference": "1Z-orphan"},
                {"trackingReference": "1Z-orphan-2"},
            ]
        }
    )
    assert bindings == (
        ReturnRecordBinding(return_reference="RMA-1", tracking_reference="1Z"),
    )


def test_an_extraction_with_no_lists_at_all_yields_nothing() -> None:
    assert extracted_artifacts({}) == ()
    assert record_bindings_from_extraction({}) == ()
