# Order Discovery: Field Corrections and Strong Anchors

**Date:** 2026-08-02  
**Purpose:** Minimal implementation guide for repository changes  
**Scope:** Order Discovery fields, anchors, and confirmation scenarios only

## 1. Required field corrections

### 1.1 Order identity

The repository currently treats `orderId`, order number, Trilogie order number, and `orderReference` as if they were interchangeable. They must be separate internally.

The user does not need separate inputs for each identifier. Order Discovery should accept one user-facing field:

```text
orderReference
```

The platform must resolve that value through exact, typed resolvers and retain both the matched identifier type and the canonical result.

Confirmed identity distinction:

```text
Canonical order key:
    LOGON*ORDERNUMBER

Source order document ID for an order/header document:
    commonly LOGON*ORDERNUMBER*VERSION
    exact physical shape remains owned by the writer-specific adapter

Canonical order-line identity:
    canonical order key + immutable line number
```

The line number is not part of the canonical order header key. It is added only when identifying a particular order line. A `salesInv` source `_id` must not be assumed to contain a line number because an active writer may store an order with embedded lines or use separate line documents.

| Required field | Source candidate | Current problem | Required correction |
|---|---|---|---|
| `canonicalOrderKey` | `eventMeta.orderKey` | Not captured | Add exact `LOGON*ORDERNUMBER` field; use as the canonical order identity |
| `rawOrderNumber` | `eventMeta.orderNumber` or approved writer-specific path | `salesHdrEventData.orderId` is treated as the order number | Preserve the raw source value; validate path by writer version |
| `sourceOrderDocumentId` | Mongo `salesInv._id` | Registered in schema but dropped from candidate and lock | Carry exact `_id` through candidate, confirmation, and discovery lock |
| `sourceWriterSchemaVersion` | `eventMeta`/writer metadata | Not captured | Required to select embedded-line versus line-document adapter |
| `sourceRevision` | source update/version metadata | Not bound to confirmation | Store in candidate and lock; re-read before confirmation |
| `accountLogon` | canonical order key/account mapping | Generic `customerReference` does not prove logon scope | Store explicitly; never derive it from order number |
| `webOrderNumber` | approved web-order path | Optional field exists but mapping is not proven | Retain separately and record its mapping to canonical order |
| `trilogieOrderNumber` | approved Trilogie path | Falls back to the same `orderId` used by `orderReference` | Remove silent fallback; populate only from proven source path |
| `sourceTransactionId` | source event metadata | Missing | Retain as source evidence, not canonical identity |

Required canonical relationship:

```text
canonicalOrderKey = accountLogon + "*" + rawOrderNumber
```

Do not use:

```text
order number alone
Mongo _id alone
web order number alone
source transaction ID alone
```

as the global canonical order identity.

#### 1.1.1 Unified order-reference resolver

The single `orderReference` field narrows the search by recognizing the submitted value's form:

```text
input orderReference
    |
    +-- matches LOGON*ORDERNUMBER -> exact canonical order-key lookup
    +-- matches W######### web-order form -> exact web lookup -> map with evidence
    +-- matches source document-ID form -> writer-version-aware source-ID lookup
    +-- otherwise raw order number -> exact lookup inside authorized account/logon scopes
```

| Resolver result | Required behavior |
|---|---|
| Zero exact candidates | Ask for another strong anchor: account/job, PO, date, product, or scanned line |
| One exact candidate | Re-read the source, validate current state, and display eligible lines |
| Multiple exact candidates | Ask for account/job, PO, date, product, or another strong anchor |
| Near or fuzzy ID match only | Do not auto-correct or bind; request confirmation or another anchor |

After resolution, retain these fields separately:

- `inputOrderReference`
- `matchedReferenceType`
- `canonicalOrderKey`
- `accountLogon`
- `rawOrderNumber`
- `sourceOrderDocumentId`
- `sourceWriterSchemaVersion`
- `sourceRevision`
- `sourceTransactionId`
- `webOrderNumber`
- `trilogieOrderNumber`
- `identityEvidenceReferences`

