-- Migration 0013: replace broad fuzzy indexes with approved natural-language fields only.

DROP INDEX customer_name_search IF EXISTS;
DROP INDEX product_description_search IF EXISTS;

CREATE FULLTEXT INDEX customer_name_search_v2 IF NOT EXISTS
FOR (c:Customer) ON EACH [c.customer_name];

CREATE FULLTEXT INDEX product_description_search_v2 IF NOT EXISTS
FOR (p:Product) ON EACH [p.product_description];
