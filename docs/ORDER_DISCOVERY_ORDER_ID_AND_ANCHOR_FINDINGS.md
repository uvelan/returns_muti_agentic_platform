# Order Discovery: Order Identity, Strong Anchors, and Real-World Scenarios

**Finding date:** 2026-08-02  
**Scope:** Order Discovery only  
**Change status:** Findings only; no runtime code or configuration changes

## 1. Executive finding

The repository **partially captures order number and order ID values, but does not preserve the required identities safely through Order Discovery**.

The most important distinction is:

| Concept | Required meaning | Current state |
|---|---|---|
| Order number | Raw business/display number, such as `0672657`, `WS890466`, or a web order beginning with `W` | Captured indirectly as `salesHdrEventData.orderId` and exposed as `orderReference`; naming and semantics are conflated |
| Canonical sales-order key | Globally scoped identity: `LOGON*ORDERNUMBER`, sourced from `eventMeta.orderKey` | Not captured by Order Discovery |
| Source order document ID | Exact Mongo/source record identity, potentially including version/instance | `_id` is registered and seeded but is not carried into `OrderCandidate` or `DiscoveryLock` |
| Web order number | Customer-facing online order number, normally beginning with `W` | Optional paths and candidate field exist, but the web-to-Trilogie/canonical mapping is not authoritative |
| Trilogie order number | ERP order number | Optional candidate field exists, but configuration falls back to `orderId`, so it is not guaranteed to be independently sourced |

Therefore, the answer to “did we capture order number and order ID?” is:

- **Storage/schema layer:** partially yes.
- **Canonical model:** separate fields exist, but the key format differs from the discovery specification.
- **Order Discovery runtime:** no, not safely. It collapses multiple meanings into `orderReference` and loses the source document ID and canonical `orderKey` before confirmation.

## 2. Evidence from the current repository

### 2.1 Registered and seeded source data

`backend/config/schema_registry.yaml` registers:

- `_id` as the source key;
- `salesHdrEventData.orderId` as the order reference;
- `salesHdrEventData.orderId` as the natural key.

It does not register the confirmed fields:

- `eventMeta.orderKey`;
- `eventMeta.orderNumber`;
- `eventMeta.sourceTransactionId`;
- source writer/schema version for order-shape selection.

The sandbox seed creates:

```text
_id = SANDBOX*<orderReference>
salesHdrEventData.orderId = <orderReference>
```

This makes `_id` and `orderId` appear safely related in sandbox data, but it does not exercise the production form in which source document identity, canonical order key, raw order number, and version are distinct.

### 2.2 Production source configuration

The configured paths are:

```yaml
order_number_paths:
  - salesHdrEventData.orderId
  - salesHdr.salesHdrData.orderId

web_order_paths:
  - salesHdrEventData.webOrderId
  - salesHdrEventData.webOrderNumber
  - salesHdr.salesHdrData.webOrderId
  - salesHdr.salesHdrData.webOrderNumber

trilogie_order_paths:
  - salesHdrEventData.trilOrdNum
  - salesHdrEventData.orderId
  - salesHdr.salesHdrData.trilOrdNum
```

Problems:

1. `order_number_paths` reads an `orderId` field instead of the confirmed `eventMeta.orderNumber`/`eventMeta.orderKey` contract.
2. `trilogie_order_paths` falls back to the same `orderId`, so `orderReference` and `trilogieOrderNumber` can become identical without independent evidence.
3. There is no path for the canonical `LOGON*ORDERNUMBER` key.
4. There is no path carrying the raw Mongo `_id` into the discovery result.
5. There is no adapter/version discriminator for the different `salesInv` physical shapes.

### 2.3 Discovery candidate and lock

`OrderCandidate` stores:

```text
customerReference
orderReference
sourceWebOrderNumber
trilogieOrderNumber
orderSource
...
```

`DiscoveryLock` stores the same order references plus `orderLineId` and `productId`.

Neither stores:

- canonical `salesOrderKey` / `eventMeta.orderKey`;
- raw order number as a separately named value;
- exact source order document ID;
- source writer/schema version;
- source update/version used at confirmation;
- evidence showing how a web order mapped to its ERP order;
- account/logon scope that makes the order number globally unique.

### 2.4 Canonical model is not the discovery model

The canonical `SalesOrder` class does separate:

```text
sales_order_key
source_document_id
account_id
order_id
order_instance_key
```

However, it requires a key shaped as:

```text
TDS:accountId:orderId:orderInstanceKey
```

The discovery package requires:

```text
SalesOrderKey = LOGON*ORDERNUMBER
SourceOrderDocumentId = exact source _id, kept separately
```