This provides one simple input without collapsing different source identities into one database field.

### 1.2 Order-line identity

| Required field | Source candidate | Current problem | Required correction |
|---|---|---|---|
| `immutableLineNumber` | `salesLines.salesLnsEventData.lineNumber` or writer-specific equivalent | Configuration searches `lineData.orderLineId`, `lineId`, and `salesLineId` | Validate active writer paths and map immutable line number explicitly |
| `sourceLineDocumentId` | line document `_id`, when line-document writer is used | Missing | Retain exact source line document identity separately |
| `canonicalOrderLineKey` | canonical order key + immutable line number | Runtime can generate `<order>:LINE:<array-position>` | Generate only from authoritative line number; never from array position |
| `lineIdentityQuality` | adapter result | Missing | Use `VERIFIED`, `AMBIGUOUS`, or `UNCONFIRMABLE` |

Required rule:

```text
canonicalOrderLineKey = canonicalOrderKey + immutableLineNumber
```

If the line number is missing, the line must be `UNCONFIRMABLE`. Do not create a synthetic confirmable ID.

### 1.3 Candidate and discovery-lock fields

Add these fields to `OrderCandidate` and `DiscoveryLock`:

```text
inputOrderReference
matchedReferenceType
canonicalOrderKey
accountLogon
rawOrderNumber
sourceOrderDocumentId
sourceWriterSchemaVersion
sourceRevision
sourceTransactionId
webOrderNumber
trilogieOrderNumber
orderSource
identityQuality
identityEvidenceReferences
```

Add these fields to each line candidate and locked line:

```text
canonicalOrderLineKey
immutableLineNumber
sourceLineDocumentId
sourceProductId
masterProductId
alternateCode
descriptionAtOrder
orderedQuantity
shippedQuantity
unitOfMeasure
lineIdentityQuality
```

The discovery lock must contain the exact identity and source revision shown to the associate.

### 1.4 Source configuration corrections

Replace generic field-path lists with versioned adapters.

Minimum adapters:

```text
SalesInvEmbeddedOrderAdapter
SalesInvLineDocumentAdapter
OrderOutbndLegacyFallbackAdapter
CustomerCdmVersionedAdapter
ShipmentInfoVersionedAdapter
InvoiceMemosCdmAdapter
```

Current configuration corrections:

| Current configuration | Correction |
|---|---|
| `order_number_paths` reads `salesHdrEventData.orderId` | Map canonical key and raw order number independently by schema version |
| `trilogie_order_paths` falls back to `salesHdrEventData.orderId` | Remove fallback unless source owner confirms identical semantics |
| `line_id_paths` reads generic IDs inside `lineData` | Add authoritative immutable line-number mapping |
| `tracking_order_field` returns an order-like value | Resolve tracking through `shipmentInfo`, then re-read `salesInv` by canonical order key |
| Only customers, `salesInv`, and products are required datasets | Require or capability-gate `shipmentInfo`, invoice, location, and fallback sources |

### 1.5 Graph corrections

| Current graph field | Required graph field |
|---|---|
| `SalesOrder.sales_order_number` unique key | `SalesOrder.canonical_order_key` unique key |
| `OrderLine.order_line_key` based on current runtime ID | Canonical order key plus immutable line number |
| `Shipment.tracking_number` as shipment key | Separate shipment identity and tracking reference |
| Relationships joined by display order number | Relationships joined by canonical order key |

Neo4j is a search projection. Before confirmation, the authoritative source must be re-read.

### 1.6 Fields to exclude

Do not map these into discovery candidates, locks, graph nodes, AI prompts, logs, traces, or frontend responses:

```text
payment token key
payment authorization code
card/account number, including masked values
cardholder name
card expiration
card billing address
raw payment-method details
```

## 2. Strong-anchor mapping

An anchor is strong only when it has exact input, correct account scope, an authoritative resolver, and source revalidation.

### 2.1 Tier A: identity anchors

