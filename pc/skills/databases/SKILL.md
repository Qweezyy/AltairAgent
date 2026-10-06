---
name: databases
description: Working with SQLite databases — understanding one through its schema and an ER diagram, querying safely (SELECT over a read-only connection), optimising queries with EXPLAIN, changing data carefully and with confirmation. For analysing and editing local databases (.db/.sqlite).
---

# Skill: databases (SQLite)

Tools: `db_schema` (the structure), `db_query` (SQL), `db_diagram` (an ER diagram in Mermaid).
SQLite is supported for now — it sits under many applications (browsers, messengers, mobile and
desktop programs, `*.db`/`*.sqlite`).

## Understand the database first, then touch it

1. `db_schema path=...` — tables, columns, types, keys, row counts. The first thing in an
   unfamiliar database.
2. `db_diagram path=...` — an ER diagram of the relations (Mermaid). Show it to the user in a
   ` ```mermaid ` block — the relations are clearer that way.
3. Only once the structure is clear, write queries.

## Queries — safe by default

* `SELECT`/`PRAGMA`/`EXPLAIN` go over a **read-only connection** and need no confirmation: the data
  cannot be damaged even by a wrong query.
* Changing queries (`INSERT`/`UPDATE`/`DELETE`/`CREATE`/`DROP`/…) need the user's confirmation and
  open the database for writing. Before changing data, show what exactly and how many rows will be
  touched (a `SELECT` first).
* The result comes as a table; with many rows, limit them with `LIMIT` in the query itself — do
  not pull a whole table into the context.

## Optimisation

`EXPLAIN QUERY PLAN SELECT ...` shows how SQLite will run a query: a `SCAN` over a large table
means an index is missing; `SEARCH ... USING INDEX` is good. A slow query on a foreign key
without an index is a typical cause of slowness.

## Careful with the user's data

* Do not run `DROP` or a mass `DELETE`/`UPDATE` without an explicit request and an understanding
  of the consequences. Data in a database often cannot be recovered.
* Before a destructive operation, offer to copy the database file.
* Do not expose sensitive contents (passwords, tokens, personal data) unless the user asked.
