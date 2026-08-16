-- Migration 0015: drop the pre-generation global uniqueness constraints.
--
-- Migration 0012 declared uniqueness on a *single* business-key property per
-- label. It predates the blue/green generation model, and under that model it
-- is not merely redundant, it is fatal: two generations legitimately share the
-- same logical key (see graph/constraints.py, "logical vs. physical keys"), so
-- a constraint that omits graph_generation_id unique-constrains ACROSS
-- generations and makes a second generation structurally impossible.
--
-- The failure it produced: the writer MERGEs on the generation-scoped physical
-- key, e.g. (graph_generation_id, account_id, sales_order_number). In a new
-- generation that MERGE matches nothing, so it CREATEs -- and the created node
-- sets sales_order_number, which the old generation's node already holds.
-- Neo4j reports it as "Merge did not find a matching node n and can not create
-- a new node due to conflicts with existing unique nodes", naming neither the
-- constraint nor the property.
--
-- The generation-scoped replacements are already provisioned and are derived
-- from the active schema rather than hand-written here:
--   uq_customer_graph_generation_id_account_id_customer_id
--   uq_salesorder_graph_generation_id_account_id_sales_order_number
--   uq_orderline_graph_generation_id_account_id_sales_order_number_line_number
--   uq_product_graph_generation_id_product_id
-- Dropping the 0012 constraints therefore removes no identity guarantee that
-- the schema-derived constraints do not already make, scoped correctly.
--
-- customer_key and order_line_key are additionally dead here: they belong to
-- the canonical projection (config/data_platform/graph_projection.yaml), and
-- no node this deployment writes carries either property.
--
-- The full-text indexes 0012 created were already replaced by 0013; only the
-- constraints were left behind.

DROP CONSTRAINT uq_customer_key IF EXISTS;

DROP CONSTRAINT uq_sales_order_number IF EXISTS;

DROP CONSTRAINT uq_order_line_key IF EXISTS;

DROP CONSTRAINT uq_product_id IF EXISTS;
