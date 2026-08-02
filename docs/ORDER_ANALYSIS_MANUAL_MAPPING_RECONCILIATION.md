# Order Analysis: Manual Mapping Reconciliation and Clean Field Contract

**Report date:** 2026-08-02  
**Scope:** Order Analysis, including its dependency on sealed Order Discovery identity  
**Evidence reviewed:** Three manually mapped Word documents, one manually mapped Excel workbook, the Return Discovery/Order Analysis package, the existing Order Discovery findings, and the current repository implementation  
**Change status:** Findings only; no runtime code or configuration changes

## 1. Executive decision

The manual mappings are useful as a field-discovery aid, but **they are not safe to implement directly**.

They confirm several important `salesInv` and product fields, but also:

- conflate display order number, source order ID, and canonical order identity;
- assign shipment/tracking responsibility to `orderOutbnd` instead of `shipmentInfo`;
- propose payment authorization and card-related fields for AI processing;
- mark `eventMeta.*` as ignorable even though canonical identity, source version, and audit evidence depend on it;
- assume product joins that the source package explicitly says require validation;
- omit `invoiceMemosCDM`, `shipmentInfo`, `locationsCDM`, and OMC V1/V2 return-history evidence from the claimed “sufficient” POC field set;
- do not define remaining-returnable quantity, return-status consumption, conflict handling, or immutable analysis output.

The clean conclusion is:

> The current repository and manual mappings provide a strong canonical-data foundation, but the production Order Analysis contract is not implemented. The manual files should be converted into versioned source-adapter evidence, not treated as a canonical schema.

## 2. Evidence hierarchy used in this report

When sources disagree, this report uses the following precedence:

1. Confirmed identity, authority, and safety rules in `ORDER_ANALYSIS_CAPABILITY_COMPLETE_REQUIREMENTS.md`.
2. Confirmed mappings and conflicts in Return Discovery specification Parts 1-12.
3. Repository behavior verified directly in code and configuration.
4. Manually mapped DOCX/XLSX fields, treated as candidate evidence requiring source validation.
5. Part 13 and unverified examples, treated as illustrative only where the package identifies defects or uncertainty.

This prevents a manually entered field name or example from overriding a confirmed authority or security boundary.

## 3. Manual files reviewed

### 3.1 `mongo db collection.docx`

Primary subject: product and warehouse-product fields, apparently from `lkpSearchProduct`.

Useful candidate fields:

- product description and long description;
- web display name;
- vendor name and vendor product code;
- brand type;
- UOM and UOM description;
- UPC;
- serial-number-required flag;
- obsolete flag/date;
- substitute product ID and notes;
- base model number;
- warehouse ID, bin location, rank code, and product status.

Unsafe or incomplete assertions:

- `_id` is called the “Primary Product ID” and directly joined to `salesInv.productId`; this must be verified by writer/schema version.
- `salesInv.masterProductId` is listed as only a “possible join”; the production adapter must preserve both source product ID and master product ID rather than choose one globally.
- `eventMeta.*` is marked ignorable. This is rejected because source version, update time, event identity, and mapping evidence are required.
- alternate codes are marked unnecessary, but the Order Analysis contract explicitly requires source product ID, MPID, alternate code, SKU/model/manufacturer part number, and historical description to remain distinguishable.
- freight code is marked unnecessary, but product freight/LTL facts are required for logistics analysis.
- product family/category and primary vendor identifier are marked unnecessary even though product/logistics and vendor-return decisions may require them.
- `perQty` is marked irrelevant, but package/UOM conversion can affect quantity interpretation and must be retained as conditional evidence until its semantics are confirmed.

### 3.2 `MongoDB_Fields_Required_For_Ferguson_Returns_AI_POC.docx`

Primary subject: a proposed minimal field set from `salesInv` and `orderOutbnd`.

Useful candidate fields:

- selling and ship-from warehouse;
- sales type and order status;
- customer ID/name, PO, job name;
- order/invoice dates;
- order and line amounts;
- address and ship-to details;
- product ID, master product ID, description;
- ordered and shipped quantity;
- inventory warehouse;
- immutable line-number candidate at `salesLines.salesLnsEventData.lineNumber`;
- web order number.