More importantly, Order Discovery does not construct or use the canonical `SalesOrder` class when building and locking candidates.

## 3. Ferguson public-site process findings

The public site confirms the following customer-visible behavior.

### 3.1 Account context is part of discovery

Ferguson’s Orders page is available to authorized users and shows online and offline orders under the organization’s master customer and accessible main/job accounts. This means an entered order number is resolved inside an authenticated account scope, even though the public UI does not display an internal `LOGON*ORDERNUMBER` key.

**Inference:** the public process supports the internal requirement that an order number alone is not a safe global identity. Account/logon scope supplies part of the uniqueness and authorization boundary.

### 3.2 Customer-facing order search

The site supports:

- direct “Find an Order” using an order number;
- exact or partial order-number search;
- online orders whose order number starts with `W`;
- advanced search using Credit Number, Ordered By, customer PO, and Ferguson PO;
- refinement by Job, Status, and Time;
- orders placed online and offline;
- visibility across permitted main and job accounts;
- order details containing main order number, PO, dates, status, return number/status, refund date, and proof of delivery.

### 3.3 Fulfillment and tracking

The site shows that:

- an order may be split by an associate for fulfillment;
- order tracking is accessed after opening an order;
- tracking is available for Ferguson-truck delivery and Pro Pick-Up;
- a tracking page can show order number, customer PO, job name, customer, delivery address/date/window, stop number, items, ordered/delivered quantities, delivery team, and invoice number;
- tracking information remains available for 12 months after delivery.

This supports treating tracking as strong shipment-to-order evidence, but **not as automatic proof of one order line**.

### 3.4 Product-led discovery

Product Purchase History allows an authenticated trade customer to find orders containing a product across the organization, including online and in-store purchases. It can show order number, date, job name, and PO.

Important limitations:

- history is limited to approximately 12 months;
- a purchase may take up to 24 hours to appear;
- a product can occur on many orders or multiple lines.

Therefore SKU/product is a useful narrowing anchor, not a unique confirmation anchor.

### 3.5 Return context

The public policy states that normally stocked, non-special-order products may be returned within 30 days if they remain new, resalable, complete, unused, uninstalled, unmodified, and undamaged. Special/non-stock products require manufacturer acceptance and may incur fees.

These facts affect the later return-policy stage. They should not be used to guess or auto-select an order during discovery.

## 4. Recommended anchor strength model

Anchor strength must be evaluated as **identifier + scope + authoritative resolution**, not by string format alone.

### Tier A — Identity anchors

These can identify an order after an exact authoritative read and authorization check.

| Anchor | Required scope/validation | Confirmation rule | Current coverage |
|---|---|---|---|
| Canonical order key `LOGON*ORDERNUMBER` | Exact match in `salesInv`; validate account access and source revision | May produce one order candidate, but line still requires authoritative selection | Missing anchor type and resolver |
| Exact source order document ID | Must be interpreted by a versioned `salesInv` adapter; never shown as the business order number | System/internal revalidation only | Registered at source, dropped by discovery |
| Exact web order number | Resolve web order to canonical/Trilogie order and account scope | Confirm mapped canonical order, not the web number alone | Field exists; authoritative resolver/mapping incomplete |
| Exact RMA/return number | Resolve return record to original canonical order | Useful for repeat-return/support discovery; still revalidate source order | Missing |

### Tier B — Strong resolver anchors

These can sharply narrow candidates but may resolve to more than one order or may not identify a line.

| Anchor | Main ambiguity | Required behavior | Current coverage |
|---|---|---|---|
| Exact tracking number | Identifies shipment context; split shipments and multiple items are possible | Resolve shipment, then order, then correlate/confirm line | Present, but shipment-item correlation is incomplete |
| Exact invoice number | One invoice may contain lines from multiple orders | Inspect all invoice lines and group by canonical order | Missing |
| Exact credit number | Can reference a credit/return rather than original sales order directly | Resolve credit to invoice/order using governed joins | Missing |
| Exact customer/account ID | Customer can have many job accounts and orders | Return bounded orders within authorized account hierarchy | Present but hierarchy is flattened |
| Exact customer PO | PO values can repeat across jobs, customers, or time | Require account scope and usually date/job refinement | Question only; no resolver |
| Exact Ferguson PO / FEI PO | May not uniquely identify the customer’s desired sales line | Resolve then confirm order and line | Missing |

### Tier C — Supporting/narrowing anchors

These must not auto-confirm an order.

