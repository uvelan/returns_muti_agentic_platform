-- Migration 0016: a full-text index over the contact's own name.

-- The company already has one (0013); the person on the order did not, and the
-- consequence was not fuzziness but a flat miss. `contact_name` is searched with
-- CONTAINS on `contact_first_name` and `contact_last_name` separately, and
-- containment runs the wrong way for a full name: the stored value is `CAMERON`,
-- and asking whether `CAMERON` contains `CAMERON SOLBERG` is always false. An
-- associate who says "the order for Cameron Solberg" -- the most natural way to
-- say it -- matched nothing at all, while saying only "Cameron" matched eight.
--
-- Both properties on one index, not two indexes. `build_fulltext_query` AND-es
-- the tokens of one value, and Neo4j parses that query across every indexed
-- property, so `cameron* AND solberg*` matches the row holding the two halves in
-- two columns. Two single-property indexes could not: neither one holds both
-- tokens, so each clause would fail in its own index.
--
-- The same analyzer and the same query builder as the customer index, so the
-- misspelling recovery comes with it for free: `Camron Solbrg` reaches the same
-- row through the fuzzy terms that already carry `Jhon Smi` to `John Smith`.
CREATE FULLTEXT INDEX contact_name_search_v1 IF NOT EXISTS
FOR (c:ContactPoint) ON EACH [c.contact_first_name, c.contact_last_name];
