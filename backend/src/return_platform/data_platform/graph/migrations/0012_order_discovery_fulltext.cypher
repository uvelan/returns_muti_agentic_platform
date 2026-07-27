-- Migration 0012: deterministic constraints and bounded full-text indexes for production Copilot.

CREATE CONSTRAINT uq_customer_key IF NOT EXISTS
FOR (c:Customer) REQUIRE c.customer_key IS UNIQUE;

CREATE CONSTRAINT uq_sales_order_number IF NOT EXISTS
FOR (o:SalesOrder) REQUIRE o.sales_order_number IS UNIQUE;

CREATE CONSTRAINT uq_order_line_key IF NOT EXISTS
FOR (l:OrderLine) REQUIRE l.order_line_key IS UNIQUE;

CREATE CONSTRAINT uq_product_id IF NOT EXISTS
FOR (p:Product) REQUIRE p.product_id IS UNIQUE;

CREATE FULLTEXT INDEX customer_name_search IF NOT EXISTS
FOR (c:Customer) ON EACH [c.customer_name, c.billing_city, c.postal_code, c.account_type];

CREATE FULLTEXT INDEX product_description_search IF NOT EXISTS
FOR (p:Product) ON EACH [p.product_description, p.sku];