| Anchor | Why it is not unique |
|---|---|
| SKU/MPID/product ID | Same product can appear on many orders and multiple lines |
| Job name/account | A job contains many orders; names can repeat |
| Ordered by | One employee places many orders |
| Order date/range | Many orders occur in the same period |
| Order status | Shared by many orders |
| Customer phone/email | Can be shared by multiple contacts/accounts |
| Customer name/company name | Common, mutable, and non-unique |
| Delivery city/postal code | Shared location evidence only |
| Product description | Fuzzy and subject to catalog/historical description drift |

### Tier D — Weak/unsafe inputs

- Partial order number without authenticated account scope.
- Typo-corrected order, tracking, invoice, RMA, PO, customer ID, or SKU.
- Array position used as a line ID.
- A generated or padded identifier character.
- Free-text product description used as order identity.
- Phone/email match used to automatically select one account.

## 5. Real-world discovery scenarios

| # | Scenario | Expected discovery behavior | Current readiness |
|---|---|---|---|
| 1 | Authorized customer supplies exact `LOGON*ORDERNUMBER` | Exact `salesInv` lookup; retain raw order number and source document ID; show lines | Not ready: composite key absent |
| 2 | Customer supplies exact order number while authenticated to one account | Add authorized account/logon scope, resolve exact order, revalidate source | Partial; current candidate does not retain scope as canonical identity |
| 3 | Same numeric order exists under two logons | Return separate candidates keyed by logon; never merge | Not ready; graph uniqueness can collide |
| 4 | Customer supplies a `W...` web order number | Resolve web order to ERP/canonical order; retain both identifiers and mapping evidence | Partial; fields exist, mapping evidence absent |
| 5 | Customer supplies an offline/in-store order number | Resolve within accessible master/job accounts | Partial; generic `orderId` lookup lacks confirmed canonical scope |
| 6 | Customer supplies only a partial order number | Run explicit partial-search mode; require account and another anchor; never treat as exact | Unsafe/incomplete; exact identifiers can be widened by current lookup behavior |
| 7 | Order number contains leading zeroes | Preserve exact characters; no numeric conversion or trimming | Canonical string types help, but runtime canonical key is absent |
| 8 | Alphanumeric order such as `WS890466` | Exact string lookup; preserve case/characters according to source policy | Extractor patterns do not cover all confirmed formats reliably |
| 9 | Order is split by associate for fulfillment | Return one canonical order with multiple fulfillment/shipment contexts | Not ready for full shipment semantics |
| 10 | One line is split across several shipments | Aggregate shipment evidence; bind return to order line, not shipment | Not ready |
| 11 | Exact tracking number is supplied | Resolve shipment to order; display shipment context; require line correlation/confirmation | Partial |
| 12 | Tracking number belongs to a shipment with several products | Do not auto-select a line; correlate SKU/quantity and ask for confirmation | Not ready |
| 13 | Same SKU occurs on two lines of one order | Preserve both line identities; require line selection | Unsafe when source line ID is missing because a synthetic array-position ID is generated |
| 14 | Customer knows invoice number only | Resolve every invoice line; one invoice may lead to multiple orders | Not implemented |
| 15 | Customer knows customer PO only | Search within account scope; narrow by job/date/status; return multiple candidates when needed | Not implemented as a resolver |
| 16 | Customer knows job name and approximate date | Use as supporting filters, never identity | Questions exist; source-backed resolver incomplete |
| 17 | Customer knows product/SKU only | Use product purchase history/order intersection; account scope required | Partial; product source is declared but discovery coverage is incomplete |
| 18 | Purchase was made less than 24 hours ago | Prefer current Orders/source read; do not treat purchase-history absence as no order | Not explicitly handled |
| 19 | Purchase is older than public 12-month history | Use authoritative internal sources subject to retention/access; explain public history limit | Not explicitly handled |
| 20 | Phone/email is shared by multiple accounts | Return all authorized account candidates; request account/job selection | Not ready; hierarchy/contact paths are incomplete |
| 21 | User lacks permission for the relevant job account | Return no disclosed order details; provide authorization-safe guidance | Authorization boundary requires explicit scenario testing |
| 22 | Product has a prior return number or RMA | Resolve return/RMA to original order, then revalidate order and line | Not implemented |
| 23 | Order detail shows a return/refund already | Preserve return/status evidence for later analysis; do not exclude the order silently | Discovery can show candidates but lacks the required return-history resolver |
| 24 | Source record changed after candidate display | Reject stale confirmation and re-read authoritative order/line | Candidate-set version exists, but source revision binding is absent |
| 25 | Source line lacks an authoritative line number | Mark candidate line unconfirmable and route to review | Current code creates a confirmable synthetic line ID |

## 6. Anchor gaps in the current enum

