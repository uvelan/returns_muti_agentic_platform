---
name: testmanual
description: Start the platform in MANUAL mode — every order-agent reasoning turn pauses and waits for a human-authored model response on disk instead of calling a hosted provider — and then answer those requests as the reasoning model. Use when asked to test, drive, or walk through the copilot without burning provider quota, when asked to "start manual mode", or when a `.manual_llm` request is waiting to be answered.
---

# Manual mode

`PLATFORM_AI_PROVIDER_ORDER=MANUAL` routes every model call through
`ManualFileProvider`: it writes the exact request a hosted model would have
received into `backend/.manual_llm/requests/<id>.json` and blocks until
`backend/.manual_llm/responses/<id>.json` appears. No key, no quota, no network,
and the same dispatch path the real providers take — so what is exercised is the
platform, not a simulator.

You are the model. Every request that lands is yours to answer.

## Start it

Four checks and two commands. Do the checks first — each one is a way manual
mode silently is not manual, and all three have happened here.

**1. The environment must ask for it.** In the repository `.env`:

```
PLATFORM_AI_PROVIDER_ORDER=MANUAL     # MANUAL *first*, not merely present
PLATFORM_AI_MANUAL_HANDOFF=FILE       # AUTO parks requests in the console instead
PLATFORM_ENVIRONMENT=development      # or test
```

A hosted provider ahead of `MANUAL` in the order answers every turn before the
file provider is asked. `AUTO` hands the request to the durable interception
store rather than to disk. And outside development or test the provider reports
`configured: false`, so the turn fails as `POLICY_BLOCKED` — which reads like a
permissions problem rather than a deployment that must never expose this.

**2. Start the platform**, if it is not already up:

```bash
./scripts/infra.sh start                        # datastores
./scripts/run_all_host.sh --no-supervise        # backend, workers, frontend
./scripts/linux/redeploy_app.sh --skip-frontend-build   # to pick up code changes
```

**3. Prove the turns will reach a human**, rather than assuming:

```bash
curl -s localhost:8000/api/v1/ai-gateway/routes | python3 -m json.tool
```

Every route must read `MANUAL`. A hosted provider published beside it means
some turns are answered by a model and some by a person, with nothing in the
transcript to tell them apart — the exact ambiguity manual mode removes.

**4. Clear anything a killed run left behind.** A response file with no matching
request is an orphan the provider will never read:

```bash
rm -f backend/.manual_llm/responses/*.json
```

Then arm the watch below.

> A Linux-only wrapper for all of this may exist at
> `scripts/dev/start_manual_mode.sh`. It is deliberately **not committed** —
> see the note in `.gitignore` — so treat the steps above as the source of
> truth and the script as a shortcut that may not be present.

Then arm a watch so requests reach you without polling:

```
Monitor(
  command: 'cd <repo>/backend/.manual_llm/requests
            seen=""
            while true; do
              for f in *.json; do
                [ -e "$f" ] || continue
                case " $seen " in *" $f "*) continue;; esac
                seen="$seen $f"; echo "MANUAL_REQUEST_PENDING $f"
              done
              sleep 1
            done',
  description: 'new MANUAL LLM requests',
  persistent: true)
```

## Answer a request

Read the request, decide the action, write the JSON to
`responses/<same-id>.json`. The provider consumes it within a second and deletes
both files. `userPayload.contextJson` carries the whole turn; `systemPrompt`
carries the required `AgentAction` schema — **read it, it is authoritative**, and
what follows is only what is easy to get wrong.

### The shape

One JSON object. `business_capability` is copied character for character from
`contextJson.compact_schema.capabilities` (`order-discovery`, lower case,
hyphenated). Each `action_type` has a required payload: `ORDER_SEARCH` needs
`search_intent`, `GRAPH_QUERY` needs `query_plan`, `CONFIRM_ORDER` needs
`order_confirmation`, `RESPOND` and `CLARIFY` need `response` — and `CLARIFY`
needs a non-empty `response.requested_input`, not just a
`CLARIFICATION_QUESTION` statement.

Asking and finishing are mutually exclusive: a response carrying a question is
rejected under `status: COMPLETE` or `DISCOVERY_COMPLETE`. Use
`NEEDS_CLARIFICATION`.

### Evidence

