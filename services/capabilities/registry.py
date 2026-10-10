"""Truthful capability inventory. Presence of source code is not runtime verification."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Each item describes a designed subsystem; do not turn source existence into VERIFIED.
CAPABILITIES = (
    ("conversation", "Conversation", "Core", "services/interaction/interaction_service.py", "tab-command-deck", "Text requests and in-memory session state"),
    ("orchestrator", "Agent orchestration", "Core", "services/orchestrator/dag_scheduler.py", "tab-missions", "DAG engine; durable independent execution is incomplete"),
    ("governance", "Approvals and policy", "Governance", "services/policy/approval_service.py", "tab-missions", "Owner approval boundary; execution-level validation still required"),
    ("x", "X executive", "Governance", "services/x_control/x_session_manager.py", "tab-sentinel", "Dormant by default; restricted owner-only activation"),
    ("emergency", "Emergency stop", "Governance", "services/core/emergency_stop.py", "tab-command-deck", "Stop controllers exist; not all execution paths are integrated"),
    ("sentinel", "Security Sentinel", "Security", "services/sentinel/sentinel_service.py", "tab-sentinel", "Local inspection module; not a certification of host security"),
    ("tool_gateway", "Tool gateway", "Engineering", "services/tool_gateway/gateway.py", "tab-systems", "Restricted execution and capability grants"),
    ("development", "Development executor", "Engineering", "services/dev_executor/code_modifier.py", "tab-systems", "Checkpoint and patch primitives; unattended delivery disabled"),
    ("browser", "Browser automation", "Engineering", "services/browser/browser_service.py", "tab-systems", "Playwright code; installed browser and live access unverified"),
    ("desktop", "Desktop control", "Engineering", "services/desktop/desktop_service.py", "tab-desktop", "Requires Windows and direct device-level validation"),
    ("memory", "Persistent memory", "Data", "services/memory/service.py", "tab-memory", "SQLite primitives; end-to-end continuity and isolation incomplete"),
    ("audit", "Audit events", "Data", "services/audit/service.py", "tab-systems", "Audit recording; completeness is not independently verified"),
    ("gemini", "Gemini API", "Models", "services/model_gateway/gemini_adapter.py", "tab-intelligence", "External provider adapter; availability never assumed"),
    ("openai", "OpenAI API", "Models", "services/model_gateway/openai_adapter.py", "tab-intelligence", "Responses API adapter implemented; explicit opt-in and live validation required"),
    ("local_llm", "Local model gateway", "Models", "services/model_gateway/local_adapter.py", "tab-intelligence", "Local adapter; running inference endpoint unverified"),
    ("hood_model", "Hood proprietary model", "Models", "services/evolution/arena.py", "tab-intelligence", "Evaluation framework only; no deployed inference model"),
    ("voice", "Voice and speech", "Interface", "services/voice/adapters.py", "tab-command-deck", "Voice adapter prototypes; real microphone capture unavailable"),
    ("ecommerce", "E-commerce workflows", "Business", "services/economic/engine.py", "tab-economic", "Economic engine is not a complete storefront integration"),
    ("freelance", "Freelance delivery", "Business", None, "tab-economic", "No verified end-to-end opportunity-to-delivery workflow"),
    ("marketing", "Campaign automation", "Business", None, "tab-economic", "No connected mailing or CRM integration"),
    ("calendar", "Calendar and meetings", "Integrations", None, "tab-systems", "Not connected to a calendar service"),
    ("email", "Email operations", "Integrations", None, "tab-systems", "Not connected to a mail provider"),
    ("artifacts", "File and document exports", "Integrations", "services/exports/service.py", "tab-systems", "md/html/pdf/docx/xlsx/csv/zip export with independent validation and an owner-scoped registry (single-use download tokens)"),
    ("nodes", "Multi-node runtime", "Infrastructure", "services/nodes/manager.py", "tab-systems", "Node primitives; distributed live cluster unverified"),
    ("economics", "Cost governance", "Business", "services/model_gateway/cost_controller.py", "tab-economic", "Budget-control primitives; real expenditure not attested"),
    ("evolution", "Learning and evolution", "Models", "services/evolution/arena.py", "tab-intelligence", "Shadow evaluation, not automatic model improvement"),
)


# Live state providers registered by feature modules: id -> callable returning
# {"state": <taxonomy value>, "detail": str}. Taxonomy (merge contract): unavailable,
# not_configured, checking, verified_online, degraded, offline, simulated, blocked,
# awaiting_approval, failed, unknown. Missing telemetry is "unknown", never healthy.
STATE_PROVIDERS = {}
STATE_TAXONOMY = {"unavailable", "not_configured", "checking", "verified_online", "degraded", "offline",
                  "simulated", "blocked", "awaiting_approval", "failed", "unknown", "available"}


def register_state_provider(capability_id, fn):
    STATE_PROVIDERS[capability_id] = fn


def live_state(capability_id):
    fn = STATE_PROVIDERS.get(capability_id)
    if fn is None:
        return {"state": "unknown", "detail": "No live status provider"}
    try:
        result = fn()
        if result.get("state") not in STATE_TAXONOMY:
            return {"state": "unknown", "detail": "Provider returned an invalid state"}
        return result
    except Exception as exc:  # a broken probe is a failure, not health
        return {"state": "failed", "detail": f"Status probe error: {type(exc).__name__}"}


def get_capability_inventory(interaction=None, runtime=None, sentinel=None, x_manager=None):
    attached = {
        "conversation": interaction is not None,
        "orchestrator": getattr(interaction, "commander", None) is not None if interaction else False,
        "governance": getattr(interaction, "approval_service", None) is not None if interaction else False,
        "x": x_manager is not None,
        "emergency": getattr(runtime, "emergency_stop", None) is not None if runtime else False,
        "sentinel": sentinel is not None,
        "memory": getattr(interaction, "memory_service", None) is not None if interaction else False,
        "desktop": getattr(runtime, "desktop_service", None) is not None if runtime else False,
    }
    # Certain adapters must never be called operational just because files/classes exist.
    forced = {"hood_model": "PLACEHOLDER", "voice": "PLACEHOLDER"}
    entries = []
    for key, name, category, relative, section, description in CAPABILITIES:
        source_present = bool(relative and (ROOT / relative).is_file())
        if key in forced:
            status = forced[key]
        elif attached.get(key):
            status = "ATTACHED_UNVERIFIED"
        elif source_present:
            status = "MODULE_ONLY"
        else:
            status = "PLACEHOLDER"
        entries.append({
            "id": key, "name": name, "category": category,
            "status": status, "description": description,
            "section": section, "source_present": source_present,
            "action": "OPEN_SECTION" if section != "tab-systems" else "DETAILS_ONLY",
            "live": live_state(key),
        })
    return {"release": "NOVA 2.5 preproduction candidate", "basis": "Local code and service attachment; not a live functionality test",
            "items": entries, "counts": {status: sum(c["status"] == status for c in entries)
                                          for status in ("ATTACHED_UNVERIFIED", "MODULE_ONLY", "UNAVAILABLE", "PLACEHOLDER")}}
