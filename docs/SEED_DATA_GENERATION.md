# Seed data generation

`backend/scripts/generate_seed_data.py` builds a synthetic source corpus sized
by `backend/config/seed/generation.yaml`. It replaces the small real sample that
was loaded from `return_discovery_order_analysis_package/files`; that sample
remains the reference for what real documents look like.

```bash
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \
    backend/scripts/generate_seed_data.py
```

Change volumes in the config, not in the script:

| Key | Default |
|---|---|
| `counts.customers` | 1000 |
| `counts.products` | 10000 |
| `counts.orders` | 10000 |
| `counts.warehouses` | 100 |
| `seed` | 20260812 — same number, same corpus |

## Why it reads the schema instead of copying the samples

The generator writes each value at the `physical_path` the active schema
declares, honouring `record_path`, `explode` and the `where` selectors. A
document built that way satisfies the schema by construction, and a schema
change is picked up without editing the generator.

Transcribing the sample shapes by hand would have been quicker and wrong: a
mistyped path yields a document that looks correct and projects nothing, and an
empty projection is indistinguishable from a source that had no data. The script
therefore verifies every declared path against a generated document **before**
anything is written, and refuses rather than loading a corpus the schema cannot
read.

## Realism rules that carry weight

- **Emails derive from the name they belong to** (`marcus.feldman@example.com`).
  The clarification policy ranks email above every narrowing signal, so a search
  by email and a search by name must find the same customer.
- **Phone numbers are unique by construction** — allocated from a shuffled index
  over a fiction range, not generated and retried. At a thousand customers a
  collision is otherwise near-certain, and a duplicate makes phone search
  ambiguous in a way no test would catch.
- **Addresses are drawn as a unit** from a city/state/ZIP table, so nothing
  lands in "Dallas, VT 90210". A geographically impossible address makes a
  location search look broken when it is the data that is wrong.

The corpus is unmistakably synthetic: `example.com` domains, the 555 exchange
reserved for fiction, and names assembled from pools in the script. It must
never be confused with real customer data.

## The warehouse structure is provisional

**Still provisional, and the schema no longer generates against this.** W2.4 added
`warehouse` and `bay` entities, and they are bound to
`platform.bay_configuration` in SQL Server — **not** to the `warehouseMaster`
collection below. That collection is not in `return_source` and never was, so an
entity declared against it would have carried physical paths that resolve on no
document; the analyzer was pointed at the one warehouse identity this platform
can actually observe, which is `warehouse_id` on the bay master.

The consequence for this generator: `warehouseMaster` is written and **nothing
reads it**. The warehouse ids it mints do not join the graph's `Warehouse` nodes,
which come from the bay table. Either wire the two together or stop writing it;
leaving it is a corpus that looks like it feeds something.

What the generator writes to `warehouseMaster` remains **invented for this
project's needs and not derived from any real Ferguson structure**:

```json
{
  "_id": "WH001",
  "warehouseId": "WH001",
  "name": "Charlotte Distribution Center",
  "address": { "line1": "...", "city": "CHARLOTTE", "state": "NC", "postal_code": "28241" },
  "phone": "(704) 555-0142",
  "bays": ["BAY-01", "..."],
  "capacityUnits": 2400,
  "acceptsHazmat": false,
  "acceptsOversize": true
}
```

It exists so the warehouse ids scattered across orders resolve to something with
a name and a location, and so return-side work has a master to point at. Treat
the field names as placeholders.

When a real warehouse master arrives it is a **rebinding plus a re-analysis**,
not an edit: point a source binding at it, re-run
`backend/scripts/add_warehouse_bay_entities.py` against it, and let the compiler
produce the entity from what the source declares. Reconciling these field names
with the real ones is part of that, and the placeholder names above are why.

`return_handling_unit` still carries bay and warehouse ids as plain properties
rather than as relationships. Making them edges is a schema migration touching
existing nodes — destructive in D8's classification — and W2.4 is additive.

## After generating

The graph is built from the source collections, so it needs a rebuild:

```bash
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \
    backend/scripts/build_knowledge_graph.py
```

At the default volumes expect roughly 60–70k nodes and a few minutes.
