---
name: IT tenderlar va frilans ishlar
description: A private Uzbek tender and freelance workspace built in the plain Google Workspace (Gmail / Drive) idiom, with money, AI verdict and deadline first.
colors:
  app-ground: "#f8fafd"
  surface: "#ffffff"
  soft-surface: "#f1f4f9"
  hover: "#eef1f6"
  chip: "#eef1f6"
  ink: "#1f1f1f"
  muted: "#5f6368"
  faint: "#80868b"
  line: "#e3e6ea"
  line-strong: "#c4c7c5"
  nav-hover: "#e9edf3"
  nav-on: "#d3e3fd"
  nav-on-ink: "#041e49"
  google-blue: "#0b57d0"
  google-blue-deep: "#0842a0"
  blue-wash: "#d3e3fd"
  on-blue: "#ffffff"
  good: "#146c2e"
  good-wash: "#e6f4ea"
  maybe: "#8a5100"
  maybe-wash: "#fef3d6"
  bad: "#b3261e"
  bad-wash: "#fce8e6"
  bad-line: "#f2b8b5"
  bid: "#7a1fa2"
  bid-wash: "#f3e8fd"
  tooltip: "#3c4043"
  dark-app-ground: "#1b1b1b"
  dark-surface: "#282a2c"
  dark-soft-surface: "#303134"
  dark-field: "#1f1f1f"
  dark-hover: "#333537"
  dark-chip: "#37393b"
  dark-ink: "#e3e3e3"
  dark-muted: "#c4c7c5"
  dark-faint: "#8e918f"
  dark-line: "#3c4043"
  dark-line-strong: "#5f6368"
  dark-nav-hover: "#2d2f31"
  dark-nav-on: "#004a77"
  dark-nav-on-ink: "#c2e7ff"
  dark-blue: "#a8c7fa"
  dark-blue-light: "#d3e3fd"
  dark-blue-wash: "#0842a0"
  dark-on-blue: "#062e6f"
  dark-good: "#6dd58c"
  dark-good-wash: "#0f3d21"
  dark-maybe: "#fdd663"
  dark-maybe-wash: "#3d3000"
  dark-bad: "#f2b8b5"
  dark-bad-wash: "#601410"
  dark-bad-line: "#8c1d18"
  dark-bid: "#e7b7ff"
  dark-bid-wash: "#4a1a63"
typography:
  page-title:
    fontFamily: "Roboto, \"Google Sans Text\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 400
    lineHeight: 1.3
  stat-value:
    fontFamily: "Roboto, \"Google Sans Text\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 500
    lineHeight: 1.3
    fontFeature: "\"tnum\""
  title:
    fontFamily: "Roboto, \"Google Sans Text\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 500
    lineHeight: 1.5
  body:
    fontFamily: "Roboto, \"Google Sans Text\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"tnum\""
  table:
    fontFamily: "Roboto, \"Google Sans Text\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Arial, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"tnum\""
  table-header:
    fontFamily: "Roboto, \"Google Sans Text\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Arial, sans-serif"
    fontSize: "12.5px"
    fontWeight: 500
    lineHeight: 1.3
  label:
    fontFamily: "Roboto, \"Google Sans Text\", ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.5
  mono:
    fontFamily: "ui-monospace, \"SF Mono\", Menlo, Consolas, monospace"
    fontSize: "12.5px"
    fontWeight: 400
rounded:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "28px"
  pill: "999px"
