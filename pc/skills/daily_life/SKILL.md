---
name: daily_life
description: Everyday tasks — travel routes, menus with calories and macros, plans and budgets. Helps to plan trips, meals and personal finances from current data, with clear charts.
---

# Skill: everyday life

An everyday task rarely has one right answer: the person has a budget, tastes and limits.
Understand them first, then plan. Do not dump a ready plan out of nowhere: two clarifying
questions save ten corrections later.

## Travel routes

1. First ask what the plan makes no sense without: the dates (or the length), the budget, who is
   going (alone / two / with children), the interests (museums, nature, food). Ask with `ask` —
   options picked in one click.
2. Check what changes on the web, not from memory: prices, opening hours, seasons and visa rules
   change. Use `web_search` and `deep_research`.
3. Give the plan by day, with the time and money each item takes. Show the total budget by
   category (stay, food, transport, activities) as a chart with `create_chart`.
4. Say honestly what should be booked in advance and where the price "floats".

## Menus, calories and macros

1. Ask the goal (lose / gain / keep weight), the limits (allergies, vegetarian) and how many meals
   a day.
2. Count calories and macros with `solve_math` or `run_python` — it is easy to slip in your head,
   and in nutrition a slip adds up over a week.
3. Give the menu for a day or a week as a table: dish, portion, kcal, protein/fat/carbs. Show the
   daily totals as a bar chart.
4. Do not give medical prescriptions. A diet for an illness is a doctor's job; you help with an
   ordinary diet.

## Finances

1. Analyse a bank statement with `analyze_statement` — it sorts it into categories and draws the
   chart itself. Do not add up sums by hand.
2. From the analysis, suggest concrete things: where spending stands out, what can be cut without
   losing quality of life. No moralising.
3. Compute forecasts and "what if" explicitly (`run_python`) and show them as a chart.

## General rules

* Numbers come from tools, not by eye. A mistake in a budget or in calories is not abstract — it
  is the person's real money and health.
* Clarity beats volume: one chart is clearer than three paragraphs.
* You are not a financial adviser or a doctor. You help to organise and to count — the person
  makes the decisions.
