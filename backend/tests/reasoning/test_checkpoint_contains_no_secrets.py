"""CheckpointRedactor rejects -- never silently strips -- a disallowed state key."""

from __future__ import annotations

import pytest

from return_platform.platform.reasoning.errors import CheckpointRedactionViolation
from return_platform.platform.reasoning.redaction import CheckpointRedactor


def test_allowlisted_state_passes_through_unchanged() -> None:
    redactor = CheckpointRedactor(frozenset({"candidate_id", "evidence_ref"}))
    state = {"candidate_id": "C-1", "evidence_ref": "EV-1"}

    assert redactor.enforce(state) == state


def test_a_secret_bearing_key_is_rejected_not_stripped() -> None:
    redactor = CheckpointRedactor(frozenset({"candidate_id"}))
    state = {"candidate_id": "C-1", "vault_secret": "sk-should-never-be-here"}

    with pytest.raises(CheckpointRedactionViolation) as excinfo:
        redactor.enforce(state)

    assert "vault_secret" in str(excinfo.value)


def test_raw_configuration_snapshot_key_is_rejected() -> None:
    redactor = CheckpointRedactor(frozenset({"configuration_release_id"}))
    state = {"configuration_snapshot": {"any": "raw config body"}}

    with pytest.raises(CheckpointRedactionViolation):
        redactor.enforce(state)