spacing:
  xxs: "4px"
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "20px"
  xl: "24px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.google-blue}"
    textColor: "{colors.on-blue}"
    rounded: "{rounded.pill}"
    padding: "0 20px"
    height: "40px"
    typography: "{typography.body}"
  button-primary-hover:
    backgroundColor: "{colors.google-blue-deep}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.google-blue}"
    rounded: "{rounded.pill}"
    padding: "0 20px"
    height: "40px"
  button-ghost-hover:
    backgroundColor: "{colors.blue-wash}"
  button-xs:
    rounded: "{rounded.pill}"
    padding: "0 14px"
    height: "32px"
  input-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "40px"
  input-search:
    backgroundColor: "{colors.soft-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "0 18px"
    height: "40px"
  nav-item:
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "0 16px"
    height: "36px"
  nav-item-active:
    backgroundColor: "{colors.nav-on}"
    textColor: "{colors.nav-on-ink}"
  filter-chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  filter-chip-on:
    backgroundColor: "{colors.blue-wash}"
    textColor: "{colors.nav-on-ink}"
  pill-neutral:
    backgroundColor: "{colors.chip}"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: "2px 10px"
    typography: "{typography.label}"
  pill-good:
    backgroundColor: "{colors.good-wash}"
    textColor: "{colors.good}"
  pill-maybe:
    backgroundColor: "{colors.maybe-wash}"
    textColor: "{colors.maybe}"
  pill-bad:
    backgroundColor: "{colors.bad-wash}"
    textColor: "{colors.bad}"
  pill-blue:
    backgroundColor: "{colors.blue-wash}"
    textColor: "{colors.nav-on-ink}"
  pill-bid:
    backgroundColor: "{colors.bid-wash}"
    textColor: "{colors.bid}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "18px 20px 16px"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "16px 20px"
  stats-strip:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "12px 18px"
  table-frame:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
  table-cell:
    padding: "10px 12px"
    typography: "{typography.table}"
  flash-ok:
    backgroundColor: "{colors.good-wash}"
    textColor: "{colors.good}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  flash-error:
    backgroundColor: "{colors.bad-wash}"
    textColor: "{colors.bad}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  chat-bubble-user:
    backgroundColor: "{colors.google-blue}"
    textColor: "{colors.on-blue}"
    rounded: "14px"
    padding: "11px 15px"
  chat-bubble-ai:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "14px"
    padding: "11px 15px"
---

# Design System: IT tenderlar va frilans ishlar

## Overview

**Creative North Star: "The Quiet Workspace"**

The site is the category standard played straight, at Google Workspace craft: a Gmail / Drive shell that disappears into the task. A faint blue-grey app ground holds a sidebar of pill-shaped nav items; content sits on white surfaces with thin grey dividers, gentle 8–16px corners and almost no shadow. One Google blue carries every action, link, focus ring and selected state. Everything else is grey, except the status colours that report a lot's verdict and urgency.

Density is high but calm. The same few people read many rows a day, for hours, so the tables use 13.5px tabular figures, sticky headers and a fixed reading order: description, money, AI verdict, deadline, then the rest. The home page opens with a plain "Muddati yaqin lotlar" list (title, sum, time left, verdict pill), followed by three section cards. Light and dark themes are equal citizens; dark uses Google's dark greys with a light-blue accent.

The user rejected a themed redesign (bazaar ornament, price-tag cards, handwritten lettering) as too unusual. This system has no ornament, no decorative type and no invented affordances. If a component would look out of place in Gmail or Drive, it does not belong here.

**Key Characteristics:**
- Faint blue-grey ground, white content surfaces, hairline grey dividers.
- Pill navigation with a light-blue active state; pill buttons; 8px-cornered chips and status pills.
- One blue accent; green / amber / red / purple only as status.
- System sans (Roboto where present), tabular figures everywhere, no web fonts.
- Flat by default: one faint shadow on the stats strip and on button hover, nothing more.
- Money, AI verdict and deadline are the first data columns of every lot table.

## Colors

A neutral Google palette: blue-tinted greys for structure, one Google blue for action, and Material-style tonal status pairs (strong ink on a pale wash) for verdicts.

### Primary
- **Google Blue** (`google-blue`; dark theme `dark-blue`): primary buttons, links, focus outline, checked checkboxes, sorted column header, progress bars, the user's chat bubble, and the logo and avatar discs.
- **Deep Google Blue** (`google-blue-deep`; dark theme `dark-blue-light`): primary button hover.
- **Blue Wash** (`blue-wash`; dark theme `dark-blue-wash`): the selected state everywhere outside the sidebar: active filter chip, active segment, selected table row, bulk-action bar, ghost-button hover, "Yangi" status pill, quoted text in AI answers, text selection.

