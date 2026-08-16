# Seed data generation

`backend/scripts/generate_seed_data.py` builds a synthetic source corpus sized
by `backend/config/seed/generation.yaml`. `backend/scripts/seed_ferguson_idiom.py`
holds the vocabulary it generates in and records what each observation was
derived from.

```bash
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \
    backend/scripts/generate_seed_data.py
```

| Key | Value | Meaning |
|---|---|---|
| `counts.customers` | 1000 | `customerOutboundCDM` documents **in total**, real included |
| `counts.products` | 1000 | `lkpSearchProduct` documents in total |
| `counts.orders` | 10000 | `salesInv` documents in total |
| `seed` | 20260812 | same number, same corpus |

Shipments and warehouses have no count. One shipment is written per generated
order that carries a shipped line, and one warehouse document per distinct
inventory warehouse id the corpus actually uses, so both are outcomes of the
corpus rather than free choices.

## The real documents are backed up, preserved, and renamed — in that order

`backend/fixtures/real_ferguson_source/` holds the only genuine Ferguson data in
the system: the real `salesInv`, `customerOutboundCDM`, `lkpSearchProduct` and
`shipmentInfo` documents, as MongoDB Extended JSON.

The generator **writes that directory once and never overwrites it**. On every
later run it reads the originals from disk rather than from Mongo, which is what
makes the rename idempotent — taking the originals from the database would mean
the second run renamed the already-renamed documents and the third renamed
those.

Replacement is `_id $nin <the backed-up ids>`, not a marker match. A marker only
removes what a previous run of *this* script wrote, and these collections have
held documents from two earlier generators.

### Customer names are individual people, real records included

An operator decision, 2026-08-16: every customer in this corpus is a person,
with the email derived from the name. `ATLAS MECHANICAL SERVICES` and
`TAMILLO PLBG` are people now. **Everything else on a real document is preserved
byte for byte** — `_id`, order numbers, line structure, SKUs, dates, statuses,
account ids. The name is replaced where the name is *stated* (header `custName`,
`shipToName`, the embedded contact rows, `placedByName`, the cardholder) and
nowhere else, so `shipToKey` and every identifier survive untouched.

Names are keyed on `(accountId, custId)`, which is `customer`'s natural key: the
same ERP customer number in two branches is two customers, and one person spread
across both would merge them in the graph.

Four of the 101 real orders stated no email anywhere. Those get one written onto
a contact row that carried no contact of its own — `contact_value` is
`COALESCE(email, phone_number)`, so such a row projects on a null key and is
lost. The other 97 already stated an email and had it rewritten in place; a
second one is never added.

**To restore the originals, load the backup back over the collections.**

## Where the realism comes from

Nothing was fetched from fergusonhome.com or anywhere else. The idiom was mined
from data already in `return_source`:

- **SKU shapes**, from the 482 distinct vendor product codes on real order
  lines: `Q1685`, `R7010108781`, `ADWVCFAMRPM`, `PSRGW1212`. Four shapes cover
  the observed set. The previous `SKU%07d` made every scan-the-box search look
  uniform in a way the real catalogue is not.
- **Description conventions**, from the 481 distinct real `productDesc` values:
  uppercase, size-led, ERP-abbreviated. `prodLongDesc` and `webDisplayName`
  expand the same string, because `productDesc` is too abbreviated to search.
- **`lineType` distribution** — `MP` 499 / `C` 39 / `CB` 24 / `SP` 8 / `NA` 1 /
  `F` 1 — reproduced rather than drawn uniformly.
- **Lines per order**, the real empirical multiset. 29 of 101 real orders carry
  one line and one carries 49. Drawing uniformly from a range makes single-line
  orders, the commonest return case, rare.
- **Branch accounts, warehouse ids, order-number prefixes, order statuses and
  ship-via codes**, all weighted as observed.

### The 482 real catalogue numbers now have product master rows

`_mine_real_products` builds one `lkpSearchProduct` per `masterProductId` any
real order line names, using **the id, SKU and description the line already
carries**. `line_references_product` matches `order_line.master_product_id`
against `product.product_id`, so before this the graph answered "no product" for
every real order line — finding D5.

Those rows carry **no vendor, no department and no finish**. The order line does
not state them, and a plausible guess against a real catalogue number would put
a claim in the corpus that no source ever made. They carry a `provenance` block
naming the fields that were stated.

### Colour and finish

`eco.colorFinish` is the path the one real product document carries its finish
on (`["White"]`), and the generated products write it there. Eleven finishes,
from the trade's real vocabulary — polished chrome, brushed nickel, matte black,
oil-rubbed bronze, stainless for plated trim; white, biscuit, bone, black for
china and seats; white, brown, sandtone for grilles and registers.

**Only where a finish is real.** A lavatory faucet has one, a flex duct does
not, and a run of PVC does not. `Category.finishes` is empty for every category
where inventing one would be fabrication. Categories that do carry a finish are
weighted up in the generation cycle — the one deliberate distortion in the file
— so that roughly half the generated catalogue has data behind an attribute that
previously had one product behind it.

