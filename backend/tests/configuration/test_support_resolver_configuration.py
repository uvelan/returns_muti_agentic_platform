"""The released resolution policy, and the closure of the tool-schema allowlist.

Contracts.md sect. 9/10. The tests here are about what a *release* can and
cannot say -- the runtime half of the tool boundary is in
`tests/platform/test_support_tool_router.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.support_resolver_configuration import (
    AUTO_REPLY,
    REVIEW_REQUIRED,
    ReplyGateConfiguration,
    SupportResolverConfiguration,
    ToolBindingConfiguration,
)
from return_platform.platform.capabilities.tool_schemas import (
    TOOL_INPUT_SCHEMAS,
    EntityType,
    UnknownInputSchemaError,
    resolve_input_schema,
)


def _binding(**overrides: object) -> ToolBindingConfiguration:
    fields: dict[str, object] = {
        "tool_id": "return-record-lookup",
        "intents": ("info_request",),
        "capability": "graph.query",
        "contract": "GraphQueryPort",
        "description": "Read what the graph knows about one return record.",
        "input_schema_ref": "graph.return_record_lookup.v1",
    }
    fields.update(overrides)
    return ToolBindingConfiguration(**fields)  # type: ignore[arg-type]


class TestDefaults:
    def test_the_default_block_is_the_closed_one(self) -> None:
        """Every default is the conservative value, stated as one equality.

        A per-field assertion would let a future edit loosen one default while
        the others kept the test green.
        """
        resolver = SupportResolverConfiguration()
        assert (
            resolver.fact_confidence_millionths,
            resolver.graph_confidence_millionths,
            resolver.tool_bindings,
            resolver.reply_gate.default,
            resolver.reply_gate.per_intent,
            resolver.clarification_resets_deadline,
            resolver.per_case_llm_budget,
        ) == (900_000, 900_000, (), REVIEW_REQUIRED, {}, True, 12)

    def test_a_release_with_no_resolver_block_still_loads_and_binds_nothing(self) -> None:
        """The field is defaulted, and its default makes no tool eligible."""
        configuration = ReturnPlatformConfiguration.model_construct()
        resolver = SupportResolverConfiguration()
        assert resolver.bindings_for_intent("info_request") == ()
        assert "support_resolver" in type(configuration).model_fields


class TestReplyGate:
    def test_an_unlisted_intent_falls_to_the_default_not_to_auto_reply(self) -> None:
        gate = ReplyGateConfiguration(per_intent={"acknowledgement": AUTO_REPLY})
        assert gate.mode_for("acknowledgement") == AUTO_REPLY
        assert gate.mode_for("info_request") == REVIEW_REQUIRED
        # An intent no release ever heard of -- the floor must still apply.
        assert gate.mode_for("intent_invented_after_this_release") == REVIEW_REQUIRED
        assert gate.requires_review("intent_invented_after_this_release") is True

    def test_a_third_mode_does_not_parse(self) -> None:
        """The mode is closed at parse time, not by an `!= auto_reply` branch.

        If it were only a branch, "send_immediately" would read as review-
        required and quietly work -- which is the failure mode where a typo
        looks safe.
        """
        with pytest.raises(ValidationError):
            ReplyGateConfiguration(default="send_immediately")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            ReplyGateConfiguration(per_intent={"info_request": "send_immediately"})


class TestToolBindings:
    def test_a_binding_naming_an_unimplemented_schema_is_refused_at_parse(self) -> None:
        with pytest.raises(ValidationError) as caught:
            _binding(input_schema_ref="graph.anything_i_like.v1")
        assert "graph.anything_i_like.v1" in str(caught.value)

    def test_a_binding_eligible_for_no_intent_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _binding(intents=())

    def test_eligibility_is_per_intent_and_keeps_declaration_order(self) -> None:
        first = _binding(tool_id="first", intents=("info_request", "tracking_provided"))
        second = _binding(
            tool_id="second",
            intents=("info_request",),
            input_schema_ref="graph.shipment_status.v1",
        )
        resolver = SupportResolverConfiguration(tool_bindings=(first, second))
        assert [b.tool_id for b in resolver.bindings_for_intent("info_request")] == [
            "first",
            "second",
        ]
        assert [b.tool_id for b in resolver.bindings_for_intent("tracking_provided")] == ["first"]
        assert resolver.bindings_for_intent("rejection") == ()

    def test_a_binding_carries_no_credential_value_only_an_id(self) -> None:
        """The credential is a reference. There is no field to put a secret in."""
        bound = _binding(credential_binding_id="graph-read-profile")
        assert bound.credential_binding_id == "graph-read-profile"
        assert set(type(bound).model_fields) == {
            "tool_id",
            "intents",
            "capability",
            "contract",
            "description",
            "input_schema_ref",
            "credential_binding_id",
        }

    def test_a_release_cannot_add_a_field_of_its_own(self) -> None:
        with pytest.raises(ValidationError):
            _binding(argument_expression="{{ body_text }}")


class TestSchemaAllowlist:
    def test_the_allowlist_is_exactly_what_this_build_implements(self) -> None:
        assert set(TOOL_INPUT_SCHEMAS) == {
            "graph.return_record_lookup.v1",
            "graph.shipment_status.v1",
        }

    def test_an_unknown_ref_raises_rather_than_returning_an_empty_schema(self) -> None:
        with pytest.raises(UnknownInputSchemaError):
            resolve_input_schema("graph.return_record_lookup.v2")

    def test_every_schema_requires_the_case_id_it_is_scoped_to(self) -> None:
        """No tool may be routed at a record without saying which case it is for."""
        for schema in TOOL_INPUT_SCHEMAS.values():
            assert "caseId" in schema.required_entity_names, schema.schema_ref

    @pytest.mark.parametrize(
        ("entity_type", "value", "expected"),
        [
            (EntityType.STRING, "  RMA-1  ", "RMA-1"),
            (EntityType.STRING, "   ", None),
            (EntityType.STRING, 7, None),
            (EntityType.STRING, None, None),
            (EntityType.INTEGER, 3, 3),
            (EntityType.INTEGER, "3", None),
            # `isinstance(True, int)` is true; without the explicit bool guard a
            # boolean fact would reach a tool as the quantity 1.
            (EntityType.INTEGER, True, None),
            (EntityType.STRING, True, None),
        ],
    )
    def test_coercion_is_a_type_check_not_a_conversion(
        self, entity_type: EntityType, value: object, expected: object
    ) -> None:
        from return_platform.platform.capabilities.tool_schemas import EntityField

        field = EntityField(name="probe", entity_type=entity_type, description="probe")
        assert field.coerced(value) == expected