### Neutral
- **App Ground** (`app-ground` / `dark-app-ground`): page background, sidebar and sticky top bar; the sidebar has no border or fill of its own.
- **Surface** (`surface` / `dark-surface`): cards, panels, tables, stats strip, filter boxes, chips at rest. Inputs use `surface` in light and `dark-field` in dark.
- **Soft Surface** (`soft-surface` / `dark-soft-surface`): resting search field, number tiles inside home cards, table footer.
- **Hover** (`hover` / `dark-hover`): row and chip hover. **Chip** (`chip` / `dark-chip`): neutral pills, inline code, empty progress tracks.
- **Ink, Muted, Faint** (`ink`, `muted`, `faint` and dark counterparts): body text; captions, column headers and secondary values; placeholders and the footer.
- **Line, Line Strong** (`line`, `line-strong` and dark counterparts): dividers and surface borders; input, chip and ghost-button strokes.
- **Nav On, Nav On Ink** (`nav-on`, `nav-on-ink`; dark `dark-nav-on`, `dark-nav-on-ink`): the active sidebar item. Nav On Ink also colours text on Blue Wash in light theme; in dark theme that text uses `dark-blue-light`.

### Status
- **Good** (`good` on `good-wash`): AI verdict "Qatnashamiz", success flash, positive counts in stats.
- **Maybe** (`maybe` on `maybe-wash`): verdict "Shartli"; deadline inside 3 days.
- **Bad** (`bad` on `bad-wash`, edged by `bad-line` on chat errors): verdict "Yo'q", expired or closed lots, error flash; deadline inside 24 hours.
- **Bid** (`bid` on `bid-wash`): reserved for lots the team is bidding on ("Qatnashyapmiz").

### Named Rules
**The One Blue Rule.** Blue means "you can act here" or "this is selected". Never use it for decoration, headings or status.

**The Wash-Not-Fill Rule.** Status and selection are a pale wash with a strong ink of the same hue. A saturated fill is reserved for the primary button and the user's chat bubble.

**The Zero Is Not Good News Rule.** A count in the good colour turns green only when it is above zero; a zero stays in ink.

## Typography

**Body Font:** Roboto, falling back to Google Sans Text, then the platform system sans (`ui-sans-serif`, `system-ui`, `-apple-system`, Segoe UI, Arial).
**Mono Font:** `ui-monospace`, SF Mono, Menlo, Consolas. Used only for lot numbers and code.

**Character:** One system sans at Google's restrained weights (400 and 500, 600 only for the active nav item). No web fonts are loaded; tabular figures are on for the whole page so sums and dates line up.

### Hierarchy
- **Page Title** (400, 22px, 1.3): the top-bar title and `h1`. Regular weight, not bold, as in Gmail.
- **Stat Value** (500, 22px, 1.3): big numbers in the stats strip and the money total. Home-card tiles use 18px / 500.
- **Title** (500, 16px): section headings (`h2`), home-card titles, the "Muddati yaqin lotlar" heading.
- **Body** (400, 14px, 1.5): everything else, including nav items, buttons and inputs. Prose blocks cap at 80–90ch.
- **Table** (400, 13.5px): table cells; money cells are 500.
- **Table Header** (500, 12.5px, 1.3, muted): column headers. Labels longer than 14 characters wrap onto two lines, 96–130px wide, instead of widening the column.
- **Label** (12–13px): captions, pill text (500, 12px), chip text (500, 13px), sidebar group headings (500, 12px, muted), and the dollar line under a sum (11.5–12px, muted).

### Named Rules
**The Two-Weight Rule.** Use 400 and 500; 600 marks only the active nav item. Hierarchy comes from size and the muted colour, not from bold.

**The No-Ornament Type Rule.** No display faces, script or handwriting, uppercase kickers or letter-spaced labels.

## Layout

The desktop shell is a two-column grid: a 256px sticky sidebar and a fluid content column. The content column has a 64px sticky top bar (page title, a muted one-line subtitle, tools pushed right) and a main area padded 8px 24px 48px. Pages stack full-width blocks: flash banner, filter box with chip rows, stats strip, folded help box, filter row, result count, then the table in a scrolling frame capped at `100vh - 140px` with a sticky header. A right-edge scroll shadow shows when the table overflows.

The home page puts the closing-soon list first, then the section cards in an auto-fit grid (minimum 300px, 16px gap). Each list row is a four-column grid: title and source, sum with dollars under it, time left with date under it, verdict pill aligned right. The AI chat is a two-column grid: conversation, plus a 300px sticky lot-facts panel.

