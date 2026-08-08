"""Canonical read-only source connector framework (Phase 8 / Wave C1).

Neutral, dynamic_knowledge-independent primitives for reading external
business data (MongoDB, SQL Server) -- consumed by `dynamic_knowledge`
(sync + on-demand reads), `data_platform` (graph sync, customer lookup),
`data_console` (admin browse/preview), and `v2` (order sync).

No mutation methods exist anywhere in this package. External sources are
read-only.
"""