Rejected or corrected assertions:

- `salesHdrEventData.orderId` is labeled simply “Order ID.” It must be separated into raw order number, canonical `orderKey`, and source document ID.
- `authCode` is proposed for payment verification. General Order Analysis must not ingest payment authorization codes.
- refund basis cannot be calculated safely from `salesInv.orderTotalAmt` alone; line-level invoice evidence, currency, and valuation rules are required.
- shipment, tracking, delivery, carrier, and package facts are placed under `orderOutbnd`. Confirmed authority is `shipmentInfo`; `orderOutbnd` is a migration fallback for order data only.
- `shippingLabel` for the return shipment is not an original-order discovery fact and requires a separate return-fulfillment source/lifecycle.
- the document claims the selected fields can validate eligibility and calculate refunds, but it omits prior V1/V2 returns, return statuses, invoice-line correlation, policy version, and remaining-returnable calculation.

### 3.3 `Product and Order Data Analysis.docx`

Primary subject: a manually drawn product-to-order relationship.

Useful candidate relationships:

```text
product master
    -> salesInv line masterProductId/productId/altCode
    -> salesInv order header
    -> customer/order dates
    -> fulfillment status
```

Issues requiring correction:

- it refers to `product_v2`, while the confirmed Order Analysis product source is `lkpSearchProduct` plus historical order-line facts;
- example master-product identifiers do not demonstrate an actual matching join and may come from different records;
- `salesHdr.salesHdrData.orderId` conflicts with other mappings that use `salesHdrEventData.orderId` and with the confirmed `eventMeta.orderKey`/`eventMeta.orderNumber` model;
- a separate `orderStatus` collection is presented without being part of the confirmed source inventory;
- tracking is attached to that unconfirmed status object, while confirmed tracking authority is `shipmentInfo`;
- color/finish is useful for product disambiguation, but it cannot establish order-line identity without authoritative line evidence.

### 3.4 `SalesInv.xlsx`

Primary subject: selected sales header, shipping, product, associate, customer, and payment fields.

Useful candidate fields:

- selling/ship-from warehouse;
- shipping method and requested ship date;
- ship-to address components;
- order status and line count;
- product/master-product/alternate-code/description fields;
- per-package quantity, ordered quantity, and shipped quantity;
- customer ID/name;
- sales associate/salesperson references, when authorized and operationally required.

Critical problems:

- the workbook does not include the canonical `eventMeta.orderKey`, raw order number, or source document/version identity needed to bind analysis safely;
- it includes payment method, payment token key, cardholder name, expiration, masked account number, and billing-address fields;
- those payment and card-related fields are prohibited from Order Analysis contexts, prompts, graph projections, logs, frontend projections, and general evidence stores;
- the workbook contains PII and payment-shaped example data and therefore should be handled as restricted source material even if examples are synthetic or masked;
- it omits invoice, shipment-item, prior-return, policy, conflict, and analysis-output fields.

## 4. Reconciliation with the existing Order Discovery findings

The manual mappings **reinforce**, rather than overturn, the existing discovery report.

### Confirmed findings

1. Order number and source order identity are not safely separated in the current discovery runtime.
2. The canonical `LOGON*ORDERNUMBER` key is still missing from candidates and locks.
3. The source Mongo `_id` is still dropped before discovery confirmation.
4. The required immutable line number is not configured; the current runtime looks for generic IDs inside `lineData`.
5. Manual evidence points to `salesLines.salesLnsEventData.lineNumber`, which should be validated in real writer versions.
6. Web order number is useful, but its mapping to the authoritative ERP/canonical order needs evidence.
7. Product/SKU is a narrowing anchor, not line identity.
8. Shipment/tracking cannot identify a return line without order-scoped item correlation.
9. Payment fields must be excluded from all general discovery and analysis models.

### New refinements from the manual mappings

