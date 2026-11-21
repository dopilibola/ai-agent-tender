"""Gemini looks at the phone's Chrome (screenshot + list of interactive elements) and places one approved bid.

Gemini only chooses *which* element to touch. The proposal text, price and days are typed by code from the
approved Proposal row, so the model can never change what gets sent. The final "place bid" click happens only
when `live` is true (config phone.dry_run: false); in dry-run the agent stops right before it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from google.genai import types
from pydantic import BaseModel

log = logging.getLogger(__name__)

ACTIONS = ("click", "fill", "scroll", "wait", "submit", "submitted", "need_human", "fail")
FIELDS = ("proposal", "price", "days", "text")

OBSERVE_JS = r"""(() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'
      && r.bottom > -100 && r.top < innerHeight * 2.5; };
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=checkbox],'
    + '[role=textbox],[contenteditable=true]';
  document.querySelectorAll('[data-fh]').forEach(e => e.removeAttribute('data-fh'));
  const out = []; let n = 0;
  for (const e of document.querySelectorAll(sel)) {
    if (n >= 120 || !vis(e)) continue;
    const id = ++n; e.setAttribute('data-fh', id);
    const lab = (e.getAttribute('aria-label') || e.placeholder || e.innerText || e.title || e.name || '')
      .trim().replace(/\s+/g, ' ').slice(0, 80);
    const r = e.getBoundingClientRect();
    const val = (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' || e.tagName === 'SELECT') ? String(e.value)
      : (e.isContentEditable ? e.innerText : '');
    out.push({id, tag: e.tagName.toLowerCase(), type: e.type || e.getAttribute('role') || '', label: lab,
      value: val.slice(0, 60), value_len: val.length, checked: !!e.checked, disabled: !!e.disabled,
      on_screen: r.top >= 0 && r.bottom <= innerHeight});
  }
  return {url: location.href, title: document.title,
          text: (document.body ? document.body.innerText : '').replace(/\n\s*\n+/g, '\n').slice(0, 2500),
          elements: out};
})()"""

FOCUS_JS = r"""((id) => { const e = document.querySelector(`[data-fh="${id}"]`); if (!e) return false;
  e.scrollIntoView({block: 'center'}); e.focus();
  try { if (e.isContentEditable) document.execCommand('selectAll', false, null); else if (e.select) e.select(); }
  catch (err) {}
  return true; })(%d)"""

VALUE_JS = r"""((id) => { const e = document.querySelector(`[data-fh="${id}"]`); if (!e) return null;
  return e.isContentEditable ? e.innerText : String(e.value); })(%d)"""

SET_JS = r"""((id, v) => { const e = document.querySelector(`[data-fh="${id}"]`); if (!e) return false;
  if (e.isContentEditable) { e.innerText = v; }
  else { const p = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
         Object.getOwnPropertyDescriptor(p, 'value').set.call(e, v); }
  e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));
  return true; })(%d, %s)"""

CLICK_JS = r"""((id) => { const e = document.querySelector(`[data-fh="${id}"]`); if (!e) return false;
  e.scrollIntoView({block: 'center'}); e.click(); return true; })(%d)"""

SYSTEM = """You operate the owner's logged-in mobile Chrome to place ONE bid/proposal on a freelance project page.
Goal: open the bid form, fill the proposal (cover letter) text, the bid amount and the delivery time, then press the
final button that places the bid. You get a screenshot plus the list of interactive elements (use their ids).

Return exactly one action per step:
- click: open forms/tabs/menus ("Bid on this project", "Place a bid", "Apply", "Откликнуться"…). Never a final send.
- fill + field: "proposal" for the cover letter / description textarea, "price" for the bid amount, "days" for
  delivery time / period. Use field "text" with `text` ONLY for another required short input (e.g. milestone
  title "Full project"). Never write claims about the owner yourself.
- scroll: the needed element is not in the list yet. wait: the page is still loading.
- submit: ONLY the final button that actually places/sends the bid ("Place Bid", "Submit", "Отправить"), and only
  when the proposal, price and days are filled (see value_len / value) and look right.
