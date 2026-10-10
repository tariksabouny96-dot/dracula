"""What HOOD's agents cannot build here, said plainly before anything is planned or approved.

The owner asked for "a website using WordPress"; the plan silently became a static site.
These notes are deterministic (keyword rules, not a model), shown in the plan dialog, the
mission page and chat, and handed to the planner so its summary states the substitution.
"""
from __future__ import annotations

import re
from typing import List

AGENT_CAN_BUILD = ("Agents can build two kinds of work on this computer: websites made of HTML, CSS and "
                   "JavaScript files (checked by reading the files; nothing is run) and Python programs using "
                   "the standard library (checked by running their tests: needs the sandbox, WSL2 or the "
                   "owner's \"Run on my PC\" approval). They cannot install or run WordPress, PHP, database "
                   "servers, npm packages or frameworks (React, Node), create accounts on hosted platforms "
                   "(Shopify, Wix), take real payments, publish anything online, or build native phone apps.")

# (pattern, note for websites, note for Python programs or None to reuse the website note)
_RULES = [
    (r"\bword ?press\b|\bwoo ?commerce\b|\bwp[- ]admin\b",
     "WordPress can't be built or run here: it needs PHP and a MySQL database, which the agents can't install. "
     "They will build a static website instead (same pages, catalogue and design, in HTML, CSS and JavaScript). "
     "Real WordPress needs a later step with Docker or WSL2.", None),
    (r"\bphp\b|\blaravel\b|\bsymfony\b|\bdrupal\b|\bjoomla\b",
     "PHP isn't available to the agents: they write HTML, CSS and JavaScript (websites) or Python.", None),
    (r"\bmysql\b|\bpostgres(?:ql)?\b|\bmongo(?:db)?\b|\bmariadb\b|\bdatabase\b|\bbase de donn[ée]es\b|\bbdd\b",
     "No database server here: a website keeps its data in the page (for example a product list in JavaScript).",
     "No database server here: a Python program can store data in SQLite files instead."),
    (r"\bshopify\b|\bwix\b|\bsquarespace\b|\bwebflow\b|\bprestashop\b|\bmagento\b",
     "The agents can't create or change sites on hosted platforms; they make files on this computer.", None),
    (r"\bstripe\b|\bpaypal\b|\breal payments?\b|\bpay(?:ment)? online\b|\bcredit card\b|\bcarte bancaire\b"
     r"|\bpaiement en ligne\b|\bcheckout\b|\bpaiement\b",
     "No real payments: a website can show a cart and an order summary, but no money is taken.", None),
    (r"\breact\b|\bvue(?:\.js)?\b|\bangular\b|\bnext\.?js\b|\bnode(?:\.js)?\b|\bnpm\b|\btailwind\b|\bbootstrap\b",
     "No frameworks or npm packages: plain HTML, CSS and JavaScript only.",
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


def scope_notes(objective: str, profile: str = "static_web") -> List[str]:
    """Plain-language limits that apply to this objective (empty when nothing stands out)."""
    text = objective or ""
    notes: List[str] = []
    for rx, web, py in _COMPILED:
        if _asked_for(rx, text):
            note = web if profile == "static_web" or py is None else py
            if note not in notes:
                notes.append(note)
    return notes
