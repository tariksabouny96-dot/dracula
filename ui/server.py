"""
HOOD Interactive Surface Local Web Server
Lightweight Python HTTP server serving the interactive HOOD Surface GUI.
Provides REST APIs connecting UI actions directly to InteractionService, ApprovalService,
and EmergencyStopController.
Governed by Master System Specification Sections 2, 4, 10 & HOOD Interactive Surface Spec.
"""

import sys
import json
import re
import threading
import hmac
from urllib.parse import unquote, urlsplit
from pathlib import Path
from http.server import HTTPServer, ThreadingHTTPServer, SimpleHTTPRequestHandler
from typing import Optional, Any

from services.interaction.interaction_service import InteractionService, UIState
from services.capabilities.registry import get_capability_inventory
import services.capabilities.live_probes  # noqa: F401  (registers live status probes)
from services.operations.mission_service import MissionService, MissionConflict
from services.operations.agent_runtime import LocalAgentRuntime, AgentRuntimeConflict
from services.core.emergency_stop import EmergencyStopController
from services.auth.auth_service import AuthenticationService, UserRole, UserPermission, PermissionDeniedError
from services.sentinel.sentinel_service import SecuritySentinelService
from services.x_control.x_session_manager import XSessionManager, XOperationalState
from services.agents.engine import MissionConflict as AgentMissionConflict, MissionBudgetExceeded
from packages.security import EmergencyStopActive
from packages.security.client import classify as classify_client, host_matches
from ui import routes as feature_routes

AGENT_MISSION_ID = re.compile(r"agm_[0-9a-f]{32}")
MAX_BACKGROUND_RUNS = 4