| Anchor | Maps to | Required resolver | Confirmation behavior |
|---|---|---|---|
| Exact canonical order key `LOGON*ORDERNUMBER` | One canonical sales order | Exact `salesInv` lookup | Re-read source and show authoritative lines |
| Exact source order document ID | One source order version/document | Versioned `salesInv` adapter | Internal revalidation only; do not display as order number |
| Exact web order number | Web order, then canonical ERP order | Web-to-order mapping adapter | Retain both IDs and mapping evidence |
| Exact return/RMA number | Prior return, then original order | OMC return-history resolver | Resolve original canonical order and revalidate it |

The user-facing `orderReference` is not itself a new canonical identity. It is an input envelope that may contain a canonical key, web order number, raw ERP order number, or source document identifier. Its resolver must record which interpretation matched.

### 2.2 Tier B: strong resolver anchors

| Anchor | Maps to | Main ambiguity | Required behavior |
|---|---|---|---|
| Exact tracking number | Shipment context | Shipment may contain several items or split lines | Resolve shipment to order; confirm line separately |
| Exact invoice number | Invoice lines | One invoice can contain multiple orders | Inspect all lines and return grouped order candidates |
| Exact customer/account ID | Customer account | Account can have many orders/job accounts | Return bounded authorized candidates |
| Exact customer PO | Orders under an account | PO values may repeat | Require account scope and possibly job/date |
| Exact FEI PO/credit number | Related order or financial document | May point indirectly to original order | Resolve through governed source join |

### 2.3 Tier C: supporting anchors

These narrow candidates but must not auto-confirm an order:

```text
SKU / MPID / alternate product code
job name or job account
ordered by
order date or date range
order status
customer phone
customer email
customer/company name
delivery city or postal code
product description or color/finish
```

## 3. Real-world scenarios

| # | Scenario | Anchor flow | Expected result |
|---|---|---|---|
| 1 | Associate enters `DALLAS*0672657` in the single order-reference field | Recognize canonical order-key form -> exact `salesInv` read | One account-scoped order; authoritative lines displayed |
| 2 | Customer gives only `0672657` in the same field | Treat as raw order number -> search authorized account/logon scopes | Resolve inside account scope; never search globally as unique |
| 3 | Same number exists under two logons | Raw order number -> two canonical keys | Show two scoped candidates; require account selection |
| 4 | Customer gives `W...` online order in the same field | Recognize web-order form -> mapping -> canonical order | Preserve input, web, and ERP IDs; confirm canonical order |
| 5 | Customer gives an offline branch order | Exact order number + main/job account | Resolve accessible online/offline order records |
| 6 | Customer gives a partial order number | Explicit partial mode + account scope | Return candidates; require another anchor and confirmation |
| 7 | Order has leading zeroes | Exact string preservation | No integer conversion or zero stripping |
| 8 | Order is alphanumeric | Exact string lookup | Preserve all source characters |
| 9 | Customer gives tracking number | Tracking -> shipment -> canonical order | Show shipment context; do not auto-select line |
| 10 | Shipment contains several products | Tracking + shipment items + order lines | Correlate product/quantity; require line confirmation |
| 11 | One line is split across shipments | Canonical order line -> all shipment evidence | Keep one line identity with multiple shipment records |
| 12 | Same SKU occurs on two order lines | SKU within confirmed order | Show both lines; require immutable line selection |
| 13 | Customer gives invoice number | Invoice -> all invoice lines -> orders | Return one or more grouped order candidates |
| 14 | Invoice line has no Ferguson line number | Invoice + product + confirmed order | Mark correlation ambiguous; do not fabricate line join |
| 15 | Customer gives PO and job name | PO + job/account + date | Narrow orders; do not assume PO is globally unique |
| 16 | Customer knows only product/SKU | Product purchase history + account | Return recent matching orders; require order and line confirmation |
| 17 | Purchase is less than 24 hours old | Direct order source instead of lagging purchase history | Do not return “not found” based only on history lag |
| 18 | Phone/email belongs to several accounts | Contact -> customer accounts | Show authorized accounts; require account selection |
| 19 | User lacks access to a job account | Anchor resolution + authorization filter | Do not disclose candidate/order details |
| 20 | Source order changes after candidates display | Candidate revision -> source re-read | Reject stale confirmation and refresh candidates |
| 21 | Source line has no immutable line number | Order found, line identity incomplete | Mark `UNCONFIRMABLE`; route to review |
| 22 | Customer provides an RMA/return number | Return -> original canonical order | Revalidate order and show prior-return evidence reference |