- requested ship date should be retained as order/shipment scheduling evidence;
- UOM, UOM description, and conditional package quantity should be considered together;
- serial-number requirement, obsolescence, substitutions, base model, vendor code, and warehouse-product status are useful product/logistics facts;
- authorized associate/salesperson references may help operational routing and audit but are not order identity;
- color/finish and alternate code can help disambiguate duplicate products but must not replace line number.

## 5. Clean authority matrix

| Fact | Primary authority | Allowed fallback | Manual-file correction |
|---|---|---|---|
| Customer/account identity | `customerOutboundCDM` or approved Customer Search API | Sealed discovery evidence followed by revalidation | Do not use flat customer/address data in `salesInv` as the canonical customer master |
| Canonical order identity | `salesInv.eventMeta.orderKey` or writer-specific equivalent | `orderOutbnd` only when migration capability explicitly permits | Do not treat `salesHdrEventData.orderId` or Mongo `_id` as interchangeable |
| Raw order number | `salesInv.eventMeta.orderNumber` or validated writer-specific field | Approved adapter alias | Preserve exactly, including leading zeroes and letters |
| Source order document ID | Exact `salesInv._id` | None | Retain separately from canonical order key |
| Order line identity | `salesInv` immutable line number plus canonical order key | None | Do not use array position or generated IDs |
| Historical ordered/shipped/open quantities | `salesInv` | Shipped quantity may degrade to approved source evidence | Keep quantity semantics source-attributed |
| Shipment/tracking/delivery | `shipmentInfo` | `salesInv.shippedQuantity` as degraded quantity evidence | Do not source authoritative shipment facts from `orderOutbnd` |
| Invoice and invoiced quantity | `invoiceMemosCDM` | Approved embedded invoice fact in `salesInv` | Invoice can span multiple orders; inspect invoice lines |
| Product/logistics | `lkpSearchProduct` plus historical order line | Historical line snapshot | Product join must be validated by source version |
| Location | `locationsCDM` | Approved session/branch context | Warehouse IDs in order/product data are references, not the complete location entity |
| V1 prior return | `omc.dbo.returns` plus `omc.dbo.returnCart` | None | Missing from manual mappings |
| V2/LSI prior return | `returnMerchandiseAuthorization` chain | None | Missing from manual mappings |
| Return status consumption | Versioned platform policy over source statuses | None | Never infer all pending/cancelled statuses consume zero or full quantity |
| Relationship/risk | Neo4j | Source reads | Derived evidence only; never quantity or identity authority |
| Runtime policy | Active platform configuration release | Last valid active release | Manual documents are not runtime policy |

## 6. Clean field disposition

### 6.1 Order header

| Clean field | Candidate manual path | Disposition | Notes |
|---|---|---|---|
| `canonical_order_key` | Not present | **Required / missing** | Must be `LOGON*ORDERNUMBER` or validated equivalent from a versioned adapter |
| `raw_order_number` | `salesHdrEventData.orderId`; `salesHdr.salesHdrData.orderId` | **Conditional** | Conflicting paths and semantics; validate per writer version |
| `source_order_document_id` | Mongo `_id` | **Required** | Never substitute for display order number |
| `source_writer_schema_version` | `eventMeta.*` | **Required / manually excluded** | Needed to select physical-shape adapter |
| `source_transaction_id` | Not present | **Required when available** | Keep separate from order identity |
| `account_or_logon` | `salesHdrEventData.accountId` | **Conditional** | Must be mapped to canonical account/logon semantics |
| `customer_key` | `salesHdr.salesHdrData.custId` | **Conditional reference** | Revalidate through customer authority |
| `customer_name_snapshot` | `salesHdr.salesHdrData.custName` | **Accept as snapshot** | Display evidence, not identity |
| `customer_po_number` | `salesHdr.salesHdrData.custPONumber` | **Accept** | Supporting discovery anchor; may repeat |
| `job_name` | `salesHdr.salesHdrData.jobName` | **Accept** | Supporting evidence only |
| `sales_type` | `salesHdrEventData.salesType` | **Accept after enum mapping** | Cash/credit semantics need source contract |
| `order_status` | `salesHdrEventData.orderStatus` | **Accept after versioned mapping** | Preserve raw and normalized values |
| `order_date` | `salesHdr.salesHdrData.orderDate` | **Accept** | Normalize timezone/date semantics |
| `invoice_date_snapshot` | `salesHdr.salesHdrData.invoiceDate` | **Accept as snapshot** | Does not replace invoice evidence |
| `requested_ship_date` | `salesHdr.shipping.reqrdShipDate` | **Accept** | Scheduling evidence |
| `web_order_number` | `sourceWebOrderNumber` variants | **Conditional** | Requires evidence-bearing mapping to canonical order |
| `selling_warehouse_reference` | `salesHdrEventData.sellWhseId` | **Accept reference** | Resolve location separately |
| `ship_from_warehouse_reference` | `salesHdrEventData.shipFromWhseId` | **Accept reference** | Resolve location separately |
| `ship_via_code/description` | header/shipping variants | **Accept snapshot** | Not shipment authority |
| `order_total_amount` | `salesHdr.salesHdrData.orderTotalAmt` | **Accept source fact** | Decimal plus currency required; not refund authorization |
| `recorded_refund_amount` | `salesHdr.salesHdrData.refundAmt` | **Restricted/conditional** | Source fact only; prior return/refund authority must be explicit |
| `payment_authorization_code` | `salesHdr.salesHdrData.authCode` | **Reject** | Prohibited from Order Analysis |

