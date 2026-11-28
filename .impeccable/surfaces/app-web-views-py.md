---
version: 1
slug: "app-web-views-py"
primary_target: "app/web/views.py"
related_targets: ["app/web/tables.py","app/web/static/app.js"]
---

Scope: the whole logged-in site (app/web: layout shell, Bosh sahifa, IT tenderlar, Tovar va xizmat, Uzum narxlari, AI chat, Frilans, Telegram, lot/job pages, login). Mode: Operate.
Audience/task: owner + team at a desktop, several times a day: see what is new, what fits, how much money, when it closes; then act.
User asked (round 2): simple, classic, ordinary UI/UX — not a themed world. Reference: Google Gmail / Drive. Keep: light/dark themes; money, AI verdict and deadline first in tables; a plain "closing soon" list on the home page.
Constraints: server-rendered HTML, one CSS string, strict CSP, phone-hosted server.

## Direction contract
THESIS: The category standard, played straight at Google Workspace craft: a tool that disappears into the task. Refuses themed ornament, decorative type and invented affordances.
OWN-WORLD: Gmail/Drive idiom — white content surface on a faint blue-grey app ground, pill-shaped nav items with a light blue active state, one Google blue for actions and links, soft two-layer elevation, rounded 8–16px corners, system sans (Roboto where present), quiet grey dividers; dark theme in Google's dark greys with light-blue accent.
STORY: open the site, see the closing-soon list and three section summaries, open a table, filter, read money/verdict/deadline first, open a lot or ask the AI.
FIRST VIEWPORT: light sidebar with pill nav; top bar with page title; home: "Muddati yaqin lotlar" as a simple list (title, sum, time left, verdict chip), then three section cards; tables: chips, filter row, white table with sticky header, price/verdict/deadline first.
FORM: canon (the category standard), user-chosen exit after the «Chorsu» build; reference Gmail/Drive; seed key 11723311 (canon).
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
