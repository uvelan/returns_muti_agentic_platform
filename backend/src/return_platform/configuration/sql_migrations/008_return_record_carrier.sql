/*
Forward-only platform migration: the carrier on the RMA (audit finding #9, T-14).

This migration extends the platform-owned SQL return store only. External
source databases remain read-only.

WHY THIS EXISTS
---------------
`005_case_return_records.sql` gave `dbo.return_record` the per-record fulfilment
identity -- label, tracking, return location, shipping instruction -- and
`007_return_record_method.sql` added the method. Neither added the carrier, and
until now nothing could have written one: a carrier reached the platform only
through `SupportActionRequest.carrier`, on the session path guarded by
`sessionId is not None`, and a Copilot case has no session. So
`ShipmentProjection.carrier` was `None` on every case, and the Copilot filled
its "Carrier & Service" tile from `session.orderSource` -- an order's source
system rendered to an operator as a carrier.

`ReturnOutcomeRecord.carrier` is the sender that closes it. The value travels
`ReturnOutcomeRecord -> SupportReturnRecord -> RETURN_RECORD_MERGED_FIELDS ->
ReturnRecordView -> project_shipments`, and this column is the authoritative
half of that chain: without it a Mongo case would report a carrier the return
store never received, which is exactly the divergence `007` refused to leave in
place for the method.

WHY IT IS ON THE RECORD AND NOT THE CASE
----------------------------------------
The rule `005` states for label, tracking and return location. A split return
goes back on two carriers under two RMAs, and a case-level column would be read
as the carrier of every package on the case -- attributing one package's carrier
to another's, which is the same class of error as the `orderSource` substitution
this closes.

WHY IT IS NOT `dbo.return_tracking.carrier_code`
------------------------------------------------
That column already exists and is a different statement. `return_tracking` is
keyed on a tracking reference and holds one *observation*: the carrier that
filed a scan, with the `tracking_type` and `event_at` that make it an event.
`record_shipment_update` writes it, from a carrier feed. This column holds what
Support said when the RMA was issued, before any carrier has filed anything --
and `persist_case_return_records` could not write a tracking row from a support
outcome anyway without inventing the `tracking_type` and `event_at` that row
requires, which is the CFG-03 defect in reverse.

WHY THERE IS NO CHECK CONSTRAINT
--------------------------------
There is no carrier vocabulary to check against. `return_policy` declares none,
`return_tracking.carrier_code` has none, and unlike a return method a carrier
name resolves to no requirement row -- an unrecognised one is a carrier nobody
has heard of yet, not a value that makes a case unresolvable. A CHECK, or a
pydantic `pattern=`, would refuse a carrier a deployment starts using tomorrow
and would advertise the stale set through OpenAPI as authoritative: the defect
`return_creation_policy.py` removed from `shippingPathExpectation` (CFG-03).
`ReturnOutcomeRecord.carrier` bounds it to a non-blank string of at most 64
characters and nothing further.

NULL is legitimate and stays legitimate: it means Support has not said which
carrier, and the merge in `record_support_outcome` sends merged values rather
than the notice's raw nulls, so a later reply carrying only a tracking number
cannot blank a carrier already recorded.

VARCHAR(64) matches `ReturnOutcomeRecord.carrier`'s and
`SupportActionRequest.carrier`'s `max_length`, so a value the API accepts is a
value this column can hold.
*/

USE [return_platform];
GO

IF COL_LENGTH(N'dbo.return_record', N'carrier') IS NULL
    ALTER TABLE dbo.return_record ADD carrier VARCHAR(64) NULL;
GO