Example retained identity after a web-order lookup:

```text
inputOrderReference: W000123456
matchedReferenceType: WEB_ORDER_NUMBER
webOrderNumber: W000123456
canonicalOrderKey: DALLAS*0672657
accountLogon: DALLAS
rawOrderNumber: 0672657
trilogieOrderNumber: 0672657
sourceOrderDocumentId: <exact salesInv _id>
```

Only `canonicalOrderKey` is used to bind the order. The other values remain source and search evidence.

## 4. Source usage and field-mapping reconciliation

The proposed source tables contain useful discovery ideas, but they mix three different states:

- fields and collections implemented in the current repository;
- target sources identified by the analysis but not yet implemented;
- inferred field paths and joins that are not proven by the manual files or repository.

Status meanings:

| Status | Meaning |
|---|---|
| **Confirmed current** | Present in current configuration/schema and used by repository code |
| **Conditional target** | Valid target role, but requires a source contract, adapter, or implementation |
| **Incorrect as written** | Conflates identities, uses a conflicting path, or treats an unproven join as authoritative |
| **Not implemented** | No active repository configuration or runtime resolver was found |

### 4.1 Source usage strategy

| Source / collection | Correct role | Status | Correct lookup or join | Required correction |
|---|---|---|---|---|
| `salesInv` | Primary order header and line source | **Confirmed current** | Resolve an exact typed order reference, then bind to `canonicalOrderKey` and retain exact source `_id` | Do not state universally that `_id = ACCOUNT*ORDERNUMBER`. The canonical key is `LOGON*ORDERNUMBER`; the physical `_id` is separate and writer-version-owned. Current runtime still looks up `salesHdrEventData.orderId` and must be corrected. |
| `customerOutboundCDM` | Customer/contact/account resolution | **Confirmed current, join conditional** | Customer authority -> authorized account/logon -> canonical order candidates | Current repository schema uses top-level `customerId`, `phoneNumber`, `email`, and `custAccts`. The proposed `party.customerId -> salesInv...orderCust` join is not proven; current order snapshot uses `salesHdr.salesHdrData.custId`. |
| `shipmentInfo` | Tracking-to-shipment-to-order resolution | **Confirmed current** | `shipmentInfoEventData.trkNum` -> `shipmentInfoEventData.trilOrdNum` -> typed order resolver -> canonical order | The current repository joins `trilOrdNum` directly to raw sales order number. Production logic must add account/logon resolution and retain mapping evidence. Carrier and shipped date are registered; authoritative delivery date is not yet registered. |
| `invoiceMemosCDM` | Invoice-to-order and invoiced-quantity resolution | **Conditional target / not implemented** | Exact invoice -> invoice lines -> one or more canonical orders and order lines | `accountNumber + orderNumber -> salesInv._id` is not safe as a universal join. An invoice can contain multiple order lines or orders; use line-level evidence and canonical resolution. |
| `lkpSearchProduct` | Product/SKU enrichment and product-led narrowing | **Confirmed current, join conditional** | Exact `productId`, `sku`, or validated `masterProductId` -> matching order lines inside account scope | Do not assert `lkpSearchProduct._id -> salesDtl[].itemNumber`. Current repository uses `salesLines[].lineData.productId` and the manual package says the `masterProductId` join still requires verification. |
| `locationsCDM` | Branch/location enrichment | **Conditional target / not implemented** | Selling or ship-from warehouse reference -> versioned location resolver | The repository currently exposes `salesHdrEventData.sellWhseId` and `shipFromWhseId`. The proposed `eventMeta.invWhse` path is not proven here. Warehouse ID is a reference, not the full location entity. |
| `shipTo` collection | Ship-to master/context resolver | **Not implemented** | Only through an approved collection contract and account-scoped order mapping | No `shipTo` collection resolver or `customerId + orderNumber` join exists in the repository. `salesInv.salesHdr.shipping.shipTo` is currently only an order-time address snapshot. |
| `purchaseHistory_v1` | Customer/product/date narrowing index | **Not implemented** | Customer/account + product/date -> candidate orders -> authoritative `salesInv` re-read | Purchase history is eventually consistent search evidence, not order or quantity authority. The proposed compound join is not implemented. |
| `orderOutbnd` | Explicitly capability-gated legacy fallback | **Conditional target / not active** | Writer-specific source identifiers -> canonical resolver -> `salesInv` revalidation when available | Do not use it as the default source for order, shipment, or tracking. The proposed `eventMeta.sourceTransactionId` and `header.orderNumber` paths need a source contract. |
| `inbndSalesInv` | Historical/context enrichment | **Not implemented** | Context reference only after canonical order resolution | No active schema, configuration, or resolver exists. It must not participate in identity binding until contracted. |

