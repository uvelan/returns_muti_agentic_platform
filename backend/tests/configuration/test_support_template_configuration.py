"""The template grammar refuses what it cannot hold to (contracts.md sect. 8).

Every refusal here is release validation: a bad template is a refused
release, never a template that renders something other than what it says.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.support_template_configuration import (
    SupportTemplateConfiguration,
    TemplateFieldConfiguration,
    TemplateRuleConfiguration,
    TemplateSectionConfiguration,
    TemplateVariantConfiguration,
    binding_source,
    subject_placeholders,
)

_PRODUCTION_YAML = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"


def _field(field_id: str = "order_number", **overrides: object) -> TemplateFieldConfiguration:
    payload: dict[str, object] = {
        "field_id": field_id,
        "label": "Order Number",
        "source_binding": "case_fact:confirmed_order_reference",
    }
    payload.update(overrides)
    return TemplateFieldConfiguration.model_validate(payload)


def _variant(variant_id: str = "default", **overrides: object) -> TemplateVariantConfiguration:
    payload: dict[str, object] = {
        "variant_id": variant_id,
        "subject_template": "Return {order_number}",
        "sections": [
            {
                "section_id": "order",
                "title": "Order:",
                "fields": [_field().model_dump()],
            }
        ],
    }
    payload.update(overrides)
    return TemplateVariantConfiguration.model_validate(payload)


class TestStrictness:
    def test_unknown_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            TemplateFieldConfiguration.model_validate(
                {
                    "field_id": "x",
                    "source_binding": "case_fact:x",
                    "python": "os.system('rm -rf /')",
                }
            )

    def test_unknown_top_level_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SupportTemplateConfiguration.model_validate({"template_id": "t", "renderer": "jinja"})


class TestFormatterAllowlist:
    def test_unknown_formatter_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unknown formatter"):
            _field(formatter="jinja2")

    @pytest.mark.parametrize("formatter", ["text", "date", "currency", "address", "item_list"])
    def test_every_allowlisted_formatter_is_accepted(self, formatter: str) -> None:
        assert _field(formatter=formatter).formatter == formatter


class TestBindingGrammar:
    def test_unknown_source_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unknown binding source"):
            _field(source_binding="shell:cat /etc/passwd")

    def test_pathless_binding_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="names no path"):
            _field(source_binding="case_fact:")

    def test_literal_binding_may_be_any_text(self) -> None:
        source, path = binding_source("literal:- Review the complete return request.")
        assert source == "literal"
        assert path == "- Review the complete return request."


class TestSubjectPlaceholders:
    def test_unresolvable_placeholder_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unknown field ids: no_such_field"):
            _variant(subject_template="Return {no_such_field}")

    def test_escaped_braces_are_literal(self) -> None:
        assert subject_placeholders("{{not_a_field}} {order_number}") == ("order_number",)

    def test_unclosed_placeholder_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unclosed"):
            subject_placeholders("Return {order_number")

    def test_lone_closing_brace_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unmatched"):
            subject_placeholders("Return order}")


class TestTemplateShape:
    def test_default_variant_must_exist(self) -> None:
        with pytest.raises(ValidationError, match="names no variant"):
            SupportTemplateConfiguration.model_validate(
                {
                    "template_id": "t",
                    "default_variant_id": "missing",
                    "variants": [_variant("only").model_dump()],
                }
            )

    def test_duplicate_variant_ids_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="duplicate variant ids"):
            SupportTemplateConfiguration.model_validate(
                {
                    "template_id": "t",
                    "default_variant_id": "default",
                    "variants": [_variant().model_dump(), _variant().model_dump()],
                }
            )

    def test_duplicate_field_ids_within_a_variant_are_refused(self) -> None:
        section = {
            "section_id": "order",
            "fields": [_field().model_dump(), _field().model_dump()],
        }
        with pytest.raises(ValidationError, match="repeats field ids"):
            _variant(sections=[section])

    def test_empty_block_loads_as_the_pre_template_release(self) -> None:
        template = SupportTemplateConfiguration()
        assert template.variants == ()
        assert template.default_variant() is None

    def test_inverted_item_count_bounds_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="min_item_count exceeds"):
            TemplateRuleConfiguration.model_validate({"min_item_count": 3, "max_item_count": 1})

    def test_clause_less_rule_declares_nothing(self) -> None:
        assert TemplateRuleConfiguration().declares_anything is False

    def test_per_record_sections_are_structural(self) -> None:
        section = TemplateSectionConfiguration.model_validate(
            {
                "section_id": "record",
                "fields": [
                    _field("rma", source_binding="return_record:returnReference").model_dump()
                ],
            }
        )
        assert section.per_record is True


class TestProductionRelease:
    def test_production_yaml_carries_the_three_variants(self) -> None:
        template = load_return_configuration(_PRODUCTION_YAML).configuration.support_template
        assert [variant.variant_id for variant in template.variants] == [
            "default",
            "parcel",
            "ltl",
        ]
        assert template.default_variant_id == "default"
        assert template.default_variant() is not None
        # The default variant is reachable only by being the default; parcel
        # and LTL each declare their shipping classes.
        assert template.variants[0].selector.declares_anything is False
        assert template.variants[1].selector.shipping_modes == (
            "PREPAID_PARCEL",
            "OFFSITE_PARCEL",
        )
        assert template.variants[2].selector.shipping_modes == ("BRANCH_LTL", "OFFSITE_LTL")