Current `AnchorType` supports:

```text
ORDER_NUMBER
CUSTOMER_ID
PHONE
EMAIL
TRACKING_NUMBER
SKU
CUSTOMER_NAME
PRODUCT_DESCRIPTION
```

Missing first-class anchor concepts:

```text
CANONICAL_ORDER_KEY
WEB_ORDER_NUMBER
INVOICE_NUMBER
CREDIT_NUMBER
CUSTOMER_PO_NUMBER
FEI_PO_NUMBER
RETURN_NUMBER
RMA_NUMBER
RGA_NUMBER
JOB_ACCOUNT
JOB_NAME
ORDERED_BY
ORDER_DATE_RANGE
```

Some of these are currently mentioned as possible clarification questions, but a question is not an implemented anchor. Each usable anchor needs:

1. extractor/input contract;
2. non-destructive normalization policy;
3. typed resolver;
4. authority and cardinality rules;
5. candidate evidence model;
6. privacy/authorization policy;
7. ambiguity and failure behavior;
8. adversarial tests.

## 7. Minimum identity contract needed before implementation

This is a finding/proposed boundary, not a code change.

```text
OrderIdentityEvidence
    canonical_order_key          # LOGON*ORDERNUMBER
    logon
    raw_order_number
    source_order_document_id     # exact source _id
    source_writer_schema_version
    source_system
    source_revision
    web_order_number             # optional
    trilogie_order_number        # optional
    oracle_order_number          # optional
    source_transaction_id        # optional
    observed_at
    authorization_scope_reference
```

Rules:

- No field may silently substitute for another.
- Missing canonical identity makes the result non-confirmable.
- Preserve leading zeroes and alphanumeric characters.
- Never derive logon/customer identity from an order number alone.
- Web/ERP mappings must carry evidence and mapping version.
- Candidate and lock must retain the same canonical key and source revision.
- Confirmation must re-read the authoritative source.
- Line identity must be canonical order key plus immutable source line number; never array position.

## 8. Recommended next decision sequence

Before changing code:

1. Confirm the current production `salesInv` writer shapes and exact paths for `eventMeta.orderKey`, `eventMeta.orderNumber`, `_id`, source transaction ID, writer/schema version, web order, and ERP order.
2. Confirm whether the public/account-scoped order number maps to one or multiple Trilogie logons.
3. Confirm exact/partial behavior and authorization rules for the current Order Search service.
4. Approve the anchor tiers and resolver cardinality in this document.
5. Freeze `OrderIdentityEvidence`, `OrderCandidate`, and `DiscoveryLock` contracts.
6. Only then implement versioned adapters and migrate graph keys.

## 9. Sources

### Ferguson public sources

- [How to Use My Orders](https://www.ferguson.com/content/customer-support/website-tutorials/how-to-use-my-orders/)
- [How to Navigate the Dashboard](https://www.ferguson.com/content/customer-support/website-tutorials/how-to-navigate-the-dashboard/)
- [How to Use Order Tracking](https://www.ferguson.com/content/customer-support/website-tutorials/how-to-use-order-tracking/)
- [How to Find Product Purchase History](https://www.ferguson.com/content/customer-support/website-tutorials/how-to-find-product-purchase-history/)
- [Returns and Cancellations](https://www.ferguson.com/content/customer-support/returns-cancellations/)

### Repository evidence

- `backend/config/schema_registry.yaml`
- `backend/config/returns/production.yaml`
- `backend/src/return_platform/canonical/order.py`
- `backend/src/return_platform/operations/associate_flow.py`
- `backend/src/return_platform/operations/seed_manifest.py`
- `backend/src/return_platform/operations/order_discovery/source_operations.py`
- `backend/src/return_platform/operations/order_discovery/candidate_retriever.py`

### Discovery package evidence

- `return-discovery-spec-part2.md`
- `return-discovery-spec-part3.md`
- `return-discovery-spec-part4.md`
- `return-discovery-spec-part5.md`
- `return-discovery-spec-part9.md`
- `return_discovery_repo_alignment_deep_analysis_v2.md`
- `ORDER_ANALYSIS_CAPABILITY_COMPLETE_REQUIREMENTS.md`

## 10. Final conclusion

Order Discovery currently captures enough display data to build sandbox candidates, but it does **not** capture and preserve the complete authoritative order identity needed for production confirmation.

The first correction should not be another search field or UI change. It should be a frozen order-identity contract that separates:

```text
canonical order key
raw order number
source document ID
web order number
ERP order number
source revision/schema version
authorization scope
```

Strong anchors should then resolve into that contract. They should never replace it.
