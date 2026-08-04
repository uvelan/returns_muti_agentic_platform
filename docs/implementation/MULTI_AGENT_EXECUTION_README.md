# Returns Platform Multi-Agent Execution Files

Complete Markdown control set for implementing the dynamic knowledge-graph and LLM-driven returns platform on Windows.

## Repository

```text
https://github.com/uvelan/returns_muti_agentic_platform.git
```

## Target branch

```text
feat/v2-order-discovery-integration
```

## Read in this order

1. `AGENTS.md`
2. `docs/implementation/MASTER_MULTI_AGENT_PROMPT.md`
3. `docs/implementation/WINDOWS_EXECUTION_WORKFLOW.md`
4. `docs/implementation/TASK_CLASSIFICATION_AND_MODEL_ROUTING.md`
5. `docs/implementation/ROLE_PROMPTS.md`
6. `docs/execution-context/MASTER_EXECUTION_CONTEXT.md`

## Operating model

- Antigravity IDE is the graphical IDE.
- Codex CLI is the production-code writer.
- Gemini 3.1 Pro handles critical architecture analysis and blocker escalation.
- Gemini 3.6 Flash handles focused analysis and independent validation.
- Claude Sonnet 4.5 handles independent code and security review.
- Gemini 3.5 Flash is optional for low-risk context maintenance.
- Only one agent writes production code at a time.
- Every completed step is reviewed as required, validated, committed and pushed.
- No ZIP files are generated.
- No unnecessary branches are created.
- Execution continues unless a genuine blocker prevents safe progress.