- submitted: the page clearly confirms the bid was placed (e.g. "Your bid has been placed", "Edit bid"/"Retract bid"
  shown for the owner's own bid).
- need_human: login page, captcha, phone/ID verification, skills exam, membership/upgrade/payment required,
  no bids left, project closed or already bid, or the bid currency differs from the proposal currency.
- fail: stuck after trying.
Never pay or buy anything; leave paid upgrades (sponsored/highlight/sealed bid) unchecked. If a popup or cookie
banner blocks the page, close it. `reason` = one short sentence in Uzbek."""


class Step(BaseModel):
    action: str
    element: int | None = None
    field: str | None = None
    text: str | None = None
    reason: str = ""


@dataclass
class BidFields:
    proposal: str
    price: str
    currency: str
    days: str


@dataclass
class AgentResult:
    status: str  # submitted | ready (dry-run) | unconfirmed | need_human | fail | phone_offline
    reason: str
    steps: list[str] = field(default_factory=list)
    screenshot: bytes | None = None
    url: str = ""


class TabLike(Protocol):
    async def eval(self, expression: str) -> Any: ...
    async def screenshot(self, fmt: str = "jpeg", quality: int = 45) -> bytes: ...
    async def insert_text(self, text: str) -> None: ...
    async def wait_loaded(self, timeout: float = 30) -> None: ...


def _norm(s: str) -> str:
    return " ".join(str(s).split())


class BidAgent:
    def __init__(self, gemini, *, max_steps: int = 25, step_delay_s: float = 1.5, confirm_steps: int = 4):
        self.gemini = gemini
        self.max_steps = max_steps
        self.delay = step_delay_s
        self.confirm_steps = confirm_steps

    async def _decide(self, tab: TabLike, bid: BidFields, history: list[str], obs: dict[str, Any]) -> Step:
        shot = await tab.screenshot("jpeg", 40)
        state = {
            "bid": {"price": bid.price, "currency": bid.currency, "days": bid.days,
                    "proposal_chars": len(bid.proposal), "proposal_start": bid.proposal[:160]},
            "history": history[-12:],
            "page": obs,
        }
        prompt = [types.Part.from_bytes(data=shot, mime_type="image/jpeg"),
                  "STATE:\n" + json.dumps(state, ensure_ascii=False)]
        return await self.gemini.generate_json(prompt, Step, role="fast", system=SYSTEM, purpose="phone_submit",
                                               temperature=0)

    async def _fill(self, tab: TabLike, el: int, value: str) -> bool:
        if not await tab.eval(FOCUS_JS % el):
            return False
        await tab.insert_text(value)
        await asyncio.sleep(0.3)
        got = await tab.eval(VALUE_JS % el)
        if got is None or _norm(got) != _norm(value):  # e.g. a framework ignored insertText → set it natively
            await tab.eval(SET_JS % (el, json.dumps(value)))
            got = await tab.eval(VALUE_JS % el)
        return got is not None and _norm(got) == _norm(value)

    async def run(self, tab: TabLike, bid: BidFields, *, live: bool) -> AgentResult:
        history: list[str] = []
        filled: set[str] = set()
        clicked_submit_at: int | None = None
        obs: dict[str, Any] = {}
        for i in range(self.max_steps):
            await asyncio.sleep(self.delay)
            obs = await tab.eval(OBSERVE_JS) or {}
            if clicked_submit_at is not None and i - clicked_submit_at > self.confirm_steps:
                return await self._end(tab, "unconfirmed", "Submit bosildi, lekin sayt tasdiqlamadi — tekshiring",
                                       history, obs)
            try:
                step = await self._decide(tab, bid, history, obs)
            except Exception as e:  # Gemini down / malformed after retries
                return await self._end(tab, "fail", f"Gemini: {type(e).__name__}", history, obs)
            ids = {e["id"]: e for e in obs.get("elements", [])}
            el = ids.get(step.element or -1)
            label = f"#{step.element} «{el['label'][:40]}»" if el else ""
            log.info("phone.step", extra={"i": i, "action": step.action, "el": label, "reason": step.reason})
            act = step.action if step.action in ACTIONS else "fail"

            if act == "submitted":
                if clicked_submit_at is None:
                    history.append(f"{i}: 'submitted' rad etildi — Submit hali bosilmagan")
                    continue
                return await self._end(tab, "submitted", step.reason or "Topshirildi", history, obs)
            if act in ("need_human", "fail"):
                return await self._end(tab, act, step.reason or act, history, obs)
            if act == "scroll":
                await tab.eval("window.scrollBy(0, Math.round(innerHeight * 0.8))")
                history.append(f"{i}: scroll")
                continue
            if act == "wait":
                await tab.wait_loaded(10)
                history.append(f"{i}: wait")
                continue
            if el is None:
                history.append(f"{i}: {act} — #{step.element} ro'yxatda yo'q, qayta ko'r")
                continue
            if act == "fill":
                fld = step.field if step.field in FIELDS else None
                value = {"proposal": bid.proposal, "price": bid.price, "days": bid.days}.get(fld or "", None)
                if fld == "text":
                    value = (step.text or "").strip()[:120]
                if not value:
                    history.append(f"{i}: fill {label} — maydon/matn noto'g'ri")
                    continue
                ok = await self._fill(tab, step.element, value)
                if ok and fld in ("proposal", "price", "days"):
                    filled.add(fld)
                history.append(f"{i}: fill {fld} → {label}: {'OK' if ok else 'YOZILMADI'}")
                continue
            if act == "submit":
                missing = {"proposal", "price", "days"} - filled
                if "proposal" in missing or "price" in missing:
                    history.append(f"{i}: submit rad etildi — hali to'ldirilmagan: {', '.join(sorted(missing))}")
                    continue
                if not live:
                    return await self._end(tab, "ready", f"Sinov: hammasi to'ldirildi, {label} bosilmadi", history,
                                           obs)
                await tab.eval(CLICK_JS % step.element)
                clicked_submit_at = i
                history.append(f"{i}: SUBMIT {label}")
                await tab.wait_loaded(15)
                continue
            await tab.eval(CLICK_JS % step.element)  # click
            history.append(f"{i}: click {label}")
            await tab.wait_loaded(15)
        status = "unconfirmed" if clicked_submit_at is not None else "fail"
        return await self._end(tab, status, f"{self.max_steps} qadamda tugamadi", history, obs)

    async def _end(self, tab: TabLike, status: str, reason: str, history: list[str], obs: dict[str, Any]) -> AgentResult:
        shot = None
        try:
            shot = await tab.screenshot("png")
        except Exception:
            log.warning("phone.final_screenshot_failed")
        return AgentResult(status, reason, history, shot, str(obs.get("url", "")))
