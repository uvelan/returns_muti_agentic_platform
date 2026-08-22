"""Targeted state repair.

Repairs are code, reviewed as critically as the invariants they restore, and
never a way to disguise an unfixed invariant. Every one of them is dry-run
first, names its exact targets, and writes a manifest that can undo it.
"""