The manual package directly supports useful `salesInv` customer, warehouse, shipping-snapshot, product, quantity, and immutable line-number candidates. It does not prove the proposed invoice, location, purchase-history, ship-to-collection, inbound-invoice, or outbound-order joins.

The supplied `SalesInv.xlsx` also lists payment token, cardholder, masked-card, expiry, and billing-address fields. Those fields must remain excluded from Order Discovery, prompts, logs, graph projections, and general discovery evidence.

### 4.2 Key field mapping

| Business field | Correct canonical/source field | Status | Discovery usage and correction |
|---|---|---|---|
| Order key | `canonicalOrderKey = accountLogon + '*' + rawOrderNumber` | **Required / missing in current runtime** | Durable order identity. Do not equate it automatically with Mongo `_id`. |
| Source order document ID | Exact `salesInv._id` | **Registered but not safely propagated** | Source provenance and optimistic revalidation only; never display as the business order number unless the source contract explicitly says it is the displayed value. |
| Raw order number | `eventMeta.orderNumber` or validated writer-specific alias | **Conditional** | Preserve exact string. `salesHdrEventData.orderId` and `salesHdr.salesHdrData.orderId` are current candidate aliases, not yet a universal canonical contract. Never parse `_id` without a writer-version adapter. |
| Web order number | Exact `W...` reference retained as `webOrderNumber` | **Conditional mapping** | A leading `W` is a resolver hint, not proof of canonical identity. Resolve exactly, retain mapping evidence, then bind the canonical order. |
| Customer ID | Customer-authority key plus order snapshot `salesHdr.salesHdrData.custId` | **Confirmed snapshot / conditional join** | Use customer authority and account authorization. The sales order customer value is a join reference, not sufficient identity proof by itself. |
| Customer name | `salesHdr.salesHdrData.custName` plus customer-authority record | **Confirmed snapshot** | Display and confirmation evidence only. |
| Phone/email | Current repository: `customerOutboundCDM.phoneNumber` and `email` | **Confirmed current paths** | Search customer authority, enforce authorization, and do not expose candidates before scope checks. Proposed nested `party.*` paths require a different adapter contract. |
| PO number | `salesHdr.salesHdrData.custPONumber` | **Strong narrowing anchor** | Exact match within authorized account/logon and date scope; not globally unique. |
| Invoice number | Contracted `invoiceMemosCDM` invoice key | **Not implemented** | Exact invoice resolver, then inspect invoice lines and map each line to canonical order candidates. |
| Tracking number | `shipmentInfoEventData.trkNum` | **Confirmed current** | Strong shipment anchor. Resolve through `trilOrdNum` and the typed order resolver; do not auto-select an order line. |
| Delivery ticket | Writer-validated `salesInv` delivery-ticket field | **Unproven** | The proposed `salesHdr.salesHdrData.deliveryTicketNumber` path is absent from current schema/configuration and manual field evidence. Add only after sample/index validation. |
| Immutable order-line number | `salesLines.salesLnsEventData.lineNumber` or writer-specific equivalent | **High-value candidate** | Combine with `canonicalOrderKey`. Do not use array position. |
| Source product ID | Current historical line: `salesLines[].lineData.productId` | **Confirmed candidate** | Exact product evidence. Do not replace it with current catalog ID. |
| Master product ID | `salesLines[].lineData.masterProductId` and `lkpSearchProduct.masterProductId` | **Conditional join** | Validate equality and cardinality for every supported writer. |
| SKU / alternate code | `salesLines[].lineData.altCode1` or configured `sku` path | **Conditional path** | Strong narrowing evidence inside an account/order scope, but not line identity. |
| Item description | `salesLines[].lineData.productDesc` | **Confirmed historical snapshot** | Use for human confirmation and preserve separately from current catalog description. |
| Product description | `lkpSearchProduct.productDescription` or contracted product-master path | **Confirmed enrichment** | Current-product enrichment only; never overwrite the historical line description. |
| Warehouse / branch | `salesHdrEventData.sellWhseId` and `shipFromWhseId` -> location resolver | **Confirmed references / enrichment not implemented** | Keep selling and ship-from warehouses separate. Resolve location details through `locationsCDM` only when its adapter exists. |
| Source-system code | Current `salesHdrEventData.srcSysCode` plus source provenance | **Confirmed current field** | Routing and provenance evidence, not order identity. Proposed `orderOutbnd.header.srcSysCode` requires its own adapter. |