Spacing runs on a 4px base: 4 / 8 / 12 / 16 / 20 / 24 / 48. Blocks are 12–16px apart, card padding is 16–20px, and table cells are 10px 12px.

At 760px and below, the sidebar becomes a horizontal, scrollable icon-and-label strip with a bottom divider. Group labels and the product name are hidden, the top bar drops to 56px with no subtitle, stats wrap two per row, the search field takes the full width, and closing-soon rows collapse to two columns. At 980px and below, the chat facts panel moves above the conversation.

## Elevation & Depth

The system is flat: depth comes from tonal layering (blue-grey ground, white surfaces, soft-grey tiles) and 1px grey borders. Shadows are rare and only one step deep.

### Shadow Vocabulary
- **Hairline lift** (`box-shadow: 0 1px 2px rgba(60,64,67,.12)`; dark `0 1px 2px rgba(0,0,0,.4)`): the stats strip at rest, and any button on hover.
- **Selection ring** (`box-shadow: 0 0 0 2px` Blue Wash): the chosen theme swatch and a targeted item panel.
- **Scroll shadow**: a 16px radial grey fade on the right edge of an overflowing table frame.

### Named Rules
**The Flat-By-Default Rule.** Cards, panels, tables and chips have a border, not a shadow. A shadow appears only on the stats strip and as hover feedback on buttons.

## Shapes

Corners are soft and follow the Workspace scale: 8px for small controls (inputs, chips, status pills, flash banners), 12px for framed strips (stats, filter box, table frame, bulk bar, chat composer), 16px for content cards (home cards, closing-soon list, panels, message cards), and 28px for the login card. Anything you navigate or press as a unit is fully rounded: nav items, buttons, the search field, segmented controls, suggestion chips and the round icon buttons. Chat bubbles use 14px corners with a 4px tail corner on the speaker's side. Avatars and icon buttons are circles. Borders are always 1px, except the 2px checkbox stroke.

Icons are inline 24-unit SVG line icons with a 1.7 stroke and round caps and joins. They are 20px by default, 18px in buttons, 16px in small buttons and 14px in chips. They are drawn, never emoji or glyphs.

## Components

### Buttons
- **Shape:** fully rounded pill (999px), 40px tall, 20px side padding, 500 weight, icon and label 8px apart.
- **Primary:** Google Blue fill, white text (dark-on-blue in dark theme).
- **Hover / Focus:** fill deepens to Deep Google Blue and gains the hairline lift; transitions are 0.15s on background and shadow. Focus is a 2px blue outline offset by 2px. Disabled buttons are 50% opacity.
- **Ghost:** transparent with a Line Strong stroke and blue text; hover fills with Blue Wash.
- **Link:** no stroke and blue text; hover fills with Blue Wash.
- **Small (xs):** 32px tall, 14px padding, 13px text, used for row and panel actions.
- **Icon buttons:** 40px circles in the sidebar footer (theme, profile, log out). They have a hover wash, a Nav On fill when active, and a dark tooltip above.

### Chips
- **Filter chips:** 32px tall, 8px corners, 1px Line Strong stroke on Surface, 500 weight label with a muted count. Selected chips switch to Blue Wash with no stroke. Options with zero results fade to 45% opacity.
- **Chip rows:** grouped in a bordered filter box, each row a bold label (minimum 130px) beside wrapping chips, separated by hairlines.
- **Segmented control:** a pill-outlined group with hairline separators; the active segment is Blue Wash.

### Status Pills
Small tonal labels: 8px corners, 2px 10px padding, 12px / 500. A 6px dot in the current colour leads the text. Variants are neutral (pending or not assessed), good, maybe, bad (also expired or closed), blue (new or notified) and bid (bidding). The AI verdict always appears as one of these pills.

### Cards / Containers
- **Corner Style:** 16px for cards and panels; 12px for strips and frames.
- **Background:** Surface, on the App Ground.
- **Shadow Strategy:** none; see Elevation & Depth.
- **Border:** 1px Line.
- **Internal Padding:** 16–20px. Home cards stack a title row with an icon, three number tiles on Soft Surface (12px corners), a "best items" list with hover rows, and a ghost "see all" button.

