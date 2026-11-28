# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The owner of a young Uzbek IT company (QUTLIYA) and their team. They work at a computer, several times a day:
they look over new government tenders and freelance jobs, decide which lots are worth bidding on, check money
(start sum, deposit, advance), read a lot's documents through the AI chat, and mark lots they bid on.
Telegram notifications bring them to the site; deep work happens on the desktop site.

## Product Purpose

A private workspace behind a login (shindo.uz/tender) for a bot that watches Uzbek public-procurement sites
and freelance boards. It turns thousands of open lots into a short list worth the team's time: IT service
tenders, goods/service tenders with Uzum market price checks, freelance jobs, and an AI chat that answers
questions about any lot from its own documents. Success: the team sees in minutes what is new, what fits,
how much money it ties up, and what to do next — without opening five procurement sites.

## Positioning

One place that reads all five procurement sources (etender.uzex.uz, xarid.uzex.uz — taklif so'rovi, tanlov,
auksion — and xt-xarid.uz / Hayot birja), filters for IT services, has Gemini read the tender documents
against the company's own profile, and lets the team ask the documents questions.

## Operating Context

- Server-rendered pages (aiohttp, `app/web/`), one CSS string in `app/web/views.py`, one small `app.js`;
  strict CSP (no inline script, no external assets), light and dark themes via a cookie.
- Pages: Bosh sahifa (three cards: IT, goods, freelance), IT tenderlar (table + filters + chips),
  Tovar va xizmat (catalog of every open lot), Uzum narxlari (price comparison per item/lot),
  AI chat (lot Q&A), Frilans ishlar, Telegram xabarlar (feed copy), lot and job detail pages, Excel export.
- Data refreshes every 20 minutes (tenders) and 5–10 minutes (freelance). Times are Tashkent.
- Language: Uzbek (Latin), with lot titles often in Russian or Uzbek Cyrillic.

## Capabilities and Constraints

- Dense tables of lots with sums in so'm and $, deadlines, deposits, AI verdicts (QATNASHAMIZ / SHARTLI / YO'Q),
  win %, fit score; filters, chips, sorting, bulk actions.
- Runs on a phone-hosted server (A23); pages must stay light: no web fonts or images from outside, no frameworks.
- Name stays "IT tenderlar va frilans ishlar" (config `web.title`).

## Brand Commitments

- Product name: "IT tenderlar va frilans ishlar". No other brand assets exist; do not invent a logo or claims.
- Standing preference (2026-11-28): plain, classic UI in the Google Workspace (Gmail / Drive) idiom — white surfaces,
  pill navigation, one blue accent, soft elevation, system fonts. No themed worlds, ornaments or decorative lettering;
  the user rejected the «Chorsu» themed redesign as too unusual. Keep money, AI verdict and deadline first in tables.

## Evidence on Hand

- Real lot data only (from the five sources). No testimonials, customers or metrics to show; none may be invented.

## Product Principles

1. Decide fast: what is new, what fits, what it costs, when it closes — visible without clicking.
2. Money and deadlines are the loudest facts on any lot.
3. The AI's verdict is advice with its reasons, never a hidden filter the team cannot see past.
4. Dense but calm: many rows a day, read by the same few people, for hours.

## Accessibility & Inclusion

No product-specific requirement established; keep WCAG AA contrast in both themes and full keyboard use.