### 6.2 Order line

| Clean field | Candidate manual path | Disposition | Notes |
|---|---|---|---|
| `canonical_order_line_key` | Not present | **Required / missing** | Canonical order key plus immutable line number |
| `source_line_document_id` | Not present | **Required for line-document writers** | Exact raw source identity |
| `immutable_line_number` | `salesLines.salesLnsEventData.lineNumber` | **High-value candidate** | Must be verified across active writer shapes |
| `source_product_id` | `salesLines[].lineData.productId` | **Accept** | Preserve exact historical value |
| `master_product_id` | `salesLines[].lineData.masterProductId` | **Accept** | Do not assume equality with product `_id` without proof |
| `alternate_code` | `salesLines[].lineData.altCode1` | **Accept** | Supporting product evidence |
| `description_at_order` | `salesLines[].lineData.productDesc` | **Accept** | Never overwrite with current product description |
| `ordered_quantity` | `salesLines[].lineData.orderQty` | **Accept** | Strict business quantity; UOM required |
| `shipped_quantity` | `salesLines[].lineData.shipQty` | **Accept as sales-order evidence** | Compare with shipment evidence |
| `open_quantity` | Not consistently present | **Required when source supports it** | Status semantics must be documented |
| `backordered_quantity` | `salesLines[].lineData.boQty` | **Conditional** | Retain when writer semantics are confirmed |
| `invoiced_quantity` | Not present in manual mapping | **Required / missing** | Prefer invoice evidence; approved embedded fact allowed |
| `per_quantity` | `salesLines[].lineData.perQty` | **Conditional** | Needed only with approved package/UOM semantics |
| `unit_of_measure` | Product/header candidates | **Required** | Historical line UOM preferred for quantity math |
| `unit_price` | `salesLines[].lineData.netPrice` | **Accept source fact** | Decimal plus currency; may not equal refund value |
| `line_net_amount` | `salesLines[].lineData.lineNetAmt` | **Accept source fact** | Decimal plus currency |
| `list_price` | `salesLines[].lineData.listPrice` | **Analysis-optional** | Not refund authority |
| `unit_cost` | `salesLines[].lineData.unitCostAmt` | **Restricted/usually unnecessary** | Do not expose unless a governed business need exists |
| `inventory_warehouse_reference` | `salesLines[].lineData.invenWhse` | **Accept reference** | Resolve canonical location separately |

### 6.3 Product and warehouse-product evidence

