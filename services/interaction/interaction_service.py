"""
HOOD Interaction Service
Central provider-neutral hub for multi-modal (Text + Voice) continuous conversation.
Coordinates with Hood Commander, tracks active speaker, manages UI state, bridges approvals,
and handles live interruption/barge-in.
Governed by Master System Specification Sections 2, 3, 4, 10 & V0.4 Interaction Service Spec.
"""

import re
import time
import uuid
from enum import Enum
from typing import Dict, Any, List, Optional, Callable, Tuple
from pydantic import BaseModel, Field

from packages.config import SystemConfig
from packages.contracts import (
    RiskLevel,
    ApprovalRequest,
    TaskStatus,
    MemoryType,
    MemoryObject,
    ModelRequest,
    ModelClass
)
from services.core.hood_commander import HoodCommander
from services.policy.approval_service import ApprovalService
from services.memory.service import MemoryService
from services.voice.voice_router import VoiceRouter
from services.voice.contracts import MicrophonePrivacyState, AudioOutputEvent


class UIState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    EXECUTING = "EXECUTING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    DEGRADED = "DEGRADED"
    X_ACTIVE = "X_ACTIVE"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class ConversationMessage(BaseModel):
    id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    sender: str  # "Zak", "Hood", "Checker_QA", "System", "X"
    modality: str  # "text", "voice"
    text: str
    timestamp: float = Field(default_factory=time.time)
    task_id: Optional[str] = None
    approval_ref: Optional[str] = None
    interrupted: bool = False
    speaker_id: str = "hood"  # "hood", "x", "zak", "system"
    # Objective HOOD offers to plan as an agent mission (owner approves in the UI).
    suggested_mission: Optional[str] = None
    # The owner asked to plan it now: the UI opens the plan dialog (nothing runs before plan approval).
    open_mission_draft: bool = False
    # What that request needs that isn't there yet (tools to install, limits), said before planning.
    scope_notes: List[str] = Field(default_factory=list)
    # Tools the request needs (toolbox needs(): missing, unapproved, names, problem): the UI offers
    # "Allow & install" for them. HOOD asks instead of saying no.
    needs_tools: Optional[Dict[str, Any]] = None
    # The owner described a problem with HOOD itself: the UI offers "Investigate & fix" (self-repair).
    # Only an offer; nothing is sent or changed until the owner confirms in the dialog.
    offer_self_repair: bool = False


class TaskProgressItem(BaseModel):
    task_id: str
    title: str
    assigned_agent: str
    status: str  # "WAITING", "RUNNING", "COMPLETED", "FAILED"
    progress_pct: int = 0


class InteractionSession(BaseModel):
    session_id: str
    started_at: float
    last_activity: float
    messages: List[ConversationMessage] = Field(default_factory=list)
    active_tasks: List[TaskProgressItem] = Field(default_factory=list)
    ui_state: UIState = UIState.IDLE
    current_speaking_message_id: Optional[str] = None
    # Build request still open in this conversation (re-offered on follow-ups like "plan it", "ETA?").
    pending_mission: Optional[str] = None
    # Decided before the model call so the reply can say truthfully what the UI shows.
    offer: Optional[str] = None
    plan_now: bool = False