### Inputs / Fields
- **Style:** 40px tall, 8px corners, 1px Line Strong stroke on Surface (dark-field in dark), 12px padding, faint placeholder.
- **Search:** a pill on Soft Surface with no stroke, at least 300px wide; it turns to Surface on focus.
- **Focus:** the border turns blue and a 1px inset blue ring is added. There is no glow.
- **Checkboxes:** custom 18px squares with a 2px muted stroke and 3px corners; checked boxes fill blue with a drawn tick.

### Navigation
- **Sidebar:** the product name beside a 32px blue initial disc, then group labels (12px / 500, muted) and nav items. Each item is a 36px pill with a 20px muted icon and a 14px label, 16px apart. Hover fills Nav Hover. The active item fills Nav On with Nav On Ink text and icon at weight 600.
- **Top bar:** 64px tall and sticky on the App Ground. It holds a regular 22px page title, a muted subtitle and right-aligned tools.
- **Mobile:** the sidebar becomes a horizontal scrolling strip; see Layout.

### Data Table
White frame (12px corners, 1px Line) with a sticky header on Surface, muted headers and right-aligned numbers. Each header link carries a faint sort icon that turns blue on the active column. Rows are 10px 12px, divided by hairlines, and fully clickable. Hover tints the row and selection fills it with Blue Wash. A selection reveals the Blue Wash bulk-action bar above. Column order for lots is fixed: description, price, AI verdict, deadline, deposit, advance, then lot number, source and customer. Deadline cells turn amber inside 3 days and red inside 24 hours. A sticky Soft Surface footer row carries totals.

### Money Cells
Sums are plain text: the so'm amount at 500 weight, with the dollar equivalent in muted 11.5px under it. Both are right-aligned and never wrap. Stats strips show the money total at 22px in ink. There is no tag, frame or colour behind money.

### Stats Strip
A single bordered Surface strip (12px corners, hairline lift) divided into flexible cells of at least 170px by vertical hairlines. Each cell has a muted 13px label, a 22px / 500 value (green only when a positive "good" count) and a muted 12px note.

### Closing-Soon List
The home page's first block: a 16px-cornered Surface card with a title row ("Muddati yaqin lotlar", muted count, blue "see all" link). It lists up to six lots closing first as hairline-divided rows of title and source, sum and dollars, time left and date, and a verdict pill.

### Flash Banners
Full-width, 8px corners, 10px 16px padding, 500 weight. Success is good-on-good-wash. Errors are bad-on-bad-wash with a leading alert icon.

### AI Chat
The conversation is a column of bubbles. User bubbles are Google Blue with white text and sit on the right. AI bubbles are Surface with a 1px Line border, sit on the left, and render paragraphs, lists and Blue Wash quotes. While the AI answers, a muted bubble shows animated dots (static with reduced motion). The composer sticks to the bottom: an auto-growing textarea (12px corners, up to 200px) and a 46px round blue send button, with pill-shaped suggestion chips under it. Enter sends and Shift+Enter adds a new line.

## Do's and Don'ts

### Do:
- **Do** keep one accent: Google Blue (`#0b57d0` light, `#a8c7fa` dark) for actions, links, focus and selection only.
- **Do** put money, AI verdict and deadline in the first data columns of every lot table, right after the description.
- **Do** show status as a tonal pill (strong ink on a pale wash of the same hue) with a leading dot.
- **Do** use pill shapes (999px) for nav items, buttons and search, and 8 / 12 / 16px corners for chips, strips and cards.
- **Do** separate with 1px grey borders and tonal layers; reserve the hairline shadow for the stats strip and button hover.
- **Do** design every new screen in both themes using the paired dark tokens, and check WCAG AA in each.
- **Do** use the system sans at 400 / 500 with tabular figures, and draw icons as inline line SVGs.

### Don't:
- **Don't** introduce a themed world, ornament, textures or decorative illustration; the user rejected the bazaar-themed redesign as too unusual.
- **Don't** load web fonts or use display, script or handwriting faces.
- **Don't** frame money in tags, badges or coloured boxes; a sum is bold text with dollars under it.
- **Don't** use blue for status, or green / amber / red / purple for anything but status.
- **Don't** colour a zero count as good news.
- **Don't** use emoji or text glyphs as icons in the UI.
- **Don't** add a logo or brand mark beyond the initial disc and the product name.
