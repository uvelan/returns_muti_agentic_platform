# UI capture — full-flow run 21 (CONNIE FERRARO / CA806592)

Run 21 executed TC-E2E-02 + TC-E2E-03 steps 1-24 clean (`run-21/report.json`); these
screens were captured from the live console during and after the run, and each one
was opened and visually verified before commit.

> Note on the request wording: the task asked for a run "with nividi" — no customer,
> account, or product matching that name exists in the source data (searched NIVID /
> NVID / NIVI across customers, accounts and job names), so the next fresh customer
> from the pool was used instead.

| Screen | Shows | Flow steps |
|---|---|---|
| S1a / S1b_1-4 | Copilot mid-run: elicitation Q&A (reason, qty, branch, proof — no shipping-class question), progress 4/6, RMA issued card, settlement pane | 7-13 |
| S2a / S2b_1-6, S2e | Copilot after completion: full return-phase transcript, RMA + tracking + label relay, "has the return been delivered?" → return_record_fulfilled + ALL_RETURNS_DELIVERED, RMA card CLOSED | 13-14, 20 |
| S3a | Returns Support work queue (Channel B) | 10-12 (thread content in run-21 JSON archives; completed items leave the queue list) |
| S4a | Operations case list — CA806592 top, COMPLETED_EXTERNAL_SETTLEMENT, 1 RMA | 15 |
| S4b | Operations case detail — case id, lifecycle COMPLETED/terminal, confirmation key carrying the run's conversation id, release adoption | 15 |
| S5a | Shipment console filtered by the case id | 16-17 |
| S5b / S5c | Shipment detail: parcel rail with Delivered terminal, terminal banner, disabled update panel, append-only event log (5 stages, actor + note per event) | 17-20 |

The discovery-phase visuals (misspelled-name fuzzy list, the 12-order graph list,
the explicit confirm prompt) are in `../ui-run20/S1b_*.png`, captured live during
run 20 before that run was aborted by the order-discovery worker hang — the copilot's
resumed view renders the return phase only, so run 20's live captures are the
visual record of steps 1-6. The freight-console visuals remain in `../ui/`
(runs 13-18).
