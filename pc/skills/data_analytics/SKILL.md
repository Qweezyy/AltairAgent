---
name: data_analytics
description: Data analysis and clear charts — bank statements and tables, interactive charts, presenting the result. For finances, reports and any "count it and show it" task.
---

# Skill: analytics and charts

The rule: **count with tools, show with a chart.** A wrong sum while analysing data is not
acceptable, and one chart is clearer than three paragraphs.

## Bank statements

* `analyze_statement` reads a CSV/XLSX itself: sorts it into categories, counts income, spending
  and the total, finds the largest expenses, draws a chart. Do not add up sums by hand.
* From the analysis, suggest concrete things: where spending stands out, what to cut without
  losing quality of life. No moralising.
* Categories come from keywords; when a bank names its operations oddly, part goes to "Other" —
  say so, do not present it as an exact picture.

## Charts (`create_chart`)

* The type follows the data: `line` — change over time, `bar` — comparing categories, `pie` —
  shares of a whole, `scatter` — a relation between values, `area` — accumulation.
* For lines and bars — `series` (the Y series) and a shared `x`; for a pie — `labels` and
  `values`.
* The chart is saved as self-contained HTML: it opens in the preview and in any browser, offline.
  Refer to it in the answer.
* Draw the chart AFTER the calculation, to show the result, not instead of it.

## Large tables (`profile_data`, `query_data`)

For CSV/Parquet/JSON — especially large ones (hundreds of MB, gigabytes) — use DuckDB, not
reading by hand:

* `profile_data path=...` — an automatic first look (EDA) at an unfamiliar dataset: rows, columns
  with types, the share of missing values, uniqueness, statistics (min/max/mean/quartiles) and the
  problems found (many gaps, constant columns). ALWAYS start with it, to understand the data
  before querying.
* `query_data path=... sql=...` — SQL right over the file (it is the table `data`), without loading
  it into memory: aggregations, grouping, filters, JOINs. Example:
  `SELECT category, SUM(amount) FROM data GROUP BY category`. Read-only — the file is not changed.
* Found the slice — show it with `create_chart`.

## Other data and forecasts

* Small calculations — explicitly with `run_python`, not "by eye".
* "What if" and forecasts — calculated too, and shown as a chart.

## Honesty

Do not make up numbers. Not enough data, or a format that did not parse — say plainly what is
unknown instead of putting in something plausible.
