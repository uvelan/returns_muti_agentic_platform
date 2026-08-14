# Reset

**Current as of 2026-08-14, commit `dcbb7dc`.**

## Full reset — any state to a working platform

```bash
./scripts/linux/reset_all.sh
```

Takes the environment from **any** state to a running platform with fresh data. In
order:

```text
1. stop the host processes
2. reset and start infrastructure          (destroys volumes)
3. re-seed Vault                           (step 2 destroyed its volume)
4. load the reference dataset
5. start the host processes
6. build the knowledge graph
```

**Step 6 is the one that had no script at all.** Loading the source collections
leaves Neo4j **empty**, so the copilot searches a graph with no nodes and truthfully
reports finding nothing — which reads as a broken agent rather than a missing build.
That is the single most confusing state this platform has: every service is up,
every health check passes, and discovery finds nothing.

**Step 3 is not optional and nothing else does it.** Step 2 destroys Vault's volume,
and without re-seeding, every credential resolution fails.

## The steps individually

```bash
python backend/scripts/load_reference_dataset.py   # wipes, then loads
python backend/scripts/build_knowledge_graph.py    # source -> Neo4j
```

## Infrastructure-only reset

```bash
CONFIRM_RESET=YES ./scripts/infra.sh reset
```

**Deletes local infrastructure volumes.** Requires the explicit confirmation
variable — the guard exists because this destroys Vault, and Vault is the only place
the generated infrastructure credentials live.

`./scripts/linux/reset_docker_environment.sh` resets the Docker environment more
broadly.

After any volume reset:

```bash
python3.13 scripts/vault/bootstrap_local_vault.py
./scripts/prepare_runtime_configuration.sh
```

## Seed data reset

Seed volume is controlled by `backend/config/seed/e2e_seed_manifest.json`. Linux and
other full-scale environments use those JSON counts when `PLATFORM_SEED_RECORD_LIMIT`
is empty.

Set `PLATFORM_SEED_RECORD_LIMIT` to a positive integer of at least `10` to cap each
generated collection for a lower-resource run:

```bash
PLATFORM_SEED_RECORD_LIMIT=1000 ./scripts/linux/07_seed_and_validate_data.sh
```

The default manifest expands deterministically to 10,000 customers, 20,000 products,
1,000,000 orders and 1,000,000 shipments, including multi-line orders and the
positive, negative and review-required scenarios. Return and support-case
collections stay **empty** before a demo — a demo that starts with returns already
in it cannot demonstrate creating one.

Million-order definitions are **lazy** and source writes use bounded bulk batches, so
configuration loading and validation do not materialize the whole dataset in memory.

### Seed endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/seed-data` | Readiness and the last applied record limit |
| `POST` | `/api/v1/seed-data/apply` | Apply with `{"recordLimit": 1000}` |
| `POST` | `/api/v1/seed-data/reset` | Delete the active seed version, apply the requested limit |
| `GET` | `/api/v1/seed-data/operation` | Progress and cancellation state |
| `POST` | `/api/v1/seed-data/cancel` | Stop at the next safe persistence boundary |
| `POST` | `/api/v1/seed-data/delete` | Delete only active seed-owned data. Requires `{"confirmation": "DELETE SEED DATA"}` |

**Apply, reset and delete are restricted to development and test environments.**
Only one seed mutation runs per API process at a time; concurrent requests get
`409`.

The Seed Data page accepts a per-run limit from `10` to `1,000,000`. The JSON
manifest and `PLATFORM_SEED_RECORD_LIMIT` remain **hard upper bounds**, so the UI
cannot exceed the configured environment capacity. The page polls the active
operation, shows progress, and can request a cooperative stop.

Seed evidence uses the Vault-aware validation fingerprint key to produce a keyed
digest. Graph synchronization selects every record matching the active seed version
and digest, **rejects digest drift**, and does not use an arbitrary record limit for
an active seed projection.

## The reference dataset

`backend/fixtures/reference_dataset/` holds 100 real orders with the identities
removed.

**Their structure is exact.** The active schema reads through it by physical path,
and a flatter approximation extracts nothing. The business vocabulary is real: order
numbers, dates, statuses, warehouse codes, product descriptions, SKUs, quantities,
prices. Names, addresses, contact details and every payment or identity field are
synthetic.

`backend/scripts/deidentify_reference_dataset.py` regenerates it from the raw
extract. It is a key-name denylist **plus a proof**: after scrubbing, it searches the
output for every value the source held under a sensitive key and **fails the run on a
survivor**.

The proof is what makes the denylist trustworthy. It caught four real leaks a 400-key
pattern list had missed — including `custPONumber` holding a site address,
`pickedEmpId` holding a person's name, and `drvLicNum` holding a phone number despite
the pattern already containing `licen`. Contact details are now also matched by
**shape**, so a field nobody classified still cannot carry a routable number out.
Output is byte-reproducible.

`backend/tests/test_reference_dataset.py` holds the committed file to both
properties: no real contact details, and all three collections still join.

## Resetting configuration only

There is no "reset configuration" operation, deliberately — releases are immutable
and rollback is forward-only. To return to a known configuration, **promote the
release you want**.

If no release is active at all:

```bash
./scripts/prepare_runtime_configuration.sh
```

Publishes and validates the initial graph configuration when none is active.
Releases created before the multi-domain migration may contain only
`RETURN_PLATFORM`; publish a complete three-domain release before starting upgraded
processes, and confirm the active release contains `RETURN_PLATFORM`, `AI_GATEWAY`
and `DEPENDENCY_SIMULATION` — production and staging **fail closed** without the
last two.

## Rebuilding the graph without a full reset

A destructive schema change rebuilds the graph into a **new generation** and swaps.
It does not require a data reset:

```bash
curl -fsS http://127.0.0.1:8000/api/schema-releases/{id}/migration-plan | jq
# then activate, which runs the strategy the plan named
```

To rebuild from current sources without a schema change, trigger a `FULL`
non-incremental sync from `/sync` or `POST /api/graph-sync/runs`.

**Either way the active generation keeps serving until the replacement validates.**
A rebuild is not an outage.

## After any reset — verify

```bash
curl -fsS http://127.0.0.1:8000/health/ready              | jq
curl -fsS http://127.0.0.1:8000/api/config/adoption       | jq
curl -fsS http://127.0.0.1:8000/api/graph-sync/runs       | jq '.data[0]'
```

And confirm the graph is not empty — the failure mode described at the top of this
document produces no error anywhere else.

## Related

- [`startup.md`](startup.md)
- [`recovery.md`](recovery.md)
- [`troubleshooting.md`](troubleshooting.md)
- [`../SEED_DATA_GENERATION.md`](../SEED_DATA_GENERATION.md)
