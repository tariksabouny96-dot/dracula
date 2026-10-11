"""Step-up confirmation for sensitive decisions made during (or because of) a voice call.

Spoken words never authorise anything. A decision is applied only after the owner's registered device
signs a one-time challenge with its biometric-protected key (Secure Enclave on iOS). The challenge binds
the decision, the exact thing being decided (a hash of the approval or the mission plan) and the device,
and expires after 60 seconds. The decision is then applied through Hood's normal functions
(``resolve_approval`` / ``approve_plan``), so every existing rule still applies.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any, Callable, Dict, List, Optional

from .config import VoiceCallConfig
from .contracts import Conflict, Forbidden, NotFound, VoiceError
from .devices import DeviceRegistry
from .store import VoiceStore
from .tokens import load_p256_public_key, verify_p256

CHALLENGE_TTL_SECONDS = 60
# Only these kinds of approval can be decided from the phone (an ALLOWLIST: anything new, unknown or sensitive —
# X activation, money, self-modification, desktop input, security changes — stays in the console until someone
# deliberately adds it here). Even these are refused at risk level L4/L5.
PHONE_CONFIRMABLE_ACTIONS = frozenset({"task_execution", "tool_execution", "architecture_recommendation"})
_REFUSED_RISK_LEVELS = ("L4", "L5")
# What the owner is shown is exactly what is signed, so it must fit in full: longer is decided in the console.
DISPLAY_LIMIT = 6000


def _b64decode(s: str) -> bytes:
    s = s.strip()
    return base64.b64decode(s + "=" * (-len(s) % 4)) if ("+" in s or "/" in s) else base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def signing_string(fields: Dict[str, Any]) -> str:
    """Canonical text the device signs. The app rebuilds it from the same fields."""
    return "\n".join(["hood-confirm-v1", fields["challenge_id"], fields["device_id"], fields["kind"],
                      fields["subject"], fields["decision"], fields["subject_hash"], fields["nonce"],
                      str(int(fields["expires"]))])


class ConfirmationService:
    def __init__(self, store: VoiceStore, devices: DeviceRegistry, cfg: VoiceCallConfig, *, interaction: Any = None,
                 approvals: Any = None, engine: Any = None, auth: Any = None, clock: Callable[[], float] = time.time):
        self.store, self.devices, self.cfg = store, devices, cfg
        self.interaction, self.approvals, self.engine, self.auth = interaction, approvals, engine, auth
        self.clock = clock

    # ------------------------------------------------------------------ what can be confirmed
    def _username(self, owner_id: str) -> str:
        if self.auth is not None and hasattr(self.auth, "get_user_by_id"):
            u = self.auth.get_user_by_id(owner_id)
            if u:
                return u["username"]
        return "Zak"

    @staticmethod
    def _risk(req: Any) -> str:
        return str(getattr(req.risk_level, "value", req.risk_level))

    @staticmethod
    def _approval_display(req: Any) -> str:
        return f"Target: {req.target}\nReason: {req.reason}"

    @staticmethod
    def _plan_display(status: Dict[str, Any]) -> str:
        """The whole plan the owner is asked to approve, in plain text (objective, summary, deliverable, tasks)."""
        plan = status.get("plan") or {}
        lines = [f"Objective: {status.get('objective') or ''}", f"Budget: ${float(status['budget_usd']):.2f}"]
        if plan.get("summary"):
            lines.append(f"Summary: {plan['summary']}")
        if plan.get("deliverable"):
            lines.append(f"Deliverable: {plan['deliverable']}")
        if plan.get("interface_contract"):
            lines.append(f"Interface: {plan['interface_contract']}")
        for i, t in enumerate(plan.get("tasks") or [], 1):
            lines.append(f"{i}. [{t.get('role')}] {t.get('title')}: {t.get('instructions')}")
        return "\n".join(lines)

    @staticmethod
    def _digest(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def _approval_hash(self, req: Any, display: str) -> str:
        blob = json.dumps({"id": req.approval_id, "action": req.action_type, "target": req.target,
                           "reason": req.reason, "risk": self._risk(req), "shown": self._digest(display)},
                          sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _plan_hash(self, status: Dict[str, Any], display: str) -> str:
        return self._digest(str(status["plan_sha256"]), str(status["budget_usd"]), self._digest(display))

    def _phone_allowed(self, action_type: str, risk: str = "") -> bool:
        """Allowlist, not denylist: only known-safe kinds, and never at risk L4/L5."""
        return (action_type or "").lower() in PHONE_CONFIRMABLE_ACTIONS and risk not in _REFUSED_RISK_LEVELS

    def _approval_confirmable(self, req: Any) -> bool:
        return self._phone_allowed(req.action_type, self._risk(req)) and len(self._approval_display(req)) <= DISPLAY_LIMIT

    def pending(self, owner_id: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if self.approvals is not None:
            for req in self.approvals.list_pending():
                confirmable = self._approval_confirmable(req)
                shown = self._approval_display(req)
                items.append({"kind": "approval", "subject": req.approval_id, "title": req.action_type,
                              "detail": shown if len(shown) <= DISPLAY_LIMIT else shown[:300] + "…  (too long to review on the phone)",
                              "risk": self._risk(req), "phone_confirmable": confirmable})
        if self.engine is not None:
            for m in self.engine.list(owner_id):
                if m["state"] != "AWAITING_PLAN_APPROVAL":
                    continue
                try:
                    shown = self._plan_display(self.engine.status(owner_id, m["id"]))
                except Exception:
                    continue
                fits = len(shown) <= DISPLAY_LIMIT
                items.append({"kind": "mission_plan", "subject": m["id"], "title": "Mission plan",
                              "detail": shown if fits else shown[:300] + "…  (too long to review on the phone)",
                              "risk": "mission", "phone_confirmable": fits})
        return items

    # ------------------------------------------------------------------ challenge
    def _subject_state(self, owner_id: str, kind: str, subject: str) -> Dict[str, Any]:
        if kind == "approval":
            req = self.approvals.get_request(subject) if self.approvals is not None else None
            if req is None:
                raise NotFound("Approval not found")
            if req.status.value != "PENDING":
                raise Conflict(f"Approval is already {req.status.value}")
            if not self._phone_allowed(req.action_type, self._risk(req)):
                raise Forbidden("This kind of decision can only be made in the Hood console, not from the phone")
            shown = self._approval_display(req)
            if len(shown) > DISPLAY_LIMIT:
                raise Forbidden("This request is too long to review on the phone; decide it in the Hood console")
            return {"hash": self._approval_hash(req, shown), "title": req.action_type, "detail": shown}
        if kind == "mission_plan":
            if self.engine is None:
                raise NotFound("Missions are not available")
            try:
                st = self.engine.status(owner_id, subject)
            except KeyError:
                raise NotFound("Mission not found") from None
            if st.get("state") != "AWAITING_PLAN_APPROVAL":
                raise Conflict(f"Mission is {st.get('state')}, not awaiting plan approval")
            shown = self._plan_display(st)
            if len(shown) > DISPLAY_LIMIT:
                raise Forbidden("This plan is too long to review on the phone; decide it in the Hood console")
            return {"hash": self._plan_hash(st, shown), "title": "Approve mission plan", "detail": shown,
                    "plan_sha256": st["plan_sha256"]}
        raise VoiceError("kind must be approval or mission_plan")

    def issue_challenge(self, owner_id: str, device: Dict[str, Any], kind: str, subject: str, decision: str) -> Dict[str, Any]:
        if decision not in ("approve", "reject"):
            raise VoiceError("decision must be approve or reject")
        if device["owner_id"] != owner_id or device.get("revoked_at"):
            raise Forbidden("Device is not authorised")
        state = self._subject_state(owner_id, kind, subject)
        now = self.clock()
        fields = {"challenge_id": "chl_" + secrets.token_hex(12), "device_id": device["device_id"], "kind": kind,
                  "subject": subject, "decision": decision, "subject_hash": state["hash"],
                  "nonce": secrets.token_hex(16), "expires": now + CHALLENGE_TTL_SECONDS}
        with self.store._db() as db:
            db.execute("INSERT INTO challenges (challenge_id, device_id, kind, subject, decision, subject_hash, nonce, "
                       "created, expires, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (fields["challenge_id"], fields["device_id"], kind, subject, decision, state["hash"],
                        fields["nonce"], now, fields["expires"], json.dumps({"title": state["title"]})))
        return {**fields, "signing_string": signing_string(fields), "title": state["title"], "detail": state["detail"],
                "algorithm": "ECDSA-P256-SHA256"}

    # ------------------------------------------------------------------ verify and apply
    def submit(self, owner_id: str, device: Dict[str, Any], challenge_id: str, signature_b64: str) -> Dict[str, Any]:
        now = self.clock()
        with self.store._db() as db:
            row = db.execute("SELECT * FROM challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
            if row is None or row["device_id"] != device["device_id"]:
                raise NotFound("Unknown challenge")
            if row["used"]:
                raise Conflict("Challenge already used")
            if now > row["expires"]:
                raise Conflict("Challenge expired; ask again")
            fields = {"challenge_id": row["challenge_id"], "device_id": row["device_id"], "kind": row["kind"],
                      "subject": row["subject"], "decision": row["decision"], "subject_hash": row["subject_hash"],
                      "nonce": row["nonce"], "expires": row["expires"]}
            try:
                pub = load_p256_public_key(_b64decode(device["public_key_b64"]))
                ok = verify_p256(pub, signing_string(fields).encode("utf-8"), _b64decode(signature_b64))
            except Exception:
                ok = False
            if not ok:
                raise Forbidden("Signature not valid for this device")
            # single use, even if applying below fails
            cur = db.execute("UPDATE challenges SET used=1 WHERE challenge_id=? AND used=0", (challenge_id,))
            if cur.rowcount != 1:
                raise Conflict("Challenge already used")
        state = self._subject_state(owner_id, row["kind"], row["subject"])
        if state["hash"] != row["subject_hash"]:
            raise Conflict("This decision changed after you were asked; review it again")
        username = self._username(owner_id)
        approve = row["decision"] == "approve"
        if row["kind"] == "approval":
            res = self.interaction.resolve_approval(row["subject"], approved=approve, resolved_by=username)
            return {"applied": True, "kind": "approval", "status": res.get("status"), "decision": row["decision"]}
        if approve:
            res = self.engine.approve_plan(owner_id, row["subject"], state["plan_sha256"], approver=username)
        else:
            res = self.engine.cancel(owner_id, row["subject"], actor=username)
        return {"applied": True, "kind": "mission_plan", "decision": row["decision"],
                "state": res.get("state") if isinstance(res, dict) else None}