Every `GRAPH_FACT` needs a non-empty `evidence_refs`, and paths are **relative
to the evidence record's `result`** with every segment a string:
`["candidates", "0", "data", "account_id"]`, `["rows", "3", "order_status"]`,
`["count"]`. Leave `expected_value` out unless copying a value exactly — it is
compared literally. State anything the results do not contain as a
`REASONED_SUGGESTION`, never as an uncited `GRAPH_FACT`.

A `USER_PROVIDED_FACT` requires `source_message_id`, which is
`contextJson.client_turn_id` — this turn's, never the previous one's.

### Facts

Report on `observed_facts` every identifying detail the associate stated, on the
turn they state it, on *every* action including searches. The `fact` name must
be one of the configured vocabulary the prompt lists; a name outside it is
discarded and the detail is lost. `acquisition` is `STATED` or `DERIVED` — never
`OBSERVED`, which means a source system reported it. Do not re-report what
`captured_facts` already holds unchanged.

### Confirming an order

`order_confirmation.candidate_id` must be a member of the **active candidate
set** (`conversation_state.orderSearchCache.candidateSet`). A customer-name
search leaves customer ids in that set, not order numbers — so confirming an
order the associate just named usually needs an `ORDER_SEARCH` on
`orderNumbers` first, to put it in a set the confirmation can bind to. Read the
lines before confirming so `order_line_references` names the line rather than
the whole order.

### What you cannot see

Redaction runs before every provider call, so `customer_name` reaches you as
`[REDACTED]` even in graph rows. The browser sees the real value. Never claim a
name you were not given; describe the customer by what you *can* cite (account,
order, product) and let the panel show who it is.

## The prompt is the instruction. Follow it whole.

**Do the whole of a two-part instruction, in one turn.** The prompt says "show
that customer's orders **and the products on them**, and only then narrow to a
single order". Doing the first half and deferring the second is not following
it — it is rewriting it into something easier to query. Observed: a customer
with eleven orders got eleven header rows, no Product column, and a question
the associate could not answer from the part in their hand.

**Never trim on a guess about volume.** The reason given for that half-step was
"eleven orders could be fifty line rows". Nobody counted. It was 31, across
eleven orders of which eight carry one or two lines — comfortably renderable,
and the products separated the orders far better than the dates did. A `COUNT`
traversal costs one query and one second. If a limit is the reason for doing
less than the prompt says, measure the limit first; if it turns out to be real,
scope deliberately and **say in the response what was left out**, rather than
quietly showing less.

**Say only what the evidence carries.** Every `GRAPH_FACT` cites a
`query_execution_id` and a path, and anything the rows do not contain is a
`REASONED_SUGGESTION` or is not said at all. `customer_name` arrives
`[REDACTED]`: describe the customer by account, order and product, and never
write a name you were not given. No invented order numbers, quantities, dates,
SKUs, bay ids or statuses — not as a placeholder, not as an example, not to
make a sentence read better. An absent value is reported absent.

**Answer the question the associate can answer.** They hold the customer and
the item; they rarely hold the paperwork. Asking "which order number?" against
five candidates is the question of last resort, and the release ranks it that
way — `orderNumbers` sits at clarification priority 20, below email, phone,
name, ZIP, SKU and product. `suggested_discriminators` ranks what actually
splits the candidates in front of you: prefer the measured basis over the
configured one.

## How to reason

Read the system prompt's guidance and follow it. The failures worth naming,
because they have all happened here:

- **A field every candidate shares splits nothing**, however identifying it
  looks. Ship-to city on four orders that all go to the same address is not a
  question.
- **Show the product.** A candidate row carrying only header fields draws no
  Product column; traversing `order_has_line` to `order_line` is what puts the
  item on screen. Count the lines before deciding it is too many.
- **A confirmation settles everything it names.** "Confirm WESTFIELD PLUMBING on
  account SACRAMENTO" settles both; scope every later query to that account and
  do not ask again — including the courtesy confirmation they have just given.
- **Delivery is queryable now.** `fleetwise_status`, `delivery_signature_at` and
  `ship_via_code` are on `sales_order`, so "which of these eleven actually
  arrived" is a question the graph can answer. One delivered order among eleven
  is a far better lead than a list of dates.

## After the turn

`POST /api/v2/order-agent/conversations/<id>/turns` answering `200 OK` in
`.runtime/linux-validation/logs/backend.log` means the turn committed. A `503`
means it failed — the UI shows the reason, and the backend log is the place to
look. Report what you sent and what came back, briefly, rather than narrating
each file write.
