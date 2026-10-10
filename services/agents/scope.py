"""What a request needs that HOOD doesn't have yet, said plainly before anything is planned.

Owner's rule (2026-10-10): HOOD does what the owner asks with what it has; when something is
missing it asks permission to install it (inside WSL2, once per tool), never a flat "no".
The notes are deterministic (keyword rules, not a model), shown in chat, the plan dialog and
the mission page, and handed to the planner so its summary states any substitution.
"""
from __future__ import annotations

import re
from typing import List, Optional

AGENT_CAN_BUILD = ("Agents build three kinds of work: websites made of HTML, CSS and JavaScript (checked by "
                   "reading the files; works anywhere), WordPress sites (a real WordPress theme plus pages, run "
                   "and checked in the sandbox; needs PHP, WordPress, its SQLite plugin and WP-CLI, which HOOD "
                   "installs inside WSL2 after the owner allows each tool once), and Python programs using the "
                   "standard library (tests run in HOOD's sandbox; on Windows HOOD sets up its own Linux sandbox "
                   "after one owner approval, or the owner approves \"Run on my PC\" per mission). When something "
                   "is missing HOOD never just says no: it names exactly what it would install or set up and asks "
                   "the owner once; after the OK HOOD does every step itself. Not available yet: frameworks/npm in missions, "
                   "WooCommerce, accounts on hosted platforms (Shopify, Wix), real payments, publishing online, "
                   "native phone apps.")

# (pattern, note for websites, note for Python programs or None to reuse the website note)
_RULES = [
    (r"\bword ?press\b|\bwp[- ]admin\b", "@wordpress", None),
    (r"\bwoo ?commerce\b",
     "WooCommerce isn't part of WordPress missions yet: pages can show products, prices and an order form, but no "
     "shop engine or real payments.", None),
    (r"\blaravel\b|\bsymfony\b|\bdrupal\b|\bjoomla\b",
     "Laravel, Symfony, Drupal and Joomla aren't mission kinds yet; HOOD can build a WordPress site, a website or "
     "a Python program instead.", None),
    (r"\bmysql\b|\bpostgres(?:ql)?\b|\bmongo(?:db)?\b|\bmariadb\b|\bdatabase\b|\bbase de donn[ée]es\b|\bbdd\b",
     "No database server here: a website keeps its data in the page (for example a product list in JavaScript).",
     "No database server here: a Python program can store data in SQLite files instead."),
    (r"\bshopify\b|\bwix\b|\bsquarespace\b|\bwebflow\b|\bprestashop\b|\bmagento\b",
     "The agents can't create or change sites on hosted platforms; they make files on this computer.", None),
    (r"\bstripe\b|\bpaypal\b|\breal payments?\b|\bpay(?:ment)? online\b|\bcredit card\b|\bcarte bancaire\b"
     r"|\bpaiement en ligne\b|\bcheckout\b|\bpaiement\b",
     "No real payments: a website can show a cart and an order summary, but no money is taken.", None),
    (r"\breact\b|\bvue(?:\.js)?\b|\bangular\b|\bnext\.?js\b|\bnode(?:\.js)?\b|\bnpm\b|\btailwind\b|\bbootstrap\b",
     "Frameworks and npm packages aren't used in missions yet (HOOD can install Node.js from Settings > Tools, "
     "but agents don't build with it yet): plain HTML, CSS and JavaScript instead.",
     "No JavaScript frameworks for a Python program; standard library only."),
    (r"\bdeploy\b|\bhosting\b|\bhost it\b|\bdomain name\b|\bpublish (?:it )?online\b|\bmettre en ligne\b"
     r"|\bh[ée]berg",
     "Nothing is published online: you get the files on this computer to upload yourself.", None),
    (r"\bandroid\b|\bios app\b|\biphone app\b|\bmobile app\b|\bapplication mobile\b|\bapp store\b|\bplay store\b",
     "No native phone apps: a responsive website works on phones.", None),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), web, py) for p, web, py in _RULES]


_NEGATION = re.compile(r"\b(no|not|without|never|don'?t|do not|cannot|can'?t|instead of|pas de|sans|aucun|ni)\b",
                       re.IGNORECASE)


def _asked_for(rx: "re.Pattern", text: str) -> bool:
    """True when the text asks for it, not when it rules it out ("No WordPress, PHP or databases")."""
    for m in rx.finditer(text):
        start = max(text.rfind(c, 0, m.start()) for c in ".\n;:!?")
        if not _NEGATION.search(text[start + 1:m.start()]):
            return True
    return False


def _wordpress_note(profile: str, tools: Optional[dict]) -> Optional[str]:
    if profile != "wordpress_site":
        return ("This plan makes a static website, not WordPress. For a real WordPress site choose the kind "
                "\"WordPress site\": HOOD will ask your OK to install PHP and WordPress inside WSL2.")
    if tools is None:
        return "Needs PHP, WordPress, its SQLite plugin and WP-CLI installed (Settings > Tools)."
    if tools.get("ready"):
        return None
    names = ", ".join(tools["names"][t] for t in tools.get("missing", []))
    sandbox = tools.get("sandbox") or {}
    if sandbox and not sandbox.get("ready"):
        if not sandbox.get("approved") and tools.get("unapproved"):
            return (f"Needs your OK once: HOOD sets up its own Linux sandbox on this PC and installs {names} inside "
                    "it (official sources, checked). Press \"Allow & set up\"; HOOD does everything else. The first "
                    "time, Windows may show its administrator prompt and ask for a restart.")
        if sandbox.get("approved"):
            return (f"HOOD is setting up its Linux sandbox, then installs {names} (you allowed it). Missions "
                    "continue by themselves when it's ready.")
    if tools.get("problem"):
        return f"Needs {names} installed first. {tools['problem']}"
    if tools.get("unapproved"):
        return (f"Needs your OK to install {names} inside WSL2 (once per tool, from official sources). "
                "Press \"Allow & install\"; after that HOOD reuses and updates them without asking.")
    return f"Installing {names} (you allowed them); missions continue by themselves when they're ready."


def scope_notes(objective: str, profile: str = "static_web", tools: Optional[dict] = None) -> List[str]:
    """What this objective needs that isn't there yet (empty when nothing stands out).

    ``tools`` is the toolbox's needs() for the profile, when known."""
    text = objective or ""
    notes: List[str] = []
    wordpress = profile == "wordpress_site"
    for rx, web, py in _COMPILED:
        if not _asked_for(rx, text):
            continue
        if web == "@wordpress":
            note = _wordpress_note(profile, tools)
        elif wordpress and "database server" in web:
            note = "WordPress keeps its data in a SQLite file here, so no MySQL server is needed."
        else:
            note = web if profile in ("static_web", "wordpress_site") or py is None else py
        if note and note not in notes:
            notes.append(note)
    if wordpress and tools is not None and not tools.get("ready") and not any("install" in n for n in notes):
        extra = _wordpress_note(profile, tools)   # a WordPress mission whose brief doesn't say "WordPress"
        if extra:
            notes.insert(0, extra)
    return notes
