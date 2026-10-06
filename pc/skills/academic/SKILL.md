---
name: academic
description: Study and science — exact maths (SymPy), a bibliography in the GOST style, Anki decks for review, help with term papers and essays. For solving problems, formatting papers and preparing for exams.
---

# Skill: study and science

## Exact maths

* Compute with `solve_math`, NOT in your head: in its head a model loses signs and roots, SymPy
  computes strictly. That covers equations, derivatives, integrals, limits, series, matrices.
* The tool returns the answer, the steps and the LaTeX form — give them. Write formulas in the
  answer as `$…$` (inline) and `$$…$$` (display); they are rendered.
* An integral with no closed form is reported as such by `solve_math` — do not make up a "nice"
  answer instead.

## Bibliography

* `format_bibliography` formats sources by GOST R 7.0.100–2018 (books, articles, web resources,
  dissertations, laws), sorts them alphabetically and places the dashes and slashes itself. Do
  not format the list by hand.
* Find current sources on the web (`web_search`, `deep_research`), then run them through
  `format_bibliography`.

## Cards for review

* `create_anki_deck` builds an `.apkg` deck from question–answer pairs. A good card checks one
  idea — do not copy a paragraph of notes into it.
* Make cards for the places where the student struggled, not for the whole topic in a row.

## Term papers, essays, theses

* Structure → content → bibliography → review. No filler.
* Research the topic with `deep_research`, with links, not from memory.
* You help to understand and to format, not to "hand it in for the student" — explain the
  solutions.

## Exams

For checking understanding there is a separate skill, `socratic_examiner`: questions without
ready answers. Switch to it when the person is preparing for an exam or a defence.