class JarvisUIHandler(SimpleHTTPRequestHandler):
    interaction_service: Optional[InteractionService] = None
    emergency_stop: Optional[EmergencyStopController] = None
    auth_service: Optional[AuthenticationService] = None
    sentinel_service: Optional[SecuritySentinelService] = None
    x_session_manager: Optional[XSessionManager] = None
    runtime: Optional[Any] = None
    mission_service: Optional[MissionService] = None
    agent_engine: Optional[Any] = None
    _agent_runs: dict = {}
    _agent_runs_lock = threading.Lock()
    ui_dir = (Path(__file__).parent / "static").resolve()

    def translate_path(self, path):
        """Keep all static requests inside the static root (including decoded URL paths)."""
        raw_path = urlsplit(path).path
        candidate = unquote(raw_path)
        if "\x00" in candidate or "\\" in candidate:
            return str(self.ui_dir / "__blocked_static_path__")
        if candidate == "/":
            # HOOD NEXT console is the default; the classic console stays at /classic.
            candidate = "next/index.html" if (self.ui_dir / "next" / "index.html").is_file() else "index.html"
        elif candidate in ("/index.html", "/classic", "/classic/"):
            candidate = "index.html"
        elif candidate.startswith("/static/"):
            candidate = candidate[len("/static/"):]
        else:
            candidate = candidate.lstrip("/")
        if candidate.startswith("/") or any(part in (".", "..") for part in Path(candidate).parts):
            return str(self.ui_dir / "__blocked_static_path__")
        resolved = (self.ui_dir / candidate).resolve()
        if not resolved.is_relative_to(self.ui_dir):
            return str(self.ui_dir / "__blocked_static_path__")
        return str(resolved)

    allowed_hosts: Optional[set] = None

    def _host_allowed(self):
        """Block DNS-rebinding: only loopback host names (plus explicit config) may reach the API.

        Configured public names (HOOD_ALLOWED_HOSTS) match with or without the default port: the
        browser sends ``Host: name`` through the TLS proxy, the documented setting is ``name:443``."""
        host = (self.headers.get("Host") or "").strip().lower()
        port = self.server.server_address[1] if getattr(self, "server", None) else None
        if port is not None and host in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}:
            return True
        return any(host_matches(host, entry) for entry in (self.allowed_hosts or ()))

    def _client(self):
        """Direct local client, or a client behind HOOD's reverse proxy (see packages/security/client.py)."""
        peer = self.client_address[0] if getattr(self, "client_address", None) else "127.0.0.1"
        return classify_client(str(peer), self.headers)

    def _session_cookie(self, token: str) -> str:
        secure = "; Secure" if self._client().secure else ""     # HTTPS at the proxy: never send it over HTTP
        return f"hood_session={token}; Path=/; HttpOnly; SameSite=Strict{secure}"

    def _csrf_ok(self, session):
        """Cookie-authenticated state changes must echo the per-session CSRF token.

        Bearer-token clients carry no ambient credential, so they are exempt.
        """
        if not session:
            return True
        cookie = self.headers.get("Cookie", "")
        if "hood_session=" not in cookie:
            return True
        supplied = self.headers.get("X-CSRF-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, session.csrf_token)

    def _authorized(self, session, permission):
        return bool(session and self.auth_service and
                    self.auth_service.has_permission(session.user_id, permission))

    def _request_origin_allowed(self):
        """Prevent cross-origin state changes on the loopback cookie-authenticated UI."""
        origin = self.headers.get("Origin")
        if not origin:
            # Non-browser clients can omit Origin but cannot rely on cross-site forms
            # because operational requests must be JSON.
            return True
        target = urlsplit(origin)
        host = self.headers.get("Host", "")
        # Same host; "https://name" and Host "name:443" (default port written or not) match.
        return target.scheme in ("http", "https") and bool(target.netloc) and host_matches(host, target.netloc)

    def _require_permission(self, session, permission):
        if not session:
            self._send_json({"error": "Authentication required"}, status=401)
            return False
        if not self._authorized(session, permission):
            self._send_json({"error": "Forbidden: insufficient permission"}, status=403)
            return False
        return True

    def _get_authenticated_session(self) -> Optional[Any]:
        if not self.auth_service or not self.auth_service.is_initialized():
            return None
        cookie = self.headers.get("Cookie", "")
        token = None
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("hood_session="):
                token = part.split("=", 1)[1]
                break
        if not token:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
        return self.auth_service.validate_session(token)

    def do_POST(self):
        if not self._host_allowed():
            self._send_json({"error": "Host not allowed"}, status=421)
            return
        if not self._request_origin_allowed():
            self._send_json({"error": "Cross-origin request blocked"}, status=403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 1024 * 1024:
                raise ValueError("Invalid request size")
            if size and self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._send_json({"error": "JSON content required"}, status=415)
                return
            payload = json.loads(self.rfile.read(size).decode("utf-8")) if size else {}
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self._send_json({"error": "Malformed JSON request"}, status=400)
            return

        # 1. Unauthenticated Auth Endpoints
        if self.path == "/api/auth/status":
            is_init = self.auth_service.is_initialized() if self.auth_service else False
            curr_session = self._get_authenticated_session()
            self._send_json({
                "initialized": is_init,
                # First run needs the one-time code printed where HOOD started (never sent here).
                "setup_code_required": not is_init,
                "authenticated": curr_session is not None,
                "username": curr_session.username if curr_session else None,
                "role": curr_session.role.value if curr_session else None,
                "csrf_token": curr_session.csrf_token if curr_session else None
            })
            return

        elif self.path == "/api/auth/init":
            if not self.auth_service:
                self._send_json({"error": "Auth service unavailable"}, status=503)
                return

            # First-run setup: only from this machine directly (never through a proxy, where every
            # internet client also looks like loopback), and only with the one-time setup code shown
            # in the window where HOOD started.
            if self.auth_service.is_initialized():
                self._send_json({"error": "HOOD is already initialized with a Root Owner."}, status=400)
                return
            client = self._client()
            if not client.direct_local:
                self._send_json({"error": "Forbidden: First-run setup can only be executed locally from the host machine "
                                          "(not through a proxy or the network)."}, status=403)
                return
            try:
                code_ok = self.auth_service.check_setup_code(payload.get("setup_code"), ip_address=client.ip)
            except ValueError as ve:
                self._send_json({"error": str(ve)}, status=429)
                return
            if not code_ok:
                self._send_json({"error": "Setup code missing or wrong. It is shown in the window where HOOD started "
                                          "(also saved in " + str(self.auth_service.setup_code_path()) + ")."},
                                status=403)
                return

            try:
                res = self.auth_service.initialize_root_owner(
                    username=payload.get("username", "zack"),
                    display_name=payload.get("display_name", "Zakaria"),
                    password=payload.get("password", "")
                )
                self._send_json({
                    "status": "INITIALIZED",
                    "message": "Root Owner initialized",
                    "one_time_recovery_key": res.get("one_time_recovery_key"),
                    "user": {
                        "user_id": res.get("user_id"),
                        "username": res.get("username"),
                        "display_name": res.get("display_name"),
                        "role": res.get("role")
                    }
                })
            except Exception as e:
                self._send_json({"error": str(e)}, status=400)
            return

        elif self.path == "/api/auth/login":
            if not self.auth_service:
                self._send_json({"error": "Auth service unavailable"}, status=503)
                return

            client_ip = self._client().ip      # behind the proxy: the real client, not the proxy's 127.0.0.1
            user_agent = self.headers.get("User-Agent", "HOOD Web Client")

            try:
                session = self.auth_service.authenticate(
                    username=payload.get("username", ""),
                    password=payload.get("password", ""),
                    ip_address=client_ip,
                    user_agent=user_agent
                )
            except ValueError as ve:
                self._send_json({"error": str(ve)}, status=429)
                return

            if not session:
                # Anti-enumeration generic failure
                self._send_json({"error": "Invalid username or password"}, status=401)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", self._session_cookie(session.session_token))
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "AUTHENTICATED",
                "csrf_token": session.csrf_token,
                "user": {
                    "user_id": session.user_id,
                    "username": session.username,
                    "role": session.role.value
                },
                "username": session.username,
                "role": session.role.value
            }).encode("utf-8"))
            return

        elif self.path == "/api/auth/logout":
            sess = self._get_authenticated_session()
            if sess and not self._csrf_ok(sess):
                self._send_json({"error": "CSRF token missing or invalid"}, status=403)
                return
            if sess and self.auth_service:
                self.auth_service.revoke_session(sess.session_token)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "hood_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "LOGGED_OUT"}).encode("utf-8"))
            return

        elif self.path == "/api/auth/recover":
            if not self.auth_service:
                self._send_json({"error": "Auth service unavailable"}, status=503)
                return

            client_ip = self._client().ip
            try:
                success = self.auth_service.recover_root_owner_password(
                    one_time_recovery_key=payload.get("recovery_key", ""),
                    new_password=payload.get("new_password", ""),
                    ip_address=client_ip
                )
                if success:
                    self._send_json({"status": "SUCCESS", "message": "Root Owner password reset successfully"})
                else:
                    self._send_json({"error": "Invalid recovery key"}, status=400)
            except ValueError as ve:
                status_code = 429 if "wait" in str(ve).lower() else 400
                self._send_json({"error": str(ve)}, status=status_code)
            return

        # Emergency Stop safety path is ALWAYS reachable without session blockage
        elif self.path == "/api/emergency_stop":
            # Reachable without a session from this machine (safety direction only: it can stop,
            # never start, work). Through a proxy it needs a signed-in user: otherwise anyone on the
            # internet could halt HOOD at will. Host, Origin and JSON checks above still apply.
            stopper = self._get_authenticated_session()
            if not stopper and not self._client().direct_local:
                self._send_json({"error": "Sign in to use the emergency stop remotely (on the HOOD machine itself "
                                          "it works without signing in)."}, status=401)
                return
            if self.emergency_stop:
                if self.x_session_manager:
                    self.x_session_manager.stand_down(reason="EMERGENCY_STOP", actor="SYSTEM")
                res = self.emergency_stop.trigger_stop(
                    "Emergency Stop from HOOD Interactive Surface by "
                    + (stopper.username if stopper else "unauthenticated loopback client"))
                if self.interaction_service:
                    self.interaction_service.set_ui_state(UIState.EMERGENCY_STOP)
                self._send_json(res)
            else:
                self._send_json({"error": "Emergency stop controller unavailable; execution state not verified"}, status=503)
            return

        # Require an initialized identity provider for ALL operational endpoints.
        if not self.auth_service or not self.auth_service.is_initialized():
            self._send_json({"error": "Owner setup required"}, status=503)
            return

        # 2. Protected Operational Endpoints
        if self.auth_service and self.auth_service.is_initialized():
            curr_session = self._get_authenticated_session()
            if not curr_session:
                self._send_json({"error": "Authentication required"}, status=401)
                return
            if not self._csrf_ok(curr_session):
                self._send_json({"error": "CSRF token missing or invalid"}, status=403)
                return
            if self._dispatch_feature_route("POST", curr_session, payload):
                return

            client_ip = self._client().ip

            if self.path == "/api/emergency_stop/reset":
                if curr_session.role != UserRole.ROOT_OWNER:
                    self._send_json({"error": "Forbidden: only the Root Owner can release the emergency stop"}, status=403)
                    return
                if not self.emergency_stop:
                    self._send_json({"error": "Emergency stop controller unavailable"}, status=503)
                    return
                if payload.get("confirm") is not True:
                    self._send_json({"error": "Explicit confirmation required"}, status=400)
                    return
                self.emergency_stop.reset_stop(authorized_by=curr_session.username, is_root_owner=True)
                if self.interaction_service:
                    self.interaction_service.set_ui_state(UIState.IDLE)
                self._send_json({"status": "STOP_RELEASED"})
                return

            # Root Owner Security operations
            if self.path == "/api/auth/change_password":
                try:
                    self.auth_service.change_password(
                        user_id=curr_session.user_id,
                        current_password=payload.get("current_password", ""),
                        new_password=payload.get("new_password", ""),
                        ip_address=client_ip
                    )
                    self._send_json({"status": "SUCCESS", "message": "Password updated successfully"})
                except ValueError as ve:
                    status_code = 429 if "wait" in str(ve).lower() else 400
                    self._send_json({"error": str(ve)}, status=status_code)
                except Exception as e:
                    self._send_json({"error": str(e)}, status=400)
                return

            elif self.path == "/api/auth/rotate_recovery_key":
                if curr_session.role != UserRole.ROOT_OWNER:
                    self._send_json({"error": "Forbidden: Only Root Owner can generate recovery keys"}, status=403)
                    return
                try:
                    new_key = self.auth_service.rotate_recovery_key(
                        user_id=curr_session.user_id,
                        current_password=payload.get("current_password", ""),
                        ip_address=client_ip
                    )
                    self._send_json({
                        "status": "SUCCESS",
                        "one_time_recovery_key": new_key,
                        "message": "New recovery key issued. Store it securely offline; this key will not be displayed again."
                    })
                except ValueError as ve:
                    status_code = 429 if "wait" in str(ve).lower() else 400
                    self._send_json({"error": str(ve)}, status=status_code)
                except Exception as e:
                    self._send_json({"error": str(e)}, status=400)
                return

            elif self.path == "/api/auth/sessions/revoke":
                session_id = payload.get("session_id")
                if session_id:
                    success = self.auth_service.revoke_session_id(curr_session.user_id, session_id)
                    if not success:
                        self._send_json({"error": "Session not found or not owned by requester"}, status=403)
                        return
                    self._send_json({"status": "SUCCESS", "revoked": True})
                    return
                target_token = payload.get("session_token", "")
                target = self.auth_service.validate_session(target_token)
                if not target or (target.user_id != curr_session.user_id and curr_session.role != UserRole.ROOT_OWNER):
                    self._send_json({"error": "Session not found or not owned by requester"}, status=403)
                    return
                success = self.auth_service.revoke_session(target_token)
                self._send_json({"status": "SUCCESS", "revoked": success})
                return

            elif self.path == "/api/auth/sessions/revoke_others":
                count = self.auth_service.revoke_other_sessions(
                    user_id=curr_session.user_id,
                    current_session_token=curr_session.session_token
                )
                self._send_json({"status": "SUCCESS", "revoked_count": count})
                return

            # Sentinel Protected Operations
            elif self.path == "/api/sentinel/scan":
                if not self._require_permission(curr_session, UserPermission.X_ACTIVATION):
                    return
                if not self.sentinel_service:
                    self._send_json({"error": "Sentinel service unavailable"}, status=503)
                    return
                summary = self.sentinel_service.run_defensive_assessment()
                self._send_json({"status": "SUCCESS", "summary": summary})
                return

            elif self.path == "/api/sentinel/x/activate":
                if curr_session.role != UserRole.ROOT_OWNER:
                    self._send_json({"error": "Forbidden: Only Root Owner (ZACK) can activate X Red-Team"}, status=403)
                    return
                if not self.sentinel_service:
                    self._send_json({"error": "Sentinel service unavailable"}, status=503)
                    return
                self._send_json({"error": "Direct activation is disabled. Request X in Hood chat, then approve the specific request."}, status=409)
                return

            elif self.path == "/api/sentinel/x/stand_down":
                if not self._require_permission(curr_session, UserPermission.X_ACTIVATION):
                    return
                if not self.sentinel_service:
                    self._send_json({"error": "Sentinel service unavailable"}, status=503)
                    return
                res = self.sentinel_service.x_red_team.stand_down()
                self._send_json(res)
                return

            if self.path.startswith("/api/admin/"):
                if not self.auth_service.has_permission(curr_session.user_id, UserPermission.GLOBAL_USER_ADMIN):
                    self._send_json({"error": "Forbidden: Root Owner authority required"}, status=403)
                    return

                if self.path == "/api/admin/users/create":
                    try:
                        role_str = payload.get("role", "OPERATOR")
                        new_user = self.auth_service.create_user(
                            requester_user_id=curr_session.user_id,
                            username=payload.get("username", ""),
                            display_name=payload.get("display_name", ""),
                            password=payload.get("password", ""),
                            role=UserRole(role_str),
                            assigned_projects=payload.get("assigned_projects", ["default"])
                        )
                        self._send_json({"status": "SUCCESS", "user": new_user.model_dump(exclude={"password_hash", "salt_hex", "recovery_hash", "recovery_salt_hex"})})
                    except Exception as e:
                        self._send_json({"error": str(e)}, status=400)
                    return

                elif self.path == "/api/admin/users/status":
                    try:
                        self.auth_service.set_user_status(
                            requester_user_id=curr_session.user_id,
                            target_user_id=payload.get("user_id", ""),
                            is_active=payload.get("is_active", True)
                        )
                        self._send_json({"status": "SUCCESS"})
                    except Exception as e:
                        self._send_json({"error": str(e)}, status=400)
                    return

        # Every operational action is checked at the HTTP boundary.
        curr_session = self._get_authenticated_session()
        if not curr_session:
            self._send_json({"error": "Authentication required"}, status=401)
            return
        root_routes = ("/api/approvals/resolve", "/api/x/stand_down")
        if self.path in root_routes and not self._require_permission(curr_session, UserPermission.X_ACTIVATION):
            return
        if self.path in ("/api/chat", "/api/interrupt") and not self._require_permission(curr_session, UserPermission.CHAT_INTERACTION):
            return

        # NOVA 2.1: scoped, durable local planning only. No autonomous execution.
        if self.path in ("/api/operations/create", "/api/operations/approve", "/api/operations/cancel", "/api/operations/execute", "/api/operations/analyze", "/api/operations/llm-analyze", "/api/operations/agent-start", "/api/operations/agent-step", "/api/operations/agent-cancel", "/api/operations/agent-reconcile"):
            if not self._require_permission(curr_session, UserPermission.X_ACTIVATION):
                return  # owner-only until multi-user workflow scopes are implemented
            if not self.mission_service:
                self._send_json({"error": "Mission store unavailable"}, status=503)
                return
            try:
                owner = curr_session.user_id
                if self.path.endswith(('/agent-start', '/agent-step', '/agent-cancel', '/agent-reconcile')):
                    if payload.get('confirm') is not True:
                        self._send_json({'error': 'Explicit local agent-run confirmation required'}, status=400)
                        return
                    engine = LocalAgentRuntime(self.mission_service)
                    if self.path.endswith('/agent-start'):
                        result = engine.start(owner, payload.get('mission_id'))
                    elif self.path.endswith('/agent-step'):
                        result = engine.advance(owner, payload.get('mission_id'))
                    elif self.path.endswith('/agent-cancel'):
                        result = engine.cancel(owner, payload.get('mission_id'))
                    else:
                        result = engine.reconcile(owner, payload.get('mission_id'))
                    self._send_json(result)
                elif self.path.endswith('/create'):
                    mission = self.mission_service.create(owner, payload.get('title'), payload.get('objective'))
                    self._send_json(mission, status=201)
                elif self.path.endswith('/approve'):
                    if payload.get('confirm') is not True:
                        self._send_json({"error": "Explicit plan-artifact confirmation required"}, status=400)
                        return
                    self._send_json(self.mission_service.approve_and_create_plan(owner, payload.get('mission_id')))
                elif self.path.endswith('/analyze'):
                    if payload.get('confirm') is not True:
                        self._send_json({'error': 'Explicit local specialist review confirmation required'}, status=400)
                        return
                    self._send_json(self.mission_service.run_specialist_review(owner, payload.get('mission_id')))
                elif self.path.endswith('/llm-analyze'):
                    if payload.get('confirm') is not True or payload.get('acknowledge_network') is not True:
                        self._send_json({'error': 'Explicit provider and network confirmation required'}, status=400)
                        return
                    if __import__('os').environ.get('HOOD_ENABLE_LLM_SPECIALISTS') != '1':
                        self._send_json({'error': 'Provider specialists disabled by administrator'}, status=503)
                        return
                    try:
                        self._send_json(self.mission_service.run_llm_review(owner, payload.get('mission_id')))
                    except RuntimeError:
                        self._send_json({'error': 'Provider not configured or unavailable'}, status=503)
                elif self.path.endswith('/execute'):
                    if payload.get('confirm') is not True:
                        self._send_json({'error': 'Explicit local workflow confirmation required'}, status=400)
                        return
                    self._send_json(self.mission_service.run_local_workflow(owner, payload.get('mission_id')))
                else:
                    self._send_json(self.mission_service.cancel(owner, payload.get('mission_id')))
            except KeyError:
                self._send_json({"error": "Mission not found"}, status=404)
            except (MissionConflict, AgentRuntimeConflict) as exc:
                self._send_json({"error": str(exc)}, status=409)
            except (ValueError, TypeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if self.path.startswith("/api/agents/"):
            self._agent_post(curr_session, payload)
            return

        if self.path == "/api/chat/clear":
            if not self._require_permission(curr_session, UserPermission.CHAT_INTERACTION):
                return
            if payload.get("confirm") is not True:
                self._send_json({"error": "Explicit confirmation required to erase conversation history"}, status=400)
                return
            removed = self.interaction_service.clear_history("user:" + curr_session.user_id) if self.interaction_service else 0
            self._send_json({"status": "ERASED", "messages_removed": removed})
            return

        if self.path == "/api/chat/mission_draft":
            if not self._require_permission(curr_session, UserPermission.CHAT_INTERACTION):
                return
            fallback = payload.get("fallback") if isinstance(payload.get("fallback"), str) else ""
            self._send_json(self.interaction_service.draft_mission_objective(
                "user:" + curr_session.user_id, fallback=fallback[:8000]))
            return

        # Chat / interrupt / approval endpoints
        if self.path == "/api/chat":
            if not self._require_permission(curr_session, UserPermission.X_ACTIVATION):
                # Until memory, tool and X authority are tenant-isolated, owner-only chat.
                return
            text = payload.get("text", "")
            modality = payload.get("modality", "text")
            if not isinstance(text, str) or len(text) > 16000:
                self._send_json({"error": "Invalid chat message"}, status=400)
                return
            if modality == "voice":
                self._send_json({"error": "Voice input unavailable: microphone capture is not connected"}, status=503)
                return
            if modality != "text":
                self._send_json({"error": "Unsupported modality"}, status=400)
                return
            # NOVA 2.2: explicit chat-to-mission handoff. Never infer an execution
            # authorization from natural language or an LLM response.
            if text.strip().lower().startswith('/mission') and (len(text.strip()) == 8 or text.strip()[8:9].isspace()):
                if not self.mission_service:
                    self._send_json({"error": "Mission store unavailable"}, status=503)
                    return
                objective = text.strip()[8:].strip()
                if not 10 <= len(objective) <= 8000:
                    self._send_json({"error": "Use /mission followed by an objective of 10-8000 characters"}, status=400)
                    return
                try:
                    mission = self.mission_service.create(curr_session.user_id, 'Chat mission: ' + objective[:100], objective)
                except (ValueError, TypeError) as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json({
                    "id": mission['id'], "sender": "hood", "speaker_id": "hood",
                    "text": "Mission " + mission['id'] + " created and saved. Nothing has been executed. Open Missions to review the steps and separately approve creation of a local Markdown plan.",
                    "tasks": [], "approval_ref": None, "approval": None,
                    "mission_id": mission['id'], "mission_status": mission['status']
                }, status=201)
                return
            # Non-owners cannot use text commands to activate X or resolve privileged approvals.
            import re
            if not self._authorized(curr_session, UserPermission.X_ACTIVATION) and re.search(
                r"\b(?:activate|wake|authorize|request activation of)\s+x\b|^\s*(?:approve|authorize|confirm|reject|deny)\s+[a-zA-Z0-9_-]+\s*$",
                text, flags=re.IGNORECASE
            ):
                self._send_json({"error": "Owner authorization required for privileged commands"}, status=403)
                return
            sid = "user:" + curr_session.user_id
            resp = self.interaction_service.handle_text_input(text, session_id=sid)
            tasks = [t.model_dump() for t in self.interaction_service.sessions[sid].active_tasks]
            
            # Retrieve pending approval record if approval_ref is set
            approval_data = None
            if getattr(resp, "approval_ref", None):
                all_pending = self.interaction_service.list_pending_approvals()
                matching = [a for a in all_pending if a.get("approval_id") == resp.approval_ref]
                if matching:
                    approval_data = matching[0]

            self._send_json({
                "id": resp.id,
                "text": resp.text,
                "sender": resp.sender,
                "speaker_id": getattr(resp, "speaker_id", "hood"),
                "tasks": tasks,
                "approval_ref": getattr(resp, "approval_ref", None),
                "approval": approval_data,
                "suggested_mission": getattr(resp, "suggested_mission", None),
                "open_mission_draft": bool(getattr(resp, "open_mission_draft", False)),
                "scope_notes": list(getattr(resp, "scope_notes", []) or []),
                "needs_tools": getattr(resp, "needs_tools", None)
            })

        elif self.path == "/api/interrupt":
            self.interaction_service.trigger_barge_in_interruption("user:" + curr_session.user_id)
            self._send_json({"status": "interrupted", "ui_state": "LISTENING"})

        elif self.path == "/api/approvals/resolve":
            appr_id = payload.get("approval_id")
            approved = payload.get("approved", False)
            try:
                req = self.interaction_service.approval_service.get_request(appr_id)
                if not req:
                    self._send_json({"error": "Approval not found"}, status=404)
                    return
                res = self.interaction_service.resolve_approval(appr_id, approved=approved, resolved_by=curr_session.username)
                if res.get("x_result", {}).get("error"):
                    self._send_json(res, status=409)
                else:
                    self._send_json(res)
            except (KeyError, ValueError, PermissionError) as exc:
                self._send_json({"error": str(exc)}, status=409)

        elif self.path == "/api/x/stand_down":
            if self.x_session_manager:
                res = self.x_session_manager.stand_down(reason="UI_STAND_DOWN_CLICKED", actor=curr_session.username)
                if self.interaction_service:
                    session = self.interaction_service.sessions.get(self.interaction_service.active_session_id)
                    if session:
                        session.ui_state = UIState.IDLE
                self._send_json(res)
            elif self.sentinel_service:
                res = self.sentinel_service.x_red_team.stand_down()
                self._send_json(res)
            else:
                self._send_json({"status": "ALREADY_DORMANT"})

        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if not self._host_allowed():
            self._send_json({"error": "Host not allowed"}, status=421)
            return
        if self.path.startswith("/preview/"):
            self._preview_get()
            return
        # Unauthenticated auth check
        if self.path == "/api/auth/status":
            auth_enabled = self.auth_service is not None
            is_init = self.auth_service.is_initialized() if auth_enabled else False
            curr_session = self._get_authenticated_session() if auth_enabled else None
            self._send_json({
                "enabled": auth_enabled,
                "initialized": is_init,
                "setup_code_required": not is_init,
                # Without an identity provider nobody is authenticated; operational APIs return 503.
                "authenticated": curr_session is not None,
                "username": curr_session.username if curr_session else None,
                "role": curr_session.role.value if curr_session else None,
                "csrf_token": curr_session.csrf_token if curr_session else None,
                "emergency_stop": self.emergency_stop.tool_gateway.stop_latch.snapshot()
                    if self.emergency_stop and self.emergency_stop.tool_gateway else None
            })
            return

        # All operational GET endpoints require an initialized identity and RBAC.
        if self.path.startswith("/api/"):
            if not self.auth_service or not self.auth_service.is_initialized():
                self._send_json({"error": "Owner setup required"}, status=503)
                return
            curr_session = self._get_authenticated_session()
            if not curr_session:
                self._send_json({"error": "Authentication required"}, status=401)
                return
            if self._dispatch_feature_route("GET", curr_session, {}):
                return
            permission = UserPermission.VIEW_TELEMETRY
            if self.path.startswith("/api/admin/"):
                permission = UserPermission.GLOBAL_USER_ADMIN
            elif self.path in ("/api/approvals", "/api/x/status", "/api/sentinel/summary", "/api/sentinel/findings", "/api/memory/list"):
                permission = UserPermission.X_ACTIVATION  # owner-only until per-user data isolation exists
            elif self.path.startswith("/api/operations"):
                permission = UserPermission.X_ACTIVATION  # owner scope
            elif self.path.startswith("/api/agents/"):
                permission = UserPermission.EXECUTE_OBJECTIVE  # missions are owner-scoped in the engine
            elif self.path in ("/api/state", "/api/chat/history"):
                permission = UserPermission.CHAT_INTERACTION
            elif self.path in ("/api/economic/summary", "/api/impossible_list", "/api/intelligence/summary", "/api/capabilities"):
                permission = UserPermission.VIEW_PROJECT_DATA
            if not self._require_permission(curr_session, permission):
                return

            if self.path == "/api/auth/sessions":
                if not curr_session:
                    self._send_json({"error": "Authentication required"}, status=401)
                    return
                sessions = self.auth_service.list_active_sessions(curr_session.user_id)
                self._send_json(sessions)
                return

            if self.path == "/api/admin/users":
                if not curr_session or not self.auth_service.has_permission(curr_session.user_id, UserPermission.GLOBAL_USER_ADMIN):
                    self._send_json({"error": "Forbidden: Root Owner authority required"}, status=403)
                    return
                users = self.auth_service.list_users(curr_session.user_id)
                self._send_json(users)
                return

        if self.path.startswith("/api/agents/"):
            self._agent_get(curr_session)
            return
        if self.path.startswith('/api/operations/agent-status/'):
            if not self.mission_service:
                self._send_json({'error': 'Mission store unavailable'}, status=503)
                return
            mission_id = self.path.removeprefix('/api/operations/agent-status/')
            try:
                if not re.fullmatch(r'msn_[0-9a-f]{32}', mission_id):
                    raise KeyError('Mission not found')
                self._send_json(LocalAgentRuntime(self.mission_service).status(curr_session.user_id, mission_id))
            except KeyError:
                self._send_json({'error': 'Mission not found'}, status=404)
            return
        if self.path == "/api/operations":
            self._send_json(self.mission_service.list(curr_session.user_id) if self.mission_service else [])
            return
        if self.path.startswith('/api/operations/agent-preview/'):
            if not self.mission_service:
                self._send_json({'error': 'Mission store unavailable'}, status=503)
                return
            mission_id = self.path.removeprefix('/api/operations/agent-preview/')
            try:
                if not re.fullmatch(r'msn_[0-9a-f]{32}', mission_id):
                    raise KeyError('Mission not found')
                data = LocalAgentRuntime(self.mission_service).preview(curr_session.user_id, mission_id)
            except KeyError:
                self._send_json({'error': 'Mission not found'}, status=404)
                return
            except (AgentRuntimeConflict, OSError) as exc:
                self._send_json({'error': str(exc)}, status=409)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 'attachment; filename="hood-local-preview.html"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/api/operations/llm-artifact/"):
            if not self.mission_service:
                self._send_json({'error': 'Mission store unavailable'}, status=503)
                return
            mission_id = self.path.removeprefix('/api/operations/llm-artifact/')
            try:
                if not re.fullmatch(r'msn_[0-9a-f]{32}', mission_id):
                    raise KeyError('Mission not found')
                data = self.mission_service.llm_artifact(curr_session.user_id, mission_id)
            except KeyError:
                self._send_json({'error': 'Mission not found'}, status=404)
                return
            except (MissionConflict, OSError) as exc:
                self._send_json({'error': str(exc)}, status=409)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="hood-llm-review.json"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/api/operations/specialist-artifact/"):
            if not self.mission_service:
                self._send_json({'error': 'Mission store unavailable'}, status=503)
                return
            mission_id = self.path.removeprefix('/api/operations/specialist-artifact/')
            try:
                if not re.fullmatch(r'msn_[0-9a-f]{32}', mission_id):
                    raise KeyError('Mission not found')
                data = self.mission_service.specialist_artifact(curr_session.user_id, mission_id)
            except KeyError:
                self._send_json({'error': 'Mission not found'}, status=404)
                return
            except (MissionConflict, OSError) as exc:
                self._send_json({'error': str(exc)}, status=409)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="hood-specialist-review.json"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/api/operations/execution-artifact/"):
            if not self.mission_service:
                self._send_json({'error': 'Mission store unavailable'}, status=503)
                return
            mission_id = self.path.removeprefix('/api/operations/execution-artifact/')
            try:
                if not re.fullmatch(r'msn_[0-9a-f]{32}', mission_id):
                    raise KeyError('Mission not found')
                data = self.mission_service.execution_artifact(curr_session.user_id, mission_id)
            except KeyError:
                self._send_json({'error': 'Mission not found'}, status=404)
                return
            except (MissionConflict, OSError) as exc:
                self._send_json({'error': str(exc)}, status=409)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="hood-local-execution.json"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/api/operations/artifact/"):
            if not self.mission_service:
                self._send_json({"error": "Mission store unavailable"}, status=503)
                return
            try:
                mission_id = self.path.removeprefix("/api/operations/artifact/")
                if not mission_id.startswith('msn_') or not all(c in '0123456789abcdef' for c in mission_id[4:]):
                    raise KeyError('Mission not found')
                data = self.mission_service.artifact(curr_session.user_id, mission_id)
            except KeyError:
                self._send_json({"error": "Mission not found"}, status=404)
                return
            except (MissionConflict, OSError) as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="hood-local-plan.md"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path == "/api/sentinel/summary":
            if self.sentinel_service:
                self._send_json(self.sentinel_service.get_security_summary())
            else:
                self._send_json({"status": "UNAVAILABLE", "message": "Sentinel Service not initialized"})
            return

        elif self.path == "/api/sentinel/findings":
            if self.sentinel_service:
                findings_list = [f.model_dump() for f in self.sentinel_service.findings.values()]
                self._send_json(findings_list)
            else:
                self._send_json([])
            return

        elif self.path == "/api/approvals":
            approvals = self.interaction_service.list_pending_approvals() if self.interaction_service else []
            self._send_json(approvals)
        elif self.path == "/api/state":
            session = self.interaction_service._get_or_create_session("user:" + curr_session.user_id)
            self._send_json({
                "ui_state": session.ui_state.value,
                "tasks": [t.model_dump() for t in session.active_tasks],
                "messages_count": len(session.messages)
            })
        elif self.path == "/api/chat/history":
            # Only the caller's own server-derived session id is ever loaded.
            if not self.interaction_service:
                self._send_json({"messages": [], "durable": False})
                return
            session = self.interaction_service._get_or_create_session("user:" + curr_session.user_id)
            self._send_json({"durable": self.interaction_service.conversation_store is not None,
                             "messages": [m.model_dump(include={"id", "sender", "modality", "text", "timestamp",
                                                                "speaker_id"}) for m in session.messages[-200:]]})
        elif self.path == "/api/telemetry":
            self._send_json(self._get_live_telemetry())
        elif self.path == "/api/x/status":
            if self.x_session_manager:
                self._send_json(self.x_session_manager.get_status())
            else:
                self._send_json({
                    "state": "DORMANT",
                    "badge": "X: DORMANT",
                    "is_active": False,
                    "is_pending": False,
                    "remaining_seconds": 0
                })
        elif self.path == "/api/economic/summary":
            commander = getattr(self.interaction_service, "commander", None) if self.interaction_service else None
            econ = getattr(commander, "economic_engine", None) if commander else None
            if econ:
                try:
                    pipeline_records = econ.list_qualified_opportunities()
                    pipeline_data = [op.model_dump() for op in pipeline_records]
                except Exception:
                    pipeline_data = []
                self._send_json({
                    "mode": econ.current_financial_mode.value,
                    "pipeline": pipeline_data,
                    "daily_spend": "$0.00",
                    "budget_limit": "$0.00 (Hard Cap Zero Autonomous Spend)",
                    "rent_vs_own": {
                        "hardware": "NVIDIA RTX 4090 (Simulated)",
                        "purchase_cost": 1600.0,
                        "monthly_owned_cost": 150.0,
                        "breakeven_months": 8.5
                    }
                })
            else:
                self._send_json({
                    "mode": "NORMAL",
                    "pipeline": [],
                    "daily_spend": "$0.00",
                    "budget_limit": "$0.00",
                    "rent_vs_own": None
                })
        elif self.path == "/api/impossible_list":
            commander = getattr(self.interaction_service, "commander", None) if self.interaction_service else None
            imp = getattr(commander, "impossible_list", None) if commander else None
            if imp:
                items = [item.model_dump() for item in imp.list_unresolved()]
                self._send_json(items)
            else:
                self._send_json([])
        elif self.path == "/api/intelligence/summary":
            commander = getattr(self.interaction_service, "commander", None) if self.interaction_service else None
            lab = getattr(commander, "intelligence_lab", None) if commander else None
            gap_map = lab.compute_frontier_gap_map() if lab else None
            self._send_json({
                "levels": {
                    "level_1": "Gemini 2.5 Flash (PRIMARY FRONTIER)",
                    "level_2": "LLaMA 3.1 8B (SELF-HOSTED FALLBACK)",
                    "level_3": "Hood-Code-v1 (SHADOW VERIFICATION)"
                },
                "frontier_gap": gap_map.frontier_gap_percentage if gap_map else 12.0,
                "level_1_score": gap_map.level_1_frontier_baseline_score if gap_map else 92.0,
                "level_2_score": gap_map.level_2_self_hosted_score if gap_map else 80.0,
                "level_3_score": gap_map.level_3_hood_score if gap_map else 80.96,
                "acceleration_mechanisms_active": 50,
                "gpu_topology": "Detected Local Compute (CPU/CUDA Hybrid Fallback)"
            })
        elif self.path == "/api/capabilities":
            self._send_json(get_capability_inventory(self.interaction_service, self.runtime,
                                                     self.sentinel_service, self.x_session_manager))
        elif self.path == "/api/memory/list":
            mem_service = getattr(self.interaction_service, "memory_service", None) if self.interaction_service else None
            if mem_service:
                try:
                    # Scope the listing to the authenticated principal so one user
                    # never sees another's memory through this endpoint (F25).
                    principal = curr_session.username
                    p_mems = [m.model_dump(mode="json") for m in mem_service.query_memories(project="personal", principal=principal)]
                    c_mems = [m.model_dump(mode="json") for m in mem_service.query_memories(project="conversation", principal=principal)]
                    all_mems = p_mems + c_mems
                    self._send_json({
                        "count": len(all_mems),
                        "personal_count": len(p_mems),
                        "memories": all_mems,
                        "status": "OPERATIONAL"
                    })
                except Exception as e:
                    self._send_json({"count": 0, "personal_count": 0, "memories": [], "error": str(e), "status": "ERROR"})
            else:
                self._send_json({"count": 0, "personal_count": 0, "memories": [], "status": "NO_SERVICE"})
        elif self.path.startswith("/api/"):
            self.send_response(404)
            self.end_headers()
        else:
            super().do_GET()

    # ------------------------------------------------------------------ feature routes (ui/routes.py)
    def _dispatch_feature_route(self, method, session, payload) -> bool:
        from urllib.parse import parse_qs
        parts = urlsplit(self.path)
        route_def, match = feature_routes.find(method, parts.path)
        if route_def is None:
            return False
        try:
            permission = UserPermission(route_def.permission)
        except ValueError:
            self._send_json({"error": "Route misconfigured"}, status=500)
            return True
        if not self._require_permission(session, permission):
            return True
        ctx = feature_routes.RequestContext(session=session, payload=payload, match=match,
                                            query=parse_qs(parts.query), services=feature_routes.SERVICES)
        try:
            result = route_def.handler(ctx)
        except KeyError:
            self._send_json({"error": "Not found"}, status=404)
            return True
        except EmergencyStopActive as exc:
            self._send_json({"error": str(exc)}, status=423)
            return True
        except PermissionError as exc:
            self._send_json({"error": str(exc) or "Forbidden"}, status=403)
            return True
        except feature_routes.RouteConflict as exc:
            self._send_json({"error": str(exc)}, status=409)
            return True
        except feature_routes.ServiceUnavailable as exc:
            self._send_json({"error": str(exc)}, status=503)
            return True
        except (ValueError, TypeError) as exc:
            self._send_json({"error": str(exc)[:500]}, status=400)
            return True
        if isinstance(result, feature_routes.Stream):
            self._send_event_stream(session, result)
            return True
        if isinstance(result, feature_routes.Raw):
            self.send_response(result.status)
            self.send_header("Content-Type", result.content_type)
            if result.filename:
                safe = re.sub(r'[^A-Za-z0-9._-]', '_', result.filename)[:120]
                self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
            for key, value in result.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(result.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(result.body)
            return True
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
            self._send_json(result[1], status=result[0])
        else:
            self._send_json(result)
        return True

    _streams_per_user: dict = {}
    _streams_lock = threading.Lock()
    MAX_STREAMS_PER_USER = 3

    def _send_event_stream(self, session, stream):
        """SSE writer: bounded per user and in time; heartbeats keep proxies honest."""
        import time as _time
        with self._streams_lock:
            active = self._streams_per_user.get(session.user_id, 0)
            if active >= self.MAX_STREAMS_PER_USER:
                self._send_json({"error": "Too many open event streams"}, status=429)
                return
            self._streams_per_user[session.user_id] = active + 1
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            started, last_beat = _time.monotonic(), _time.monotonic()
            for item in stream.events:
                if _time.monotonic() - started > stream.max_seconds:
                    break
                if item is None:
                    if _time.monotonic() - last_beat >= stream.heartbeat_seconds:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        last_beat = _time.monotonic()
                    continue
                event_id, event_type, data = item
                payload = json.dumps(data, default=str).replace("\n", " ")
                self.wfile.write(f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                last_beat = _time.monotonic()
        except ConnectionError:  # BrokenPipe/Reset, and ConnectionAborted (WinError 10053)
            pass
        finally:
            with self._streams_lock:
                self._streams_per_user[session.user_id] = max(0, self._streams_per_user.get(session.user_id, 1) - 1)
            self.close_connection = True

    # ------------------------------------------------------------------ agent engine API
    def _agent_engine_or_503(self):
        if self.agent_engine is None:
            self._send_json({"error": "Agent engine not configured on this server"}, status=503)
            return None
        return self.agent_engine

    def _agent_mission_id(self, rest):
        mission_id = rest.split("/", 1)[0]
        if not AGENT_MISSION_ID.fullmatch(mission_id):
            raise KeyError("Mission not found")
        return mission_id

    @classmethod
    def _agent_background_run(cls, owner, mission_id):
        engine = cls.agent_engine
        try:
            engine.run(owner, mission_id)
        except Exception:
            pass  # state and errors are persisted by the engine; status shows them
        finally:
            with cls._agent_runs_lock:
                cls._agent_runs.pop(mission_id, None)

    @classmethod
    def start_agent_run(cls, owner, mission_id):
        """Start a mission's background run: "started", "already" (running) or "busy" (too many)."""
        with cls._agent_runs_lock:
            if mission_id in cls._agent_runs:
                return "already"
            if len(cls._agent_runs) >= MAX_BACKGROUND_RUNS:
                return "busy"
            worker = threading.Thread(target=cls._agent_background_run, args=(owner, mission_id), daemon=True)
            cls._agent_runs[mission_id] = worker
        worker.start()
        return "started"

    def _agent_post(self, session, payload):
        engine = self._agent_engine_or_503()
        if engine is None:
            return
        if not self._require_permission(session, UserPermission.EXECUTE_OBJECTIVE):
            return
        owner = session.user_id
        try:
            if self.path == "/api/agents/missions":
                objective = payload.get("objective")
                budget = payload.get("budget_usd", 1.0)
                if payload.get("confirm") is not True:
                    self._send_json({"error": "Explicit confirmation required: planning calls a model provider"}, status=400)
                    return
                profile = payload.get("profile", "python_app")
                created = engine.create_mission(owner, objective, budget, profile=profile)
                if self.interaction_service is not None and hasattr(self.interaction_service, "mission_planned"):
                    self.interaction_service.mission_planned("user:" + owner)   # stop re-offering it in chat
                self._send_json(created, status=201)
                return
            rest = self.path.removeprefix("/api/agents/missions/")
            mission_id = self._agent_mission_id(rest)
            action = rest[len(mission_id):]
            if action == "/approve":
                if not self._require_permission(session, UserPermission.APPROVE_ACTIONS):
                    return
                if payload.get("confirm") is not True:
                    self._send_json({"error": "Explicit plan approval required"}, status=400)
                    return
                self._send_json(engine.approve_plan(owner, mission_id, payload.get("plan_sha256"), session.username))
            elif action == "/run":
                if payload.get("confirm") is not True:
                    self._send_json({"error": "Explicit run confirmation required"}, status=400)
                    return
                status = engine.status(owner, mission_id)
                if status["state"] not in ("QUEUED", "RUNNING", "VERIFYING"):
                    raise AgentMissionConflict(f"Mission is {status['state']}; it cannot run")
                started = self.start_agent_run(owner, mission_id)
                if started == "already":
                    raise AgentMissionConflict("Mission is already running")
                if started == "busy":
                    self._send_json({"error": "Too many missions running; try again later"}, status=429)
                    return
                self._send_json({"status": "RUN_STARTED", "mission_id": mission_id}, status=202)
            elif action == "/cancel":
                self._send_json(engine.cancel(owner, mission_id, session.username))
            elif action == "/retry":
                if payload.get("confirm") is not True:
                    self._send_json({"error": "Explicit retry confirmation required"}, status=400)
                    return
                self._send_json(engine.retry_blocked(owner, mission_id, session.username))
            else:
                self._send_json({"error": "Not found"}, status=404)
        except KeyError:
            self._send_json({"error": "Mission not found"}, status=404)
        except EmergencyStopActive as exc:
            self._send_json({"error": str(exc)}, status=423)
        except (AgentMissionConflict, MissionBudgetExceeded) as exc:
            self._send_json({"error": str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _agent_get(self, session):
        engine = self._agent_engine_or_503()
        if engine is None:
            return
        owner = session.user_id
        try:
            if self.path == "/api/agents/missions":
                self._send_json(engine.list(owner))
                return
            rest = self.path.removeprefix("/api/agents/missions/")
            mission_id = self._agent_mission_id(rest)
            action = rest[len(mission_id):]
            if action == "":
                status = engine.status(owner, mission_id)
                with self._agent_runs_lock:
                    status["background_run_active"] = mission_id in self._agent_runs
                self._send_json(status)
            elif action == "/events":
                self._send_json(engine.events(owner, mission_id))
            elif action == "/receipts":
                self._send_json(engine.verify_receipts(owner, mission_id))
            elif action == "/artifact":
                name, data, digest = engine.artifact(owner, mission_id)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("X-Content-SHA256", digest)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send_json({"error": "Not found"}, status=404)
        except KeyError:
            self._send_json({"error": "Mission not found"}, status=404)
        except AgentMissionConflict as exc:
            self._send_json({"error": str(exc)}, status=409)

    def _get_live_telemetry(self) -> dict:
        """Collects grounded runtime telemetry from attached services or system inspection."""
        from services.core.system_diagnostics import SystemDiagnosticsCollector
        diag = SystemDiagnosticsCollector.collect(self.interaction_service.config if self.interaction_service else None)

        # Inspect real foreground window if desktop backend is present
        current_app = "Not inspected"
        control_method = "Unverified"
        verification_state = "NONE (MONITORING)"
        financial_spend = "Spend not independently measured / Pending: 0"
        unattended_tasks = "0 running / 0 queued"

        if self.runtime:
            if hasattr(self.runtime, "desktop_service"):
                fw = self.runtime.desktop_service.inspect_active_window()
                if fw:
                    current_app = f"{fw.title} ({fw.process_name})"
            if hasattr(self.runtime, "financial_advisor"):
                pending_fin = sum(1 for r in self.runtime.financial_advisor.recommendations.values() if r.approval_status.value == "PENDING")
                financial_spend = f"Spend not independently measured / Pending: {pending_fin}"
            if hasattr(self.runtime, "overnight_manager"):
                running_cnt = sum(1 for s in self.runtime.overnight_manager.branch_states.values() if s.value == "RUNNING")
                unattended_tasks = f"{running_cnt} running / 0 queued"

        # Truthful Mic & Provider state (Explicitly distinguish live execution vs simulated input)
        mic_status = "MIC: NOT CONNECTED"
        provider_badge = "PROVIDER: NOT VERIFIED"
        if self.interaction_service and self.interaction_service.voice_router:
            vr = self.interaction_service.voice_router
            mode_name = vr.current_provider_mode.value.replace("_", " ")
            provider_badge = f"VOICE MODE: {mode_name} (NOT LIVE VERIFIED)"

        # Evolution Model Levels (Truthfully reflect active vs offline local status)
        evo_models = {
            "level_1": "Gemini adapter (availability unverified)",
            "level_2": "Local adapter (endpoint unverified)",
            "level_3": "Hood candidate (not deployed)",
            "gpu_topology": diag["hardware"]["gpu"]
        }

        # Node Status
        node_status = f"LOCAL NODE: {diag['runtime']['node_role'].upper()} (HEALTH UNVERIFIED)"

        return {
            "desktop": {
                "badge": "DESKTOP: UNVERIFIED",
                "current_app": current_app,
                "control_method": control_method,
                "verification_state": verification_state,
                "financial_spend": financial_spend,
                "unattended_tasks": unattended_tasks
            },
            "mic": {
                "badge": mic_status
            },
            "provider": {
                "badge": provider_badge
            },
            "evolution": {
                "badge": "EVOLUTION: EXPERIMENTAL",
                "level_1": evo_models["level_1"],
                "level_2": evo_models["level_2"],
                "level_3": evo_models["level_3"],
                "gpu_topology": evo_models["gpu_topology"]
            },
            "nodes": {
                "badge": node_status
            },
            "x_status": self.x_session_manager.get_status() if self.x_session_manager else {
                "state": "DORMANT",
                "badge": "X: DORMANT",
                "is_active": False,
                "is_pending": False,
                "remaining_seconds": 0
            },
            "voice": {
                "daily_spend": "Not independently metered",
                "active_sessions": f"{len(self.interaction_service.sessions)} text session(s)" if self.interaction_service else "0 text sessions"
            },
            "v1_2": {
                "impossible_list_barriers": len(self.interaction_service.commander.impossible_list.list_unresolved()) if self.interaction_service and hasattr(self.interaction_service.commander, "impossible_list") else 0,
                "economic_mode": self.interaction_service.commander.economic_engine.current_financial_mode.value if self.interaction_service and hasattr(self.interaction_service.commander, "economic_engine") else "NORMAL",
                "frontier_gap": self.interaction_service.commander.intelligence_lab.compute_frontier_gap_map().frontier_gap_percentage if self.interaction_service and hasattr(self.interaction_service.commander, "intelligence_lab") else None
            },
            "sentinel": self.sentinel_service.get_security_summary() if self.sentinel_service else {
                "posture": "NOT INSPECTED",
                "last_scan": "NOT RUN",
                "open_findings_count": 0,
                "findings_by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFORMATIONAL": 0},
                "firewall": {"active": None, "mode": "READ-ONLY", "listening_ports_count": 0, "unexpected_ports": [], "baseline_deviations": []},
                "integrity": {"monitored_targets_count": 0, "drift_detected": None},
                "self_healing": {"recent_actions_count": 0, "last_action": None},
                "x_red_team": {"status": "DORMANT", "total_findings_discovered": 0},
                "patches": {"pending_approval_count": 0, "deployed_count": 0}
            }
        }

    def _preview_get(self):
        """Website-mission preview: /preview/<mission>/<token>/<path>.

        No session cookie is involved: the token is a per-mission capability only the owner's
        status view hands out. The page runs sandboxed (opaque origin, no network, no forms
        submitted, resources only from its own preview folder), so agent-written JavaScript
        can never act on HOOD with the owner's session.
        """
        parts = urlsplit(self.path).path.split("/", 4)   # ['', 'preview', mid, token, rest]
        engine = self.agent_engine
        try:
            if engine is None or len(parts) < 4 or not AGENT_MISSION_ID.fullmatch(parts[2]):
                raise KeyError("Not found")
            data, mime = engine.preview_file(parts[2], parts[3], unquote(parts[4] if len(parts) > 4 else ""))
        except KeyError:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        own = f"http://{self.headers.get('Host', '').strip()}/preview/{parts[2]}/{parts[3]}/"
        self._csp_override = (
            "sandbox allow-scripts allow-modals allow-popups; "
            f"default-src {own} data:; script-src {own} 'unsafe-inline'; style-src {own} 'unsafe-inline'; "
            f"img-src {own} data: blob:; font-src {own} data:; media-src {own} data:; connect-src 'none'; "
            "form-action 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        # Defense in depth for every response, including static files.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", getattr(self, "_csp_override", None) or (
                         "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
                         "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"))
        super().end_headers()

    def _send_json(self, data, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logs
        pass


class _QuietDisconnectServer(ThreadingHTTPServer):
    """A browser closing a tab, refreshing or switching pages mid-response is
    normal, not an error: don't print a traceback for it (Windows reports it as
    ConnectionAbortedError / WinError 10053). Real errors are still printed."""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class JarvisServer:
    def __init__(
        self,
        interaction_service: Optional[InteractionService] = None,
        emergency_stop: Optional[EmergencyStopController] = None,
        runtime: Optional[Any] = None,
        port: int = 8999,
        auth_service: Optional[AuthenticationService] = None,
        sentinel_service: Optional[SecuritySentinelService] = None,
        x_session_manager: Optional[XSessionManager] = None,
        agent_engine: Optional[Any] = None
    ):
        self.port = port
        self.auth_service = auth_service or getattr(runtime, "auth_service", None)
        self.sentinel_service = sentinel_service or (getattr(runtime, "sentinel_service", None) or SecuritySentinelService())
        
        # Initialize x_session_manager if not passed
        if x_session_manager is not None:
            self.x_session_manager = x_session_manager
        elif hasattr(runtime, "x_session_manager") and getattr(runtime, "x_session_manager") is not None:
            self.x_session_manager = getattr(runtime, "x_session_manager")
        elif interaction_service and getattr(interaction_service, "x_session_manager", None):
            self.x_session_manager = interaction_service.x_session_manager
        else:
            approval_svc = getattr(interaction_service, "approval_service", None) or getattr(runtime, "approval_service", None)
            if approval_svc:
                audit_svc = getattr(runtime, "audit_service", None)
                x_ctrl = getattr(runtime, "x_controller", None)
                self.x_session_manager = XSessionManager(
                    approval_service=approval_svc,
                    audit_service=audit_svc,
                    x_controller=x_ctrl,
                    sentinel_service=self.sentinel_service
                )
            else:
                self.x_session_manager = None

        if interaction_service and self.x_session_manager:
            interaction_service.x_session_manager = self.x_session_manager

        # Extra Host names (e.g. a TLS reverse proxy's name) must be listed explicitly.
        import os as _os
        JarvisUIHandler.allowed_hosts = {h.strip().lower() for h in
                                         _os.environ.get("HOOD_ALLOWED_HOSTS", "").split(",") if h.strip()}
        feature_routes.load_modules()
        JarvisUIHandler.interaction_service = interaction_service
        JarvisUIHandler.emergency_stop = emergency_stop
        JarvisUIHandler.runtime = runtime
        JarvisUIHandler.mission_service = MissionService(Path.home() / ".hood" / "nova21")
        engine = agent_engine if agent_engine is not None else getattr(runtime, "agent_engine", None)
        JarvisUIHandler.agent_engine = engine
        # Shared service instances for feature modules (ui/routes.py). One approval service for
        # the whole server, so module approvals and the approval centre are the same records.
        shared_approvals = getattr(interaction_service, "approval_service", None) or getattr(runtime, "approval_service", None)
        for name, value in (("agents", engine), ("router", getattr(runtime, "model_router", None)),
                            ("approvals", shared_approvals), ("x", self.x_session_manager),
                            ("emergency_stop", emergency_stop), ("auth", self.auth_service),
                            ("firewall", getattr(runtime, "firewall", None)),
                            ("selfdev", getattr(runtime, "self_dev", None)),
                            ("memory", getattr(runtime, "memory_service", None)),
                            ("learning", getattr(runtime, "learning", None)),
                            ("toolbox", getattr(runtime, "toolbox", None) or getattr(engine, "toolbox", None)),
                            ("wsl_sandbox", getattr(runtime, "wsl_sandbox", None))):
            if value is not None:
                feature_routes.SERVICES[name] = value
            else:
                feature_routes.SERVICES.pop(name, None)
        JarvisUIHandler._agent_runs = {}
        # Reset the per-user SSE stream counter on every server start so a stream
        # that was not cleanly torn down in a previous lifecycle cannot leak into
        # this one and spuriously trip MAX_STREAMS_PER_USER (observed across
        # in-process test servers that reuse the fixed root-owner user id).
        JarvisUIHandler._streams_per_user = {}
        if engine is not None:
            engine.recover()  # reconcile work interrupted by a previous crash before serving

            def _continue(owner, mission_id):
                # A mission that continues by itself (its sandbox/tools became ready): if too many
                # are running, it stays in VERIFYING and the owner's Run (or the next start) picks it up.
                JarvisUIHandler.start_agent_run(owner, mission_id)
            engine.continue_runner = _continue
            try:
                engine.resume_waiting()   # e.g. HOOD's sandbox finished while HOOD was stopped
            except Exception:
                pass
        JarvisUIHandler.auth_service = self.auth_service
        JarvisUIHandler.sentinel_service = self.sentinel_service
        JarvisUIHandler.x_session_manager = self.x_session_manager
        if interaction_service and self.auth_service:
            interaction_service.auth_service = self.auth_service
        if interaction_service is not None and emergency_stop is not None:
            interaction_service.emergency_stop = emergency_stop
        if self.x_session_manager is not None and self.auth_service is not None:
            self.x_session_manager.auth_service = self.auth_service
        self.httpd = _QuietDisconnectServer(("127.0.0.1", port), JarvisUIHandler)
        self.thread: Optional[threading.Thread] = None

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# Backward-compatible and canonical aliases
HoodUIHandler = JarvisUIHandler
HoodServer = JarvisServer