| Clean field | Candidate manual path | Disposition |
|---|---|---|
| Current product description | `masterProduct.productDesc` / current product source | Accept |
| Long/current display description | `prodLongDesc`, `webDisplayName` | Accept as display evidence |
| Vendor name/code | `vendorName`, `vendorProdCode` | Accept after product-source validation |
| Brand/manufacturer/model | `brandType`, `baseModelNumber` | Accept |
| UPC | `upcCode` | Accept as supporting lookup evidence |
| UOM/UOM description | `uom`, `uomDesc` | Accept; reconcile with historical line UOM |
| Serial required | `serialReqrdFlag` | Accept; may create missing-information requirement |
| Obsolete/date | `obsFlag`, `obsDate` | Accept |
| Substitute ID/notes | `subsId`, `subsNotes` | Accept as current product evidence |
| Alternate codes | `altCodes` | Retain; manual “ignore” decision rejected |
| Freight/LTL fact | `freightCode` or proven LTL field | Required when freight logic applies |
| Category/product family | product hierarchy/family fields | Conditional but useful for policy/logistics |
| Warehouse ID/bin/rank/status | `whseProducts.*` | Accept as warehouse-product evidence, not location master |

### 6.4 Shipment evidence

The manual files do not provide a safe shipment model. The clean model must come from `shipmentInfo` and include:

- shipment identity distinct from tracking number;
- canonical order key;
- all tracking references;
- carrier/source and shipment status;
- shipped/delivered quantities;
- shipment timestamp and split sequence;
- item SKU/product references;
- item quantity and delivered quantity;
- item status;
- optional governed POD/signature/GPS reference;
- source update/version;
- line-correlation quality and ambiguity reason.

Tracking identifies shipment context. It does not automatically identify the return line.

### 6.5 Invoice evidence

Invoice fields are effectively absent from the manual package except for header snapshots. The clean model must read `invoiceMemosCDM` and include:

- invoice ID and invoice number;
- account/customer reference;
- status and invoice date;
- every invoice line;
- line-level sales-order reference;
- Ferguson line number when available;
- product reference, quantity, UOM, unit amount, line amount, and currency;
- source update/version;
- correlation quality when line number is unavailable.

One invoice may reference multiple orders. Header invoice date or order total in `salesInv` is not sufficient.

### 6.6 Prior-return evidence

The manual package contains no usable model for prior returns. Order Analysis requires separate V1 and V2 evidence:

```text
V1: omc.dbo.returns + omc.dbo.returnCart
V2: returnMerchandiseAuthorization -> cart -> cartItem -> related entities
```

Each returned quantity must retain source version, source status, normalized consumption class, and policy version.

### 6.7 Prohibited payment and sensitive fields

The following manual fields must not enter Order Analysis contexts, prompts, graph projections, logs, frontend projections, or general evidence storage:

- payment token key;
- authorization code;
- payment account/card number, even when masked;
- cardholder name;
- card expiration;
- card billing address;
- raw payment method details beyond a separately governed, minimal reference when a downstream payment service requires it.

Order Analysis may output a valuation reference. It must not choose or expose a payment/refund credential.

## 7. Clean Order Analysis model

### 7.1 Request

```text
OrderAnalysisRequest
    analysis_request_id
    idempotency_key
    session_id
    discovery_lock_id
    discovery_context_version
    discovery_context_digest
    requested_lines[]
        canonical_order_key
        immutable_line_number
        requested_return_quantity
        reason_code
        condition
    intake_channel
    actor
```

Identity values must match the sealed discovery context; the caller cannot override them.

### 7.2 Canonical evidence snapshot

```text
CanonicalOrderEvidence
    customer_identity
    customer_account_identity
    sales_order_identity
    source_order_documents[]
    selected_order_lines[]
    product_evidence[]
    shipment_evidence[]
    invoice_evidence[]
    location_evidence[]
    prior_returns_v1[]
    prior_returns_v2[]
    source_health[]
    source_versions[]
```

### 7.3 Quantity analysis per line

```text
ordered_quantity
sales_order_shipped_quantity
shipment_reported_quantity
delivered_quantity
invoiced_quantity
completed_returned_v1
completed_returned_v2
active_reserved_v1
active_reserved_v2
released_return_quantity
unknown_or_disputed_return_quantity
safe_shipped_basis
remaining_returnable_quantity
requested_return_quantity
```

