# Agent Matrix

All five bounded agents exist; UI/runtime completeness differs.

| Agent | Classification | Source | Typed input/output | Boundary | Invocation | Persistence | Tests | UI | Authority |
|---|---|---|---|---|---|---|---|---|---|
| Order Discovery Agent | VERIFIED_IMPLEMENTED | backend/src/return_platform/agents/order_discovery.py | Yes | Typed advisory agent ranks candidates and cannot auto-confirm. | POST /api/v1/return-agents/order-discovery/assess | agent_decisions | backend/tests/agents/test_return_agents.py | /associate/returns | Advisory |
| Return Workflow Agent | VERIFIED_IMPLEMENTED | backend/src/return_platform/agents/return_workflow.py | Yes | Typed advisory agent validates intake and drafts Support request. | POST /api/v1/return-agents/return-workflow/assess | agent_decisions | backend/tests/agents/test_return_agents.py | /associate/returns | Advisory |
| Return Fulfillment Agent | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | backend/src/return_platform/agents/fulfillment.py | Yes | Normalize authoritative facts without creating them. | POST /api/v1/return-agents/fulfillment/assess | agent_decisions | backend/tests/agents/test_return_agents.py | Missing /operations/return-agents | Advisory |
| Bay Assignment Agent | VERIFIED_IMPLEMENTED | backend/src/return_platform/agents/bay_assignment.py | Yes | Advisory compatibility/capacity ranking; deterministic SQL owns assignment. | POST /api/v1/return-agents/bay-assignment/assess | agent_decisions; platform bay SQL | backend/tests/agents/test_return_agents.py | None | Advisory |
| Feedback Learning Agent | VERIFIED_IMPLEMENTED | backend/src/return_platform/agents/feedback.py | Yes | Review-only recommendations; no automatic production rule changes. | POST /api/v1/return-agents/feedback/assess | agent_decisions; feedback_learning_records | backend/tests/agents/test_return_agents.py | None | Advisory |