### 4.3 Correct resolver sequence

```text
one inputOrderReference
    -> classify exact reference form
    -> query the matching source adapter
    -> produce zero, one, or multiple scoped candidates
    -> map the selected candidate to canonicalOrderKey
    -> re-read authoritative salesInv source and revision
    -> enrich with customer, shipment, product, invoice, and location evidence
```

Secondary sources narrow or enrich the search. None may replace `canonicalOrderKey`, immutable line identity, authorization checks, or authoritative source revalidation.

## 5. Repository files requiring changes

| File | Required change |
|---|---|
| `backend/src/return_platform/operations/associate_flow.py` | Replace overloaded candidate/lock fields; remove synthetic line IDs; add source revalidation |
| `backend/src/return_platform/canonical/order.py` | Align discovery identity contract and separate source/canonical IDs |
| `backend/src/return_platform/configuration/return_configuration.py` | Replace generic path-list contract with adapter/version configuration |
| `backend/config/returns/production.yaml` | Add canonical fields/anchors; remove unsafe path fallbacks |
| `backend/config/schema_registry.yaml` | Register canonical key, raw number, source identity/version, and immutable line number |
| `backend/config/data_platform/canonical_mappings.yaml` | Add SalesOrder and OrderLine mappings |
| `backend/src/return_platform/data_platform/graph/sync_service.py` | Project canonical keys instead of display order number/tracking number identities |
| `backend/src/return_platform/data_platform/graph/migrations/0012_order_discovery_fulltext.cypher` | Migrate uniqueness constraint to canonical order key |
| `backend/src/return_platform/operations/order_discovery/source_operations.py` | Replace unconditional stub with authoritative validation port/service |
| `backend/src/return_platform/operations/order_discovery/candidate_retriever.py` | Replace fixed stub and bind results to canonical evidence |

## 6. Minimum completion checks

- Exact canonical order key produces the correct account-scoped order.
- One user-facing order-reference field resolves canonical, web, raw ERP, and source-document forms through typed exact resolvers.
- The resolver records `matchedReferenceType`; it does not store the input directly as canonical identity.
- Same order number under different logons does not collide.
- Raw order number, canonical key, source `_id`, web order, and ERP order remain separate.
- Leading zeroes and alphanumeric order numbers are preserved.
- Missing line number never creates a confirmable synthetic line.
- Tracking and invoice anchors do not auto-select an order line.
- Candidate confirmation re-reads the authoritative source revision.
- Unauthorized account/job orders are not disclosed.
- Payment/card fields are absent from discovery output, graph, prompts, traces, and logs.
- Candidate and lock digests include canonical identity and source revision.