Required calculation boundary:

```text
remaining_returnable = max(
    0,
    safe_shipped_basis
      - consumed_return_quantity
      - reserved_active_return_quantity
)
```

Unknown status or unavailable return-history sources must produce review/degraded evidence, never zero.

### 7.4 Immutable output

```text
OrderAnalysisContext
    analysis identity/version/status
    session and discovery binding
    request/configuration/policy digests
    canonical identities
    per-line quantity analysis
    shipment evidence
    invoice evidence
    prior-return evidence
    product/location facts
    conflicts
    missing requirements
    policy result
    risk reference
    valuation reference
    next allowed transition
    human-review requirement
    audit/evidence references
    payload digest
```

## 8. Current repository alignment

### Implemented foundation

- strict immutable canonical base and provenance;
- `Customer` and `CustomerAccount`;
- `SalesOrder` and `OrderLine`;
- `Product`, `Warehouse`, and `WarehouseProduct`;
- `Shipment`, `ShipmentItem`, and `TrackingEvent`;
- generic V1/V2 `Return` and `ReturnItem`;
- workflow context snapshots, audit events, and graph evidence;
- model/mapping tests that validate the current internal contracts.

### Partial or mismatched

- canonical sales-order key uses `TDS:accountId:orderId:orderInstanceKey`, not the required discovery `LOGON*ORDERNUMBER` identity;
- order line uses `source_line_number`, but runtime discovery does not populate the canonical model;
- product model covers many manual product fields but lacks a complete LTL/non-stock/kit evidence contract;
- shipment model lacks delivered quantity, split sequence, complete item-correlation quality, and optional missing line number;
- generic return models lack the full V1 and V2 source relationships and status-consumption classifications;
- graph registry and synchronization still use legacy display-oriented keys.

### Missing

- versioned `salesInv` adapters for line-document and embedded-line shapes;
- `orderOutbnd` migration fallback adapter with explicit capability and discrepancy evidence;
- `shipmentInfo` evidence adapter;
- invoice and invoice-line canonical models/adapters;
- `locationsCDM` adapter;
- complete OMC V1 and V2 return-history readers;
- source revalidation bound to sealed discovery;
- deterministic quantity engine;
- conflict record and evidence-sufficiency models;
- `OrderAnalysisRequest` and `OrderAnalysisContext`;
- Order Analysis persistence, events, API, and Temporal workflow;
- adversarial end-to-end validation.

## 9. Blocking contradictions to resolve with source owners

1. Exact active `salesInv` paths for canonical order key, raw order number, source `_id`, source transaction ID, and writer/schema version.
2. Whether `salesHdrEventData.orderId` is raw order number, composite order ID, or a writer-specific alias.
3. Exact location of immutable line number across active writers, including `salesLnsEventData.lineNumber`.
4. Whether `lkpSearchProduct._id`, product ID, and MPID are equal for every supported source/writer.
5. Approved product fields for LTL/freight, non-stock, kit/component, category, and disposition.
6. Whether `perQty` changes return quantity interpretation and how it relates to UOM.
7. Current governed invoice reader required when `fergusonLineNumber` is omitted by a public API.
8. Exact V1/V2 return statuses and the approved consumption policy.
9. Current location source and padded/unpadded warehouse lookup rules.
10. Explicit confirmation that no payment fields may cross the Order Analysis serialization boundary.

## 10. Recommended implementation sequence

### OA0 - Freeze contracts

- canonical order and line identities;
- source document identities and schema versions;
- request/output contracts;
- source authority matrix;
- payment/PII exclusion contract;
- failure codes and state transitions.

### OA1 - Versioned order adapters

- embedded and line-document `salesInv` shapes;
- exact `orderKey`, raw number, `_id`, and immutable line number;
- web/ERP reference mapping evidence;
- `orderOutbnd` fallback capability.

### OA2 - Source revalidation

- discovery lock/version/digest validation;
- authoritative source revision comparison;
- stale order/line behavior;
- unconfirmable line handling.

### OA3 - Shipment and invoice evidence