**The active schema declares no colour field on `product`**, so the value
reaches Mongo and not the graph. It is searchable today only through
`product_description` and `web_display_name`, both of which state it. Declaring
a `color_finish` field on the `product` entity is the outstanding half.

## Realism rules that carry weight

- **Emails derive from the name they belong to** (`richard.reynolds@…`,
  `r.reynolds@…`). The clarification policy ranks email above every narrowing
  signal, so a search by email and a search by name must find the same customer.
- **Phone numbers are unique by construction** — allocated from a shuffled index
  over a fiction range, not generated and retried. At a thousand customers a
  collision is otherwise near-certain, and a duplicate makes phone search
  ambiguous in a way no test would catch.
- **Names are drawn without replacement** from the full cross product of the
  given-name and surname pools, so a thousand customers cannot become a hundred
  variations of one surname.
- **Addresses are drawn as a unit** from a city/state/ZIP table, so nothing
  lands in "Dallas, VT 90210".
- **Descriptions are unique** within the generated catalogue. Two catalogue
  numbers reading `2 IPS DEEP ESC CP` from the same vendor make a product search
  return two rows an associate cannot tell apart.

The corpus is unmistakably synthetic: RFC 2606 reserved domains, the 555
exchange reserved for fiction, and names assembled from pools in
`seed_ferguson_idiom.py`. It must never be confused with real customer data.

## The customer-account bridge

Generated CDM documents carry `party[].partyMainCusts[].mainCusts` holding
`BRANCH*CUSTID`, which is what the real document carries and what
`customer_account` reads. Orders draw their customer from the same generated
bridges, so `salesInv.custId` and the CDM agree by construction.

They deliberately do **not** carry `party[].custAccts[].additionalCustomerInfo[]`.
No real CDM document has a `custAccts` array at any level; the previous
generator built one *from the declared schema path*, manufacturing exactly the
shape the declaration asserted and the source lacked, which cleared the entity's
validation error dishonestly. See D41 and D48 in
`RETURN_COPILOT_EXECUTION_STATE.md`. `scripts/load_reference_dataset.py` carried
the same fabrication and has been corrected to the real path.

## Known real-data mismatch: shipments never join their order

The 100 real `shipmentInfo` documents put `BRANCH*ORDER` in
`shipmentInfoEventData.trilOrdNum` (`"DIST*1000100"`), while
`order_shipped_as` joins `shipment.sales_order_number` to
`sales_order.sales_order_number`, which is the bare order id. Those rows can
therefore never form a `SHIPPED_AS` edge — and their order numbers name no order
in the extract either. Generated shipments carry the bare number so the edge
does form. **The real rows are left as they are**; the join was not loosened to
accommodate them.

No shipment is invented for a real order. Minting a tracking number against a
genuine Ferguson order is the `TRK-98421049281` fabrication in a new costume.

## The warehouse structure is still provisional

**Still provisional, and the schema does not generate against it.** W2.4 added
`warehouse` and `bay` entities bound to `platform.bay_configuration` in SQL
Server — **not** to the `warehouseMaster` collection. That collection is not in
the active schema and nothing reads it.

What is written there remains **invented for this project's needs and not
derived from any real Ferguson structure**. One change: the ids are now the real
inventory warehouse ids the order lines carry (`1969`, `3526`, `686`, …), so a
warehouse id on an order at least resolves to a document with the same
identifier rather than to a `WH001` that appears nowhere else. Treat the field
names as placeholders.

When a real warehouse master arrives it is a **rebinding plus a re-analysis**,
not an edit: point a source binding at it, re-run
`backend/scripts/add_warehouse_bay_entities.py` against it, and let the compiler
produce the entity from what the source declares.

## Why the builders write a template rather than walking the schema

The previous generator wrote a value at each declared `physical_path` and
nothing else. That satisfied the schema and produced documents no ERP would
emit — `upc_code-38342`, `brand_type-403988`, `SKU0000001` — which cannot be
used to judge whether a screen reads well, and a manual-testing corpus is for
exactly that.

The builders now write the enclosing structure the real documents carry and put
real-idiom values at the declared paths. The guarantee the old approach gave —
that a schema change is picked up without editing the script — is replaced by
`_verify`, which resolves every declared path against a generated document of
each kind before anything is written, and **refuses the load naming the missing
field**. Loud failure instead of silence, at the cost of a code change when the
schema moves.

## After generating

The graph is built from the source collections, so it needs a rebuild. Pass a
per-asset record cap above the largest collection, or the build truncates:

```bash
PYTHONPATH=backend/src backend/.venv/Scripts/python.exe \
    backend/scripts/build_knowledge_graph.py 20000
```

At these volumes expect roughly 65k nodes and several minutes. A blue/green
rebuild mints a new generation and cuts over to it; "the sync wrote N nodes" and
"a generation activated" are different claims, and the script prints both.
