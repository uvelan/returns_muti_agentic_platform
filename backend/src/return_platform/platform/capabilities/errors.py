"""Capability registry error types."""

from __future__ import annotations


class CapabilityNotPublished(RuntimeError):
    """No provider has published this (capability, contract) pair."""


class DuplicateCapability(RuntimeError):
    """The same (capability, contract) pair was published twice."""


class CapabilityTypeMismatch(RuntimeError):
    """The published instance does not structurally satisfy `contract`."""
