# platform/redaction

Scrubs whole fields out of a structured payload before it is persisted or emitted —
distinct from `secrets.vault.SecretRedactor`, which scrubs secret *values* out of log
lines and has no notion of a field allowlist.

`AllowlistRedactor` is fail-closed by construction: a field is dropped unless it was
explicitly named in the allowlist passed to the constructor, so a payload field added
later stays redacted by default until someone deliberately allowlists it.

Used wherever a structured payload crosses a trust boundary — an `AgentExecutionContext`
constructing an `AuditEvent`, and (in a later phase) LangGraph checkpoint state before
it is written to the reasoning checkpointer.