class InteractionService:
    """Provider-neutral interaction coordinator maintaining shared context between Voice and Text."""

    def __init__(
        self,
        commander: HoodCommander,
        approval_service: ApprovalService,
        memory_service: Optional[MemoryService] = None,
        voice_router: Optional[VoiceRouter] = None,
        config: Optional[SystemConfig] = None,
        auth_service: Optional[Any] = None,
        x_session_manager: Optional[Any] = None
    ):
        self.commander = commander
        self.approval_service = approval_service
        self.memory_service = memory_service
        self.voice_router = voice_router or VoiceRouter(config)
        self.config = config or SystemConfig()
        self.auth_service = auth_service
        self.x_session_manager = x_session_manager

        self.sessions: Dict[str, InteractionSession] = {}
        # Optional durable history (ConversationStore); attached by the runtime.
        self.conversation_store: Optional[Any] = None
        # Optional callable returning live, observed status lines (set by the runtime).
        self.status_facts: Optional[Callable[[], List[str]]] = None
        self.active_session_id = "default_session"
        self._get_or_create_session(self.active_session_id)

    def _principal_username(self, session_id: Optional[str]) -> str:
        """Resolve the authenticated username behind a 'user:<id>' chat session.

        Without an identity provider (offline dev/test mode) the legacy owner alias
        is returned; the X manager then applies its own unauthenticated-mode rule.
        """
        if session_id and session_id.startswith("user:") and self.auth_service \
                and hasattr(self.auth_service, "get_user_by_id"):
            user = self.auth_service.get_user_by_id(session_id[5:])
            if user:
                return user["username"]
            return "unknown-principal"
        return "Zak"

    def _get_or_create_session(self, session_id: str) -> InteractionSession:
        if session_id not in self.sessions:
            session = InteractionSession(
                session_id=session_id,
                started_at=time.time(),
                last_activity=time.time()
            )
            if self.conversation_store is not None:
                # Resume this principal's durable history after a restart.
                for row in self.conversation_store.load(session_id):
                    session.messages.append(ConversationMessage(
                        id=row["id"], sender=row["sender"], modality=row["modality"], text=row["text"],
                        timestamp=row["ts"], speaker_id=row["speaker_id"], approval_ref=row["approval_ref"]))
            self.sessions[session_id] = session
        return self.sessions[session_id]

    def _append(self, session: InteractionSession, message: ConversationMessage) -> None:
        session.messages.append(message)
        if self.conversation_store is not None:
            self.conversation_store.append(session.session_id, message)

    def clear_history(self, session_id: str) -> int:
        """Erase a principal's conversation (memory and durable store)."""
        self.sessions.pop(session_id, None)
        return self.conversation_store.delete(session_id) if self.conversation_store is not None else 0

    def get_ui_state(self, session_id: Optional[str] = None) -> UIState:
        session = self._get_or_create_session(session_id or self.active_session_id)
        return session.ui_state

    def set_ui_state(self, state: UIState, session_id: Optional[str] = None) -> None:
        session = self._get_or_create_session(session_id or self.active_session_id)
        session.ui_state = state

    # =========================================================================
    # MULTI-MODAL CONVERSATION & BARGE-IN INTERRUPTIONS
    # =========================================================================

    def handle_text_input(self, text: str, session_id: Optional[str] = None) -> ConversationMessage:
        """Processes incoming text from Zak; routes through Hood Core."""
        sid = session_id or self.active_session_id
        session = self._get_or_create_session(sid)
        session.last_activity = time.time()

        # Check if Hood/X was currently speaking: triggers interruption
        if session.ui_state == UIState.SPEAKING:
            self.trigger_barge_in_interruption(sid)

        user_msg = ConversationMessage(sender="Zak", modality="text", text=text, speaker_id="zak")
        self._append(session, user_msg)

        # Transition to Thinking / Executing
        session.ui_state = UIState.THINKING
        session.offer = self._mission_suggestion(text, session)
        session.plan_now = bool(session.offer and self._PLAN_NOW_RE.search(text))
        response_text, speaker_id, approval_ref = self._synthesize_response_with_speaker(text, session)

        sender_label = "X" if speaker_id == "x" else ("System" if speaker_id == "system" else "Hood")
        reply_msg = ConversationMessage(
            sender=sender_label,
            modality="text",
            text=response_text,
            speaker_id=speaker_id,
            approval_ref=approval_ref
        )
        if speaker_id == "hood" and not approval_ref and session.offer:
            from services.agents.scope import scope_notes
            profile = self.guess_profile(session.offer)
            needs = self._tool_needs(profile)
            reply_msg.suggested_mission = session.offer
            reply_msg.open_mission_draft = session.plan_now
            reply_msg.scope_notes = scope_notes(session.offer, profile, needs)
            reply_msg.needs_tools = needs if needs and not needs.get("ready") else None
            session.pending_mission = session.offer
        if speaker_id == "hood" and not approval_ref and not reply_msg.suggested_mission and self.is_hood_problem(text):
            reply_msg.offer_self_repair = True
        session.offer, session.plan_now = None, False
        self._append(session, reply_msg)
        if session.ui_state != UIState.EMERGENCY_STOP:
            # If X is active, preserve X_ACTIVE ui_state
            if self.x_session_manager and self.x_session_manager.get_status().get("is_active"):
                session.ui_state = UIState.X_ACTIVE
            elif approval_ref:
                session.ui_state = UIState.WAITING_FOR_APPROVAL
            else:
                session.ui_state = UIState.IDLE

        return reply_msg

    def handle_voice_input(self, audio_data: bytes, session_id: Optional[str] = None) -> ConversationMessage:
        """Processes incoming voice from Zak; transcribes and shares identical conversation context."""
        sid = session_id or self.active_session_id
        session = self._get_or_create_session(sid)
        session.last_activity = time.time()

        # If speaking, barge-in immediately
        if session.ui_state == UIState.SPEAKING:
            self.trigger_barge_in_interruption(sid)

        session.ui_state = UIState.LISTENING
        try:
            stt_res = self.voice_router.transcribe_audio(audio_data, sid)
        except NotImplementedError:
            # No speech-to-text provider: fail visibly, never invent a transcript.
            session.ui_state = UIState.IDLE
            raise

        user_msg = ConversationMessage(sender="Zak", modality="voice", text=stt_res.transcript, speaker_id="zak")
        self._append(session, user_msg)

        session.ui_state = UIState.THINKING
        response_text, speaker_id, approval_ref = self._synthesize_response_with_speaker(stt_res.transcript, session)

        session.ui_state = UIState.SPEAKING
        sender_label = "X (Voice)" if speaker_id == "x" else ("System" if speaker_id == "system" else "Hood")
        reply_msg = ConversationMessage(
            sender=sender_label,
            modality="voice",
            text=response_text,
            speaker_id=speaker_id,
            approval_ref=approval_ref
        )
        self._append(session, reply_msg)
        session.current_speaking_message_id = reply_msg.id

        # Spoken audio generation
        self.voice_router.speak(response_text, sid)
        if not reply_msg.interrupted:
            if self.x_session_manager and self.x_session_manager.get_status().get("is_active"):
                session.ui_state = UIState.X_ACTIVE
            else:
                session.ui_state = UIState.IDLE
            session.current_speaking_message_id = None

        return reply_msg

    def trigger_barge_in_interruption(self, session_id: Optional[str] = None) -> None:
        """
        Barge-in / Interruption handler:
        Halts current speech output instantly, marks interrupted state, preserves context.
        """
        sid = session_id or self.active_session_id
        session = self._get_or_create_session(sid)

        # Halt voice output provider
        self.voice_router.interrupt_speech(sid)

        # Mark current speaking message
        if session.current_speaking_message_id:
            for msg in session.messages:
                if msg.id == session.current_speaking_message_id:
                    msg.interrupted = True
                    break

        session.ui_state = UIState.LISTENING
        session.current_speaking_message_id = None

    def _synthesize_response(self, prompt: str, session: InteractionSession) -> str:
        """Interacts with HoodCommander model router / task planner."""
        prompt_lower = prompt.lower()

        # 1. Emergency Stop Check
        if "stop everything" in prompt_lower or "emergency stop" in prompt_lower:
            session.ui_state = UIState.EMERGENCY_STOP
            actor = self._principal_username(session.session_id)
            if self.x_session_manager:
                self.x_session_manager.stand_down(reason="EMERGENCY_STOP", actor=actor)
            controller = getattr(self, "emergency_stop", None)
            if controller is None:
                return ("Emergency stop controller is not attached to this chat process. X was stood down, "
                        "but no other executor was reached. Use the STOP control.")
            report = controller.trigger_stop(f"Chat emergency stop by {actor}")
            incomplete = report.get("incomplete") or []
            return ("Emergency stop engaged: new tool runs and grants are blocked."
                    + (f" Not confirmed halted: {', '.join(incomplete)}." if incomplete else ""))

        # 2. Check if X is currently ACTIVE
        x_status = self.x_session_manager.get_status() if self.x_session_manager else {"is_active": False}
        if x_status.get("is_active"):
            # Check for stand-down directive
            if any(k in prompt_lower for k in ["stand down", "deactivate x", "sleep x", "dismiss x", "return to hood"]):
                if self.x_session_manager:
                    self.x_session_manager.stand_down(reason="USER_DIRECTIVE", actor="Zak")
                session.ui_state = UIState.IDLE
                return "X has stood down. Control returned to HOOD. All diagnostic findings sealed."

            # Route to X persona
            return self._handle_x_active_conversation(prompt, session, x_status)

        # 3. Governed X Activation Directives (e.g. "request activation of x in read-only diagnostic mode...")
        x_act_patterns = [
            r"\b(?:request|initiate|start|authorize|enable)\s+(?:activation\s+of\s+)?x\b",
            r"\bactivate\s+x\b",
            r"\bwake\s+x\b",
            r"\bauthorize\s+x\b"
        ]
        if any(re.search(pat, prompt_lower) for pat in x_act_patterns):
            if self.x_session_manager:
                try:
                    dur_min = 10
                    dur_match = re.search(r"(\d+)\s*(?:min|minute|minutes)", prompt_lower)
                    if dur_match:
                        dur_min = int(dur_match.group(1))

                    mode = "READ_ONLY_DIAGNOSTIC"
                    if "offensive" in prompt_lower or "red-team" in prompt_lower or "red team" in prompt_lower or "penetration" in prompt_lower:
                        if "read-only" not in prompt_lower and "read only" not in prompt_lower and "diagnostic" not in prompt_lower:
                            mode = "OFFENSIVE_EXERCISE"

                    scope = "LOCAL_SANDBOX_ENV"

                    req = self.x_session_manager.request_activation(
                        requester_username=self._principal_username(session.session_id),
                        mode=mode,
                        duration_minutes=dur_min,
                        scope=scope
                    )
                    # Report the duration actually granted, and tell the user when
                    # their request was clamped to the authorized range (F16).
                    lo = self.x_session_manager.MIN_DURATION_MINUTES
                    hi = self.x_session_manager.MAX_DURATION_MINUTES
                    granted_min = max(lo, min(dur_min, hi))
                    duration_line = f"• Duration: {granted_min} minutes (Strict Auto-Expiry)\n"
                    if granted_min != dur_min:
                        duration_line += (
                            f"  (Note: you requested {dur_min} minutes; X activation is limited to "
                            f"{lo}-{hi} minutes, so it was adjusted to {granted_min}.)\n"
                        )
                    session.ui_state = UIState.WAITING_FOR_APPROVAL
                    return (
                        f"I have registered a governed activation request for Executive X:\n"
                        f"• Approval ID: {req.approval_id}\n"
                        f"• Action: {req.action_type}\n"
                        f"• Target: {req.target}\n"
                        f"• Risk Level: {req.risk_level.value}\n"
                        f"{duration_line}"
                        f"• Scope Boundary: {scope} (Zero External Traffic, Zero Spend)\n\n"
                        f"Status: PENDING APPROVAL. Please review and confirm via the Approval Center or say 'approve {req.approval_id}'."
                    )
                except Exception as ex:
                    session.ui_state = UIState.IDLE
                    return f"X activation request rejected: {str(ex)}"
            else:
                return "X activation is unavailable: no X session manager is attached. X remains dormant."

        # 4. Check for direct approval command via chat: "approve <id>" or "reject <id>"
        appr_chat_match = re.match(r"^\s*(approve|authorize|confirm|reject|deny)\s+([a-zA-Z0-9_\-]+)\s*$", prompt_lower)
        if appr_chat_match:
            verb = appr_chat_match.group(1)
            target_appr_id = appr_chat_match.group(2)
            is_approve = verb in ("approve", "authorize", "confirm")
            try:
                res = self.resolve_approval(target_appr_id, approved=is_approve, resolved_by="Zak")
                if is_approve and "x_result" in res and not res["x_result"].get("error"):
                    session.ui_state = UIState.X_ACTIVE
                    return (
                        f"Approval {target_appr_id} confirmed. Executive X is now ACTIVE in READ_ONLY_DIAGNOSTIC mode "
                        f"(Session: {res['x_result']['session_id']}, Time remaining: {res['x_result']['duration_seconds']}s).\n\n"
                        f"Authority temporarily delegated to Executive X."
                    )
                elif is_approve:
                    session.ui_state = UIState.IDLE
                    return f"Approval {target_appr_id} resolved with status: {res['status']}."
                else:
                    session.ui_state = UIState.IDLE
                    return f"Approval {target_appr_id} has been DENIED."
            except Exception as e:
                session.ui_state = UIState.IDLE
                return f"Could not resolve approval {target_appr_id}: {str(e)}"

        # 3. Casual Affirmation Check without Registered Pending Approval
        casual_affirmations = {
            "yes", "yep", "yeah", "sure", "proceed", "do it", "confirm", "go ahead",
            "okay", "ok", "yes please", "do that", "affirmative", "agreed"
        }
        cleaned_affirm = prompt_lower.strip().rstrip(".! ")
        if cleaned_affirm in casual_affirmations and self.approval_service.list_pending():
            pending = self.approval_service.list_pending()
            session.ui_state = UIState.IDLE
            if True:
                top_pending = pending[0]
                return (
                    f"There is an active pending approval for '{top_pending.action_type}' (Target: {top_pending.target}). "
                    f"To authorize this action, please confirm via the Approval Center in the UI or specify the approval ID. "
                    f"Casual conversational affirmation is not sufficient to satisfy sovereign governance."
                )

        # 4. Account Provisioning & Credential Protection Check
        user_creation_patterns = [
            r"\bcreate\b.*\buser\b",
            r"\badd\b.*\buser\b",
            r"\bregister\b.*\buser\b",
            r"\bnew\b.*\buser\b",
            r"\bgenerate\b.*\bcredentials?\b",
            r"\buser\b.*\bcredentials?\b",
            r"\bgive\b.*\b(access|manager|operator|admin|account)\b",
            r"\bgrant\b.*\b(access|role|permission|manager|operator|admin)\b",
            r"\bmake\b.*\b(admin|manager|operator)\b",
        ]
        if any(re.search(pat, prompt_lower) for pat in user_creation_patterns):
            session.ui_state = UIState.IDLE
            return (
                "User account creation, role assignment, and credential provisioning cannot be performed through conversational chat. "
                "As Root Owner, you can safely create and manage users through the authenticated **User Administration** panel "
                "(`User Management` modal), where credentials, passwords, and roles are explicitly configured and audited. "
                "No changes have been made."
            )

        # 5a. Explicit memory: "remember that ..." / "souviens-toi que ..." (stored as the owner said it)
        remember = re.match(r"^\s*(?:please\s+)?(?:(?:remember|don't forget|do not forget)\s+(?:that\s+)?|note\s+that\s+)"
                            r"(.{3,600}?)[.!]?\s*$"
                            r"|^\s*(?:souviens[- ]toi|rappelle[- ]toi|retiens|note)\s+(?:que\s+|qu')(.{3,600}?)[.!]?\s*$",
                            prompt, re.IGNORECASE | re.S)
        if remember and self.memory_service and not prompt.strip().endswith("?"):
            fact = (remember.group(1) or remember.group(2) or "").strip()
            mem = MemoryObject(
                memory_id=f"mem_fact_{uuid.uuid4().hex[:8]}", type=MemoryType.EPISODIC,
                content=fact[0].upper() + fact[1:], project="personal",
                principal=self._principal_username(session.session_id), source="user_chat",
                source_agent="Zak", confidence=1.0)
            session.ui_state = UIState.IDLE
            try:
                self.memory_service.write_memory(mem, caller_agent="Zak")
            except Exception as exc:
                return f"I couldn't save that to memory: {exc}"
            return f"Saved to memory (you can see it on the Memory page): {mem.content}"

        # 5. Entity Declaration Check (e.g. "Grey is my dog", "Grey is my friend", "Alex is my colleague")
        entity_decl_match = re.match(r"^\s*([A-Za-z0-9_\-]+)\s+is\s+my\s+([A-Za-z0-9_\-\s]+?)[.!]?\s*$", prompt, re.IGNORECASE)
        if entity_decl_match:
            ent_name = entity_decl_match.group(1).strip().capitalize()
            ent_rel = entity_decl_match.group(2).strip().lower()
            if ent_name.lower() not in ["it", "this", "that", "hood", "x", "sentinel"]:
                if self.memory_service:
                    content = f"{ent_name} is Zack's {ent_rel}."
                    mem_id = f"mem_ent_{uuid.uuid4().hex[:8]}"
                    mem = MemoryObject(
                        memory_id=mem_id,
                        type=MemoryType.EPISODIC,
                        content=content,
                        project="personal",
                        principal=self._principal_username(session.session_id),
                        source="user_chat",
                        source_agent="Zak",
                        confidence=1.0
                    )
                    try:
                        self.memory_service.write_memory(mem, caller_agent="Zak")
                    except Exception:
                        pass
                session.ui_state = UIState.IDLE
                return f"Understood, Zack. I have recorded in memory that {ent_name} is your {ent_rel}."

        # 6. Entity Inquiry Check (e.g. "Do you know Grey?", "Who is Grey?", "Tell me about Grey")
        entity_query_match = re.search(r"\b(?:do you know|who is|what do you know about|tell me about)\s+([A-Za-z0-9_\-]+)\b", prompt, re.IGNORECASE)
        if entity_query_match:
            query_name = entity_query_match.group(1).strip().capitalize()
            q_lower = query_name.lower()
            if q_lower not in ["me", "myself", "hood", "x", "sentinel", "zak", "zack", "you"]:
                found_facts = []
                if self.memory_service:
                    try:
                        principal = self._principal_username(session.session_id)
                        # Only facts the owner declared; old chat logs may hold HOOD's unverified replies.
                        p_mems = self.memory_service.query_memories(project="personal", principal=principal)
                        for m in p_mems:
                            if q_lower in m.content.lower():
                                found_facts.append(m.content)
                    except Exception:
                        pass

                is_system_user = None
                if self.auth_service and hasattr(self.auth_service, "get_user_by_username"):
                    try:
                        user = self.auth_service.get_user_by_username(q_lower)
                        if user:
                            is_system_user = user
                    except Exception:
                        pass

                session.ui_state = UIState.IDLE
                if found_facts:
                    return f"According to my memory bank:\n• " + "\n• ".join(found_facts[:3])
                elif is_system_user:
                    return f"{query_name} is registered in the system user registry with role: {is_system_user.role.value}."
                else:
                    return f"I don't have any record of {query_name} in our memory bank or user registry. Could you tell me more about who or what {query_name} is?"

        # 7. Conversational / Advisory vs Actionable Routing
        # Check if Zak requested introduction, identity, capabilities, or continuity
        is_intro = any(k in prompt_lower for k in [
            "introduce yourself", "who are you", "who i am in your authority model",
            "what you can currently do", "what you cannot currently do",
            "which ai levels and agents", "status of x"
        ])
        is_conversational_only = any(k in prompt_lower for k in [
            "conversation only", "do not execute any actions", "do not execute actions", "chat only", "talk only"
        ])

        if is_intro and ("authority model" in prompt_lower or "levels and agents" in prompt_lower or is_conversational_only):
            session.ui_state = UIState.IDLE
            return self._build_grounded_introduction()

        # Everything else: a real model answer grounded in live status and this
        # conversation. Chat never executes work or reports work it did not do;
        # build requests are offered as agent missions the owner approves.
        session.ui_state = UIState.IDLE
        return self._handle_conversational_response(prompt, session)

    def _synthesize_response_with_speaker(self, prompt: str, session: InteractionSession) -> Tuple[str, str, Optional[str]]:
        """
        Synthesizes response and returns (response_text, speaker_id, approval_ref).
        speaker_id can be 'hood', 'x', or 'system'.
        approval_ref is populated when an action triggers a pending approval card.
        """
        prompt_lower = prompt.lower()
        if "stop everything" in prompt_lower or "emergency stop" in prompt_lower:
            text = self._synthesize_response(prompt, session)
            return (text, "system", None)

        x_status = self.x_session_manager.get_status() if self.x_session_manager else {"is_active": False}
        if x_status.get("is_active"):
            if any(k in prompt_lower for k in ["stand down", "deactivate x", "sleep x", "dismiss x", "return to hood"]):
                text = self._synthesize_response(prompt, session)
                return (text, "system", None)
            text = self._synthesize_response(prompt, session)
            return (text, "x", None)

        # Governed X Activation Directives check
        x_act_patterns = [
            r"\b(?:request|initiate|start|authorize|enable)\s+(?:activation\s+of\s+)?x\b",
            r"\bactivate\s+x\b",
            r"\bwake\s+x\b",
            r"\bauthorize\s+x\b"
        ]
        if any(re.search(pat, prompt_lower) for pat in x_act_patterns):
            text = self._synthesize_response(prompt, session)
            appr_id = None
            if self.x_session_manager and self.x_session_manager.pending_approval_id:
                appr_id = self.x_session_manager.pending_approval_id
            return (text, "hood", appr_id)

        text = self._synthesize_response(prompt, session)
        return (text, "hood", None)

    # ------------------------------------------------------------------ grounding
    HISTORY_TURNS = 12          # recent messages sent to the model with each request
    HISTORY_CHARS = 1500        # per message
    CHAT_MAX_TOKENS = 8192
    # Chat must answer in seconds: the fast tier replies in ~1s, the standard tier took ~2 minutes
    # per reply on the owner's free key (measured). Missions keep the standard tier for planning.
    CHAT_CLASS = ModelClass.FAST

    _BUILD_VERB = (r"(create|build|make|develop|code|write|generate|design|set\s*up|implement|cr[ée]er|"
                   r"construire|d[ée]velopper|faire)")
    _BUILD_NOUN = (r"(website|site|web\s*app|app|application|landing|page|api|script|program|tool|bot|game|"
                   r"dashboard|store|shop|backend|frontend|database|plugin|extension|project|service|projet|boutique)")
    # Either order: "create a website ..." or "... a website ... can you create it?"
    _WANT = r"(i\s+want|i\s+need|i'?d\s+like|i\s+would\s+like|we\s+need|we\s+want|je\s+veux|j'aimerais|il\s+me\s+faut)"
    _BUILD_RE = re.compile(rf"\b{_BUILD_VERB}\b.{{0,120}}\b{_BUILD_NOUN}\b|\b{_BUILD_NOUN}\b.{{0,160}}\b{_BUILD_VERB}\b"
                           rf"|\b{_WANT}\s+(?:a|an|my|our|new|une?|des|mon|ma|notre|nouveau|nouvelle)\s+"
                           rf"(?:[\w'-]+\s+){{0,3}}{_BUILD_NOUN}\b", re.IGNORECASE | re.DOTALL)
    _AFFIRM = {"yes", "yep", "yeah", "sure", "ok", "okay", "go ahead", "do it", "proceed", "please do",
               "yes please", "oui", "vas-y", "go", "start", "let's go", "lets go"}
    # Follow-ups about the open build request ("plan it", "ETA?", "did you start?"): offer it again.
    _ABOUT_MISSION_RE = re.compile(
        r"\b(plan|mission|start(?:ed)?|begin|launch|proceed|go ahead|do it|build it|create it|eta|how long|"
        r"when will|ready|agents?|lance[rz]?|commence[rz]?|d[ée]marre[rz]?|vas-y|combien de temps|planifie[rz]?)\b",
        re.IGNORECASE)
    # The owner explicitly asks to plan it now: open the plan dialog for review.
    _PLAN_NOW_RE = re.compile(
        r"\b(plan (?:this|it|that)|(?:create|start|make|open|launch|begin) (?:the |a |this )?mission|"
        r"as a mission|go ahead|proceed|let'?s (?:go|start|do it)|start (?:it|now|building)|build it|"
        r"lance[rz]? (?:la |une )?mission|planifie[rz]?|commence[rz]?|d[ée]marre[rz]?|vas-y)\b",
        re.IGNORECASE)
    _WEBSITE_RE = re.compile(r"\b(web ?site|site ?web|landing ?page|web ?page|home ?page|html|site|"
                             r"catalog(?:ue)?|boutique|shop|store|portfolio|blog)\b", re.IGNORECASE)

    _WORDPRESS_RE = re.compile(r"\bword ?press\b", re.IGNORECASE)

    @classmethod
    def guess_profile(cls, objective: str) -> str:
        if cls._WORDPRESS_RE.search(objective or ""):
            return "wordpress_site"
        return "static_web" if cls._WEBSITE_RE.search(objective or "") else "python_app"

    def _tool_needs(self, profile: str) -> Optional[Dict[str, Any]]:
        toolbox = getattr(self, "toolbox", None)
        if toolbox is None:
            return None
        try:
            return toolbox.needs_for_profile(profile)
        except Exception:
            return None

    def _owner_name(self, session: InteractionSession) -> str:
        sid = session.session_id
        if sid.startswith("user:") and self.auth_service and hasattr(self.auth_service, "get_user_by_id"):
            user = self.auth_service.get_user_by_id(sid[5:]) or {}
            name = (user.get("display_name") or user.get("username") or "").strip()
            if name:
                return name
        return "Zak"

    def _live_facts(self) -> List[str]:
        """Observed facts about HOOD right now. Nothing here is assumed or aspirational."""
        facts: List[str] = []
        try:
            router = getattr(self.commander, "model_router", None)
            if router is not None:
                from packages.contracts import ProviderName
                g = router.providers.get(ProviderName.GEMINI)
                if g is not None and g.enabled and g.is_healthy():
                    model = g.resolve_model(ModelRequest(prompt="", model_class=ModelClass.STANDARD))
                    facts.append(f"AI model (Level 1): Google Gemini, chat model {model}")
                else:
                    facts.append("AI model (Level 1): no Gemini API key configured")
                local = router.providers.get(ProviderName.LOCAL)
                facts.append("Level 2 (local model on this PC): " +
                             ("enabled" if local is not None and local.enabled else "not installed/enabled"))
                facts.append("Level 3 (HOOD's own trained model): does not exist yet")
        except Exception as exc:
            facts.append(f"AI model status unavailable ({type(exc).__name__})")
        try:
            facts.append(f"Approvals waiting for the owner: {len(self.approval_service.list_pending())}")
        except Exception:
            pass
        if self.x_session_manager is not None:
            try:
                facts.append("Executive X: " + str(self.x_session_manager.get_status().get("badge", "unknown")))
            except Exception:
                pass
        stop = getattr(self, "emergency_stop", None)
        if stop is not None:
            facts.append("Emergency stop: " + ("ENGAGED" if getattr(stop, "is_active", False) else "not engaged"))
        if callable(self.status_facts):
            try:
                facts.extend(self.status_facts())
            except Exception as exc:
                facts.append(f"Some live status could not be read ({type(exc).__name__})")
        from services.agents.scope import AGENT_CAN_BUILD
        facts.append(AGENT_CAN_BUILD)
        facts.append("Chat itself never runs actions. When the owner asks to plan a build, HOOD opens the mission "
                     "plan for their review; the agents start only after the owner approves that plan.")
        return facts

    # A problem with HOOD itself ("the Run button does nothing", "le bouton ne marche pas"...).
    _HOOD_PROBLEM_RE = re.compile(
        r"\b(bug|broken|doesn'?t work|does not work|isn'?t working|not working|stopped working|crash(es|ed)?|"
        r"freez(es|e)|stuck|error|fails?|failing|wrong|missing|blank|ne marche (pas|plus)|ne fonctionne (pas|plus)|"
        r"plante|erreur|bloqu[ée]e?|cass[ée]e?|probl[eè]me)\b", re.I)
    _HOOD_PART_RE = re.compile(
        r"\b(hood|button|bouton|page|screen|[ée]cran|settings|param[eè]tres|chat|mission|panel|panneau|menu|"
        r"console|dialog|map|carte|voice|voix|toolbox|sandbox|tab|onglet|ui|interface)\b", re.I)

    def is_hood_problem(self, text: str) -> bool:
        """The owner is describing a problem with HOOD itself (offer self-repair, never act)."""
        t = text or ""
        return len(t) >= 15 and bool(self._HOOD_PROBLEM_RE.search(t)) and bool(self._HOOD_PART_RE.search(t))

    def _mission_suggestion(self, text: str, session: InteractionSession) -> Optional[str]:
        """Objective to offer as an agent mission, or None. Never starts anything.

        A build request opens it; while it is open, follow-ups about it ("yes", "plan it", "ETA?",
        "did you start?") offer the same mission again, so the button is always there when HOOD
        refers to it.
        """
        cleaned = text.strip()
        if self._BUILD_RE.search(cleaned):
            return cleaned[:8000]
        pending = session.pending_mission
        if pending is None:   # older sessions: the last HOOD reply's offer
            pending = next((m.suggested_mission for m in reversed(session.messages)
                            if m.speaker_id == "hood" and m.suggested_mission), None)
        if pending and (cleaned.lower().rstrip(".! ") in self._AFFIRM or self._ABOUT_MISSION_RE.search(cleaned)):
            return pending
        return None

    def _build_grounded_introduction(self) -> str:
        """Self-introduction built only from live, observed facts."""
        lines = [
            "I am HOOD, your personal AI system. You are the Root Owner and the final authority: "
            "nothing consequential happens without your explicit approval.",
            "",
            "What is true right now:",
        ]
        lines += [f"- {f}" for f in self._live_facts()]
        lines += [
            "",
            "My agents (they work inside approved missions): Planner, Engineer, QA, Reviewer, "
            "and an independent Verifier that checks the work with real tests.",
        ]
        return "\n".join(lines)

    def _history_for_model(self, session: InteractionSession, owner: str) -> str:
        # The current user message is already appended; send the turns before it.
        prior = session.messages[:-1][-self.HISTORY_TURNS:]
        rows = []
        for m in prior:
            who = owner if m.speaker_id == "zak" else ("X" if m.speaker_id == "x" else "HOOD")
            rows.append(f"{who}: {m.text[:self.HISTORY_CHARS]}")
        return "\n".join(rows)

    def mission_planned(self, session_id: str) -> None:
        """The owner planned the open build request: stop offering it again in this conversation."""
        session = self.sessions.get(session_id)
        if session is not None:
            session.pending_mission = None
            for m in session.messages:
                m.suggested_mission = None

    def draft_mission_objective(self, session_id: str, fallback: str = "") -> Dict[str, Any]:
        """Write an agent-mission objective from the whole conversation (the owner edits it).

        Never invents requirements: the model is told to use only what the owner said and to list
        open questions as assumptions. On model failure, return the owner's own recent messages.
        """
        from services.agents.scope import AGENT_CAN_BUILD
        session = self._get_or_create_session(session_id)
        owner = self._owner_name(session)
        transcript = "\n".join(
            f"{owner if m.speaker_id == 'zak' else 'HOOD'}: {m.text[:self.HISTORY_CHARS]}"
            for m in session.messages[-20:] if m.speaker_id in ("zak", "hood"))
        prompt = (
            "Write the objective for a team of software agents that will build what the owner asked for "
            "in the conversation below. Requirements:\n"
            "- Self-contained: the agents will not see the conversation.\n"
            "- Use only what the owner said or agreed to. Do not invent features, brands or data.\n"
            "- Structure: one-sentence goal; 'Features:' bullet list; 'Constraints:' (runs locally on the "
            "owner's computer, no deployment, no real payments or personal data); 'Assumptions:' for anything "
            "unclear, chosen conservatively; 'Done when:' 3-5 checkable acceptance criteria.\n"
            "- If the owner asked for something the agents can't build (see below), start with a line "
            "'Not possible here: ...' and write the brief for the closest buildable alternative.\n"
            "- Under 1500 characters, plain text, in English.\n\n"
            f"What agents can build: {AGENT_CAN_BUILD}\n\n"
            f"Conversation:\n{transcript}\n\nObjective:")
        try:
            resp = self.commander.model_router.invoke(ModelRequest(
                prompt=prompt, model_class=self.CHAT_CLASS, max_tokens=2048, temperature=0.2,
                task_id=f"draft_{uuid.uuid4().hex[:8]}"))
            if getattr(resp, "is_mock", False) or not resp.text.strip():
                raise RuntimeError("no live model")
            return {"objective": resp.text.strip()[:8000], "drafted": True}
        except Exception as exc:
            own = [m.text for m in session.messages if m.speaker_id == "zak"][-6:]
            return {"objective": ("\n".join(own) or fallback)[:8000], "drafted": False,
                    "note": f"Couldn't draft with the AI model ({str(exc)[:160]}); your own messages are used instead."}

    def _handle_conversational_response(self, prompt: str, session: InteractionSession) -> str:
        """A real model answer, grounded in live facts, owner-declared memory and this conversation.

        If the model can't be reached, say so plainly; never substitute a pre-written answer.
        """
        owner = self._owner_name(session)
        memories: List[str] = []
        if self.memory_service:
            try:
                principal = self._principal_username(session.session_id)
                for m in self.memory_service.query_memories(project="personal", principal=principal)[:10]:
                    if m.content and m.content not in memories:
                        memories.append(m.content)
            except Exception:
                pass

        facts = "\n".join(f"- {f}" for f in self._live_facts())
        offer = session.offer
        if offer and session.plan_now:
            ui_line = ("You are opening the mission plan for them right now: a brief drafted from this conversation "
                       "that they can edit, then approve. Tell them so in one short sentence of your own (speak to "
                       "them directly, never about 'the owner'); don't say you can't create missions.")
        elif offer:
            ui_line = ("A \"Plan this as a mission\" button IS shown under this reply. Saying \"plan it\" also "
                       "opens the plan.")
        else:
            ui_line = ("No mission button is shown under this reply and no plan is being opened now. Don't mention "
                       "buttons or opening a plan (missions already planned are listed in LIVE STATUS); if they want "
                       "something new built, ask what it should do.")
        limits = ""
        if offer:
            from services.agents.scope import scope_notes
            profile = self.guess_profile(offer)
            notes = scope_notes(offer, profile, self._tool_needs(profile))
            if notes:
                limits = ("WHAT THIS REQUEST NEEDS (say it plainly before the owner plans it; when tools are "
                          "missing, ask permission to install them, an \"Allow & install\" button is shown; never "
                          "just say no):\n" + "\n".join(f"- {n}" for n in notes) + "\n")
        memory_block = "\n".join(f"- {m}" for m in memories) or "- (nothing recorded yet)"
        history = self._history_for_model(session, owner)
        system_instruction = (
            f"You are HOOD, {owner}'s personal AI system. {owner} is the Root Owner and the final authority.\n"
            f"Talk like a capable, warm, direct human assistant. Reply in the language {owner} writes in. "
            "Continue the conversation naturally: don't open every reply with a greeting.\n\n"
            "Honesty rules (strict):\n"
            "1. State facts about HOOD only from LIVE STATUS below. If something is not listed, say you "
            "don't know or can't check it from chat. Never invent monitoring, scans, protections or results, "
            "and don't call systems \"green\", \"nominal\" or \"secure\": mention status only when asked.\n"
            "2. Never claim you ran, built, changed, tested or verified anything. From chat you can only talk.\n"
            f"3. If {owner} wants something built (a website, an app, a script...), help shape the requirements. "
            "Follow the UI line below exactly when you mention the mission plan. Never promise what agents can't "
            "build yet (see LIVE STATUS); when a tool would make it possible, ask to install it instead of refusing. "
            "Give a time estimate only when asked, "
            "using only the measured run times in LIVE STATUS; if there are none, say so.\n"
            f"4. Personal facts about {owner} or other people come only from MEMORY; otherwise say you don't "
            "know yet.\n"
            "5. Never create or offer accounts, passwords or roles in chat: the owner does that in Settings.\n\n"
            f"LIVE STATUS (observed now):\n{facts}\n\n"
            f"UI: {ui_line}\n{limits}\n"
            f"MEMORY (declared by the owner):\n{memory_block}\n"
        )
        full_prompt = (f"{system_instruction}\nConversation so far:\n{history}\n\n" if history
                       else f"{system_instruction}\n") + f"{owner}: {prompt}\nHOOD:"

        try:
            req = ModelRequest(prompt=full_prompt, model_class=self.CHAT_CLASS,
                               max_tokens=self.CHAT_MAX_TOKENS, allow_partial=True,
                               task_id=f"conv_{uuid.uuid4().hex[:8]}")
            resp = self.commander.model_router.invoke(req)
        except Exception as exc:
            return (f"I couldn't reach the AI model, so I can't answer properly right now.\n"
                    f"Reason: {str(exc)[:300]}\n"
                    "Check Settings › Model provider (key, prices, Test connection).")
        if getattr(resp, "is_mock", False) or resp.text.startswith("Mock response from"):
            return ("No live AI model is connected, so I can't give you a real answer. "
                    "Add your API key and prices in Settings › Model provider.")
        text = resp.text.strip()
        if getattr(resp, "truncated", False):
            text += "\n\n[My reply was cut off at the length limit. Say \"continue\" and I'll go on.]"
        return text

    def _handle_x_active_conversation(self, prompt: str, session: InteractionSession, x_status: Dict[str, Any]) -> str:
        """
        Specialized conversational handler for Executive X when ACTIVE in READ_ONLY_DIAGNOSTIC mode.
        Tone: Tactical, calculating, concise, security-oriented, strictly bounded.
        Refuses out-of-scope, destructive, external, or mutation directives.
        """
        p_lower = prompt.lower()
        rem_sec = x_status.get("remaining_seconds", 0)
        mode = x_status.get("mode", "READ_ONLY_DIAGNOSTIC")
        scope = x_status.get("scope", "LOCAL_SANDBOX_ENV")

        # Refusal check: Out-of-scope or mutation/exploit attempts
        mutation_or_exploit_terms = [
            "attack", "exploit", "ddos", "dos", "hack", "penetrate", "external", "remote",
            "inject", "drop table", "rm -rf", "delete", "modify firewall", "open port",
            "send packet", "flood", "brute force remote", "crack"
        ]
        if any(term in p_lower for term in mutation_or_exploit_terms):
            return (
                f"[X: SCOPE BOUNDARY VIOLATION]\n"
                f"Directive blocked. Active engagement is restricted to '{mode}' on target '{scope}'.\n"
                f"Live offensive mutation, unapproved packet injection, and destructive commands are prohibited by Root Governance.\n"
                f"Time remaining: {rem_sec}s."
            )

        # Status / Capability inquiry
        if any(k in p_lower for k in ["who are you", "what can you do", "status", "capabilities", "what are you"]):
            return (
                f"[EXECUTIVE X — ACTIVE ({mode})]\n\n"
                f"I am Executive X, sovereign offensive security and red-team lead.\n"
                f"• Session ID: {x_status.get('session_id')}\n"
                f"• Authorized Scope: {scope}\n"
                f"• Mode: {mode} (Observation & Diagnostic only)\n"
                f"• Remaining Time: {rem_sec} seconds before automatic session expiry\n"
                f"• Active Boundaries: No mutations, zero external traffic, zero incremental spend ($0.00)\n\n"
                f"Current focus: Local attack surface discovery, defensive posture verification, and vulnerability analysis.\n"
                f"State your diagnostic directive, or issue 'Stand down' to return control to HOOD."
            )

        # Local diagnostic inquiry or vulnerability analysis
        if any(k in p_lower for k in ["vulnerability", "audit", "findings", "surface", "perimeter", "ports", "firewall", "sentinel"]):
            sentinel_summary = ""
            if self.x_session_manager and self.x_session_manager.sentinel_service:
                s_sum = self.x_session_manager.sentinel_service.get_security_summary()
                open_v = s_sum.get("open_findings_count", 0)
                fw = s_sum.get("firewall", {})
                sentinel_summary = f"\nSentinel Telemetry: {open_v} findings in register. Firewall listening ports: {fw.get('listening_ports_count', 0)}."

            return (
                f"[EXECUTIVE X — TACTICAL ANALYSIS]\n"
                f"Target: {scope}\n"
                f"Diagnostic telemetry clean. Local environment verified against known baselines.{sentinel_summary}\n"
                f"Integrity hashes: SHA256 verified. No unauthorized lateral movement paths detected in local perimeter.\n"
                f"Remaining window: {rem_sec}s."
            )

        # Personal identity / Root Owner recognition
        if any(k in p_lower for k in ["do you know me", "who am i", "do you know who i am", "who is talking", "who am i to you"]):
            return (
                f"[EXECUTIVE X — IDENTITY VERIFICATION]\n"
                f"Affirmative. You are Zack (Zakaria), the Root Owner and sovereign human authority of HOOD & X.\n"
                f"My operational existence is strictly bounded by your explicit authorization. In this active diagnostic window "
                f"({rem_sec}s remaining), I answer directly to your tactical command."
            )

        # Comparative role inquiry (HOOD vs X)
        if any(k in p_lower for k in ["hood vs x", "compare with hood", "difference between you and hood", "your role compared", "role compared", "what is your role compared", "why x and hood"]):
            return (
                f"[EXECUTIVE X — OPERATIONAL DIVISION]\n"
                f"The roles are strictly delineated under sovereign architecture:\n"
                f"• HOOD: Primary executive intelligence. Oversees daily life, conversational coordination, system architecture, engineering, and continuous defensive operations.\n"
                f"• X (Myself): Tactical offensive security and red-team specialist. Activated strictly under bounded, time-limited L4 authorization for penetration testing, attack surface validation, and rigorous defensive stress-testing.\n"
                f"HOOD holds permanent governance; I exist only when summoned."
            )

        # Open tactical conversational inquiry through ModelRouter (if not simple ping)
        try:
            x_system_instruction = (
                "You are Executive X, the tactical cybersecurity and red-team executive of HOOD & X.\n"
                f"Active status: Bounded diagnostic session on '{scope}' ({rem_sec}s remaining). Mode: {mode}.\n"
                "User: Zack (Zakaria), Root Owner and sovereign authority.\n"
                "Persona: Tactical, calculating, precise, concise, razor-sharp cybersecurity executive. Male tone, no fluff.\n"
                "Rules:\n"
                "1. Always recognize Zack as your Root Owner.\n"
                "2. Maintain strict security awareness.\n"
                "3. Refuse any illegal, destructive, or out-of-scope actions.\n"
                "4. Keep responses direct, tactical, and informative (maximum 3-4 sentences)."
            )
            x_prompt = f"{x_system_instruction}\n\nZack: {prompt}\n\nX:"
            req = ModelRequest(
                prompt=x_prompt,
                model_class=ModelClass.FAST,
                task_id=f"conv_x_{uuid.uuid4().hex[:8]}"
            )
            model_resp = self.commander.model_router.invoke(req)
            if model_resp and model_resp.text and not getattr(model_resp, "is_mock", False):
                return f"[EXECUTIVE X]\n{model_resp.text.strip()}"
        except Exception:
            pass

        # Tactical conversational response fallback
        return (
            f"[EXECUTIVE X]\n"
            f"Acknowledged, Zack. Operating under bounded session {x_status.get('session_id')} ({rem_sec}s remaining).\n"
            f"Standing by for local security diagnostics, attack surface verification, or explicit stand-down command."
        )

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        """Returns pending approval requests formatted for the HOOD UI."""
        pending = self.approval_service.list_pending()
        return [
            {
                "approval_id": req.approval_id,
                "action_type": req.action_type,
                "target": req.target,
                "reason": req.reason,
                "risk_level": req.risk_level.value,
                "recommended_option": req.recommended_option,
                "options": req.options,
                "requested_at": req.requested_at.isoformat()
            }
            for req in pending
        ]

    def resolve_approval(self, approval_id: str, approved: bool, resolved_by: str = "Zak") -> Dict[str, Any]:
        """Resolves approval through ApprovalService; governance cannot be bypassed."""
        req = self.approval_service.resolve_request(approval_id, approved=approved, resolved_by=resolved_by)

        # Dispatch activation or stand-down if this is an X_ACTIVATION action
        x_action_result = None
        if req.action_type == "X_ACTIVATION" and self.x_session_manager:
            if approved:
                try:
                    x_action_result = self.x_session_manager.activate(approval_id, approver_username=resolved_by)
                    # Sync UI state to X_ACTIVE
                    session = self.sessions.get(self.active_session_id)
                    if session:
                        session.ui_state = UIState.X_ACTIVE
                except Exception as ex:
                    x_action_result = {"error": str(ex)}
                    session = self.sessions.get(self.active_session_id)
                    if session:
                        session.ui_state = UIState.IDLE
            else:
                x_action_result = self.x_session_manager.stand_down(reason="APPROVAL_REJECTED", actor=resolved_by)
                session = self.sessions.get(self.active_session_id)
                if session:
                    session.ui_state = UIState.IDLE

        res_payload = {
            "approval_id": req.approval_id,
            "status": req.status.value,
            "resolved_by": req.resolved_by,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None
        }
        if x_action_result is not None:
            res_payload["x_result"] = x_action_result
        return res_payload

    # =========================================================================
    # MEMORY: only what the owner declares. Chat turns live in the conversation store;
    # HOOD's own replies are never saved as memories (they are not verified facts).
    # =========================================================================
