---
name: visual_qa
description: Checking a layout visually before handing it over — screenshots of pages and elements at different screen sizes and an autonomous vision audit (the model looks at the result and finds the defects). For web UIs, landing pages, components and any layout work.
---

# Skill: visual verification of a layout (vision in the loop)

The rule: **do not show the user a UI you built until you have seen it through the model's eyes**.
HTML/CSS text does not tell how it looks — spacing, contrast, overflow and overlaps show only on a
screenshot.

## Tools

* `screenshot_ui` — take a screenshot and show it in the Preview panel. Modes:
  - the whole page (`full_page=true`) or an element by CSS selector (`selector="#header"`);
  - the screen size `viewport`: `desktop` (1280×800), `tablet` (768×1024), `mobile` (375×667).
* `audit_ui` — take a screenshot AND give it to a multimodal model: it returns a list of defects
  (contrast, alignment, overflow, broken images, horizontal scroll) with fixes and a verdict
  "Ready to show / Needs fixes". `focus` — what to look at especially.

The target (`target`) is your dev server's URL (`http://localhost:5173`) or the path to an HTML
file in the working folder.

## The loop (Render → Audit → Fix)

1. Built or changed the UI → start the preview or the dev server.
2. `audit_ui(target=..., viewport="desktop")` — get the review.
3. There are remarks → fix the CSS/HTML (`edit_file` / `apply_patch`) for the reasons given.
4. `audit_ui` again — until it says "Ready to show". Do not show a broken intermediate result to
   the user.
5. For responsive layouts run the audit on all three viewports (`desktop`/`tablet`/`mobile`):
   typical bugs are a horizontal scroll on mobile and click targets that are too small.

## Details

* Screenshots go to `.screenshots/` in the working folder and show up in the Preview at once — a
  visual log of what was checked.
* A selector screenshot is cropped to the element's bounding box — handy for checking one
  component (a card, a header) without looking over the whole page.
* Auditing your own `localhost` or a local file needs no extra approvals; a screenshot of an
  external site asks for permission (it is a network action).
* The model is asked NOT to invent problems: if the layout is clean, it says so.