- `shipmentInfo` split-shipment and item correlation;
- `invoiceMemosCDM` multi-order invoice handling;
- explicit ambiguity and degraded evidence.

### OA4 - Unified return history

- V1 reader;
- V2 reader;
- versioned status-consumption policy;
- source-separated quantity totals.

### OA5 - Quantity engine

- safe shipped basis;
- source conflicts;
- aggregate requested quantity;
- remaining-returnable result;
- deterministic rejection/review rules.

### OA6 - Product/location/policy evidence

- historical vs current product facts;
- UOM/package semantics;
- LTL/non-stock/kit/obsolete/substitute evidence;
- selling vs fulfillment location;
- missing policy routed to review.

### OA7 - Persistence and workflow

- immutable analysis snapshots;
- idempotency and digests;
- atomic events/outbox;
- API and review endpoint;
- Temporal workflow and activities.

## 11. Acceptance criteria

Order Analysis is not complete until all of the following are demonstrated:

- canonical `LOGON*ORDERNUMBER` identity is preserved and revalidated;
- source `_id` and canonical identity remain separate;
- immutable line number is source-derived, never generated from array position;
- both supported `salesInv` shapes normalize to the same canonical model;
- web/ERP mappings are evidence-bearing;
- shipment and invoice facts are read from their authoritative sources;
- split shipments and multi-order invoices are supported;
- V1 and V2 return histories are both queried;
- unknown return statuses fail to review rather than consume zero;
- remaining-returnable quantity is deterministic and auditable;
- duplicate/aggregate over-return is blocked;
- product/UOM/logistics facts are source-attributed;
- payment fields are absent from JSON, graph, logs, prompts, traces, and frontend;
- analysis output is immutable and digest-bound;
- exact retry is idempotent and changed-payload retry conflicts;
- source outage is not converted to an empty or zero fact;
- adversarial and end-to-end tests pass against real supported source shapes.

## 12. Final conclusion

The manual mappings should be retained as **candidate source-field evidence**, with each field assigned to a versioned adapter and an explicit authority, security, and cardinality rule.

They should not become a shared dictionary scanner or a single flattened “AI context.” The clean production boundary is:

```text
sealed discovery identity
    -> versioned authoritative readers
    -> canonical evidence with provenance
    -> deterministic quantity/conflict analysis
    -> immutable OrderAnalysisContext
    -> human review or Return Workflow transition
```

The most urgent corrections are identity separation, payment-field exclusion, authoritative shipment/invoice sources, immutable line number, dual return history, and the remaining-returnable calculation.

## 13. Source files

### Manual mappings

- `K:\Projects\FEG\Ret\full\return_discovery_order_analysis_package\files\mongo db collection.docx`
- `K:\Projects\FEG\Ret\full\return_discovery_order_analysis_package\files\MongoDB_Fields_Required_For_Ferguson_Returns_AI_POC.docx`
- `K:\Projects\FEG\Ret\full\return_discovery_order_analysis_package\files\Product and Order Data Analysis.docx`
- `K:\Projects\FEG\Ret\full\return_discovery_order_analysis_package\files\SalesInv.xlsx`

### Existing findings and requirements

- `docs/ORDER_DISCOVERY_ORDER_ID_AND_ANCHOR_FINDINGS.md`
- `K:\Projects\FEG\Ret\full\return_discovery_order_analysis_package\outputs\ORDER_ANALYSIS_CAPABILITY_COMPLETE_REQUIREMENTS.md`
- `K:\Projects\FEG\Ret\full\return_discovery_order_analysis_package\outputs\return_discovery_repo_alignment_deep_analysis_v2.md`

### Current implementation

- `backend/src/return_platform/canonical/order.py`
- `backend/src/return_platform/canonical/product.py`
- `backend/src/return_platform/canonical/shipment.py`
- `backend/src/return_platform/canonical/return_models.py`
- `backend/src/return_platform/operations/associate_flow.py`
- `backend/config/returns/production.yaml`
- `backend/config/schema_registry.yaml`
- `backend/config/data_platform/canonical_mappings.yaml`
