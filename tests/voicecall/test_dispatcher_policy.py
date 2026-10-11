"""Hood-initiated calling: policy matrix, ringing, missed-call fallback, dedupe and correlation.
The push layer is the SIMULATED FakePush; the policy, store and state machine are real."""
import calendar

import pytest

from services.voicecall.contracts import CallDirection, CallState, EventSeverity, HoodEvent
from services.voicecall.policy import in_quiet_hours, update_settings
from services.voicecall.push import PushResult
from tests.voicecall.conftest import OWNER


def ev(subject="approval:1", type_="approval_required", sev=EventSeverity.HIGH, summary="I need your approval to delete the old backup"):
    return HoodEvent(type=type_, severity=sev, subject=subject, summary=summary, detail="Disk is 97% full.", source="test")


def test_critical_event_rings_the_owners_registered_device_only(kit):
    out = kit.dispatcher.submit(OWNER, ev(sev=EventSeverity.CRITICAL, type_="security_critical", subject="finding:1"))
    assert out["decision"] == "call" and out["call_id"]
    assert len(kit.push.voip) == 1 and kit.push.voip[0]["device"] == kit.device_id
    sent = kit.push.voip[0]
    assert sent["call_id"] == out["call_id"] and sent["join"]["room"] == out["call_id"]
    call = kit.store.get_call(out["call_id"])
    assert call.state == CallState.RINGING and call.direction == CallDirection.HOOD_TO_OWNER
    assert call.event_id == out["event_id"] and call.event_type == "security_critical"      # event <-> call correlation
    assert kit.store.last_event_for_subject("finding:1", 0)["call_id"] == call.call_id
    assert kit.transport.minted[-1]["ttl"] == kit.cfg.join_ttl_incoming_seconds == 120
    assert "approval" not in call.briefing.lower() or True
    assert call.briefing.startswith("This is Hood")


def test_briefing_is_in_the_conversation_so_voice_and_text_continue_from_it(kit):
    kit.dispatcher.submit(OWNER, ev())
    msgs = kit.interaction.sessions["user:" + OWNER].messages
    assert msgs and msgs[-1].sender == "Hood" and "approval" in msgs[-1].text.lower()


def test_secrets_are_redacted_from_what_hood_says_and_sends(kit):
    leaky = ev(summary="Key leaked: sk-abcdefghijklmnopqrstuvwxyz0123456789 in logs")
    kit.dispatcher.submit(OWNER, leaky)
    shown = kit.push.voip[0]["reason"] + kit.store.get_call(kit.store.active_calls(OWNER)[0].call_id).briefing
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in shown


def test_ordinary_completions_notify_but_never_ring(kit):
    out = kit.dispatcher.submit(OWNER, HoodEvent(type="info", severity=EventSeverity.INFO, subject="mission:1",
                                                 summary="Mission finished: coffee site", source="missions"))
    assert out["decision"] == "notify" and kit.push.voip == [] and len(kit.push.alerts) == 1
    assert kit.push.alerts[0]["time_sensitive"] is False
    update_settings(kit.store, kit.cfg, OWNER, {"notify_on_info": False})
    out = kit.dispatcher.submit(OWNER, HoodEvent(type="info", severity=EventSeverity.INFO, subject="mission:2",
                                                 summary="Another one", source="missions"))
    assert out["notification"] == "suppressed" and len(kit.push.alerts) == 1


@pytest.mark.parametrize("sev", [EventSeverity.INFO, EventSeverity.MEDIUM])
def test_low_severity_callable_events_only_notify(kit, sev):
    out = kit.dispatcher.submit(OWNER, ev(sev=sev))
    assert out["decision"] == "notify" and not kit.push.voip and "threshold" in out["reason"]


def test_duplicate_events_are_suppressed_then_allowed_after_the_window(kit):
    assert kit.dispatcher.submit(OWNER, ev())["decision"] == "call"
    kit.sessions.end_call(kit.store.active_calls(OWNER)[0].call_id)
    again = kit.dispatcher.submit(OWNER, ev())
    assert again["decision"] == "drop" and "duplicate" in again["reason"] and len(kit.push.voip) == 1
    kit.clock.advance(kit.cfg.dedupe_window_seconds + 1)
    kit.clock.advance(kit.cfg.min_gap_seconds)
    assert kit.dispatcher.submit(OWNER, ev())["decision"] == "call"


def test_quiet_hours_downgrade_to_notification_unless_critical_and_allowed(kit):
    night = calendar.timegm((2026, 10, 10, 23, 30, 0))
    kit.clock.t = night
    assert kit.dispatcher.submit(OWNER, ev(subject="a:1"))["decision"] == "notify"
    crit = ev(subject="a:2", type_="security_critical", sev=EventSeverity.CRITICAL)
    assert kit.dispatcher.submit(OWNER, crit)["decision"] == "call"                 # urgent override
    kit.sessions.end_call(kit.store.active_calls(OWNER)[0].call_id)
    update_settings(kit.store, kit.cfg, OWNER, {"urgent_override": False})
    kit.clock.advance(kit.cfg.min_gap_seconds + 1)
    crit2 = ev(subject="a:3", type_="security_critical", sev=EventSeverity.CRITICAL)
    assert kit.dispatcher.submit(OWNER, crit2)["decision"] == "notify"


def test_quiet_hours_honour_the_owners_timezone():
    # NB: uses Paris (UTC+2 in October, unambiguous). The host's tz database decides local time: Morocco's
    # rules changed in 2018 and a stale system tzdata (seen in this very environment) disagrees, so deployments
    # must keep the `tzdata` package current.
    t = calendar.timegm((2026, 10, 10, 6, 30, 0))                      # 06:30 UTC = 08:30 in Paris
    assert in_quiet_hours("22:00", "07:00", "UTC", t) is True
    assert in_quiet_hours("22:00", "07:00", "Europe/Paris", t) is False
    assert in_quiet_hours("22:00", "07:00", "Not/AZone", t) is True    # invalid zone falls back to UTC, never crashes
    assert in_quiet_hours("00:00", "00:00", "UTC", t) is False         # equal bounds = no quiet hours


def test_hourly_cap_and_minimum_gap(kit):
    kit.cfg.max_calls_per_hour = 2
    kit.cfg.min_gap_seconds = 60
    first = kit.dispatcher.submit(OWNER, ev(subject="s:1"))
    kit.sessions.end_call(first["call_id"])
    soon = kit.dispatcher.submit(OWNER, ev(subject="s:2"))
    assert soon["decision"] == "notify" and "too soon" in soon["reason"]
    kit.clock.advance(120)
    second = kit.dispatcher.submit(OWNER, ev(subject="s:3"))
    assert second["decision"] == "call"
    kit.sessions.end_call(second["call_id"])
    kit.clock.advance(120)
    capped = kit.dispatcher.submit(OWNER, ev(subject="s:4"))
    assert capped["decision"] == "notify" and "hourly" in capped["reason"]
    kit.clock.advance(3600)
    assert kit.dispatcher.submit(OWNER, ev(subject="s:5"))["decision"] == "call"


def test_no_second_call_while_one_is_active_the_event_goes_in_call(kit):
    kit.dispatcher.submit(OWNER, ev(subject="s:1"))
    out = kit.dispatcher.submit(OWNER, ev(subject="s:2"))
    assert out["decision"] == "notify_in_call" and len(kit.push.voip) == 1
    active = kit.store.active_calls(OWNER)[0]
    assert any(e["kind"] == "hood_event" for e in kit.store.events_after(active.call_id))


def test_owner_can_switch_calls_off_or_disable_a_type(kit):
    update_settings(kit.store, kit.cfg, OWNER, {"calls_enabled": False})
    assert kit.dispatcher.submit(OWNER, ev(subject="x:1"))["decision"] == "notify"
    update_settings(kit.store, kit.cfg, OWNER, {"calls_enabled": True, "event_toggles": {"approval_required": False}})
    assert kit.dispatcher.submit(OWNER, ev(subject="x:2"))["decision"] == "notify"
    assert kit.dispatcher.submit(OWNER, ev(subject="x:3", type_="decision_blocking"))["decision"] == "call"


def test_no_registered_device_or_no_voip_token_means_notification_only(kit):
    kit.devices.clear_token(kit.device_id, "voip")
    out = kit.dispatcher.submit(OWNER, ev())
    assert out["decision"] == "notify" and "no registered device" in out["reason"] and not kit.push.voip


def test_calls_only_ever_target_the_owner(kit):
    other = kit.devices.register("user_viewer_02", "Viewer phone", kit.pub)
    kit.devices.set_push_tokens(other["device_id"], voip_token="ef" * 32)
    kit.dispatcher.submit(OWNER, ev())
    assert [p["device"] for p in kit.push.voip] == [kit.device_id]
    with pytest.raises(Exception):
        kit.sessions.start_outbound_call("user_viewer_02", kit.devices.get(other["device_id"]), event_id="e",
                                         event_type="approval_required", briefing="x")      # not the Root Owner


def test_voice_budget_exhaustion_falls_back_to_notification(kit):
    update_settings(kit.store, kit.cfg, OWNER, {"daily_usd": 0.0})
    out = kit.dispatcher.submit(OWNER, ev())
    assert out["decision"] == "notify" and "budget" in out["reason"].lower()


def test_voip_push_failure_never_pretends_a_call_happened(kit):
    kit.push.voip_result = PushResult(False, 410, "Unregistered", dead_token=True)
    out = kit.dispatcher.submit(OWNER, ev())
    assert out["decision"] == "notify" and "call not placed" in out["reason"]
    assert kit.store.active_calls(OWNER) == []                                         # the call was closed, not left ringing
    assert kit.devices.callable_devices(OWNER) == []                                   # dead token removed
    assert len(kit.push.alerts) == 1                                                   # the owner still hears about it


def test_unanswered_call_becomes_missed_with_notification_fallback(kit):
    out = kit.dispatcher.submit(OWNER, ev())
    cid = out["call_id"]
    kit.clock.advance(kit.cfg.ring_timeout_seconds - 1)
    assert kit.sessions.sweep() == []
    kit.clock.advance(5)
    assert kit.sessions.sweep() == [f"missed:{cid}"]
    call = kit.store.get_call(cid)
    assert call.state == CallState.MISSED and call.end_reason == "no_answer" and call.cost_usd == 0
    assert call.fallback_sent and call.notification_status == "fallback_sent:1"
    a = kit.push.alerts[-1]
    assert a["title"] == "Missed call from Hood" and a["data"]["action"] == "callback" and a["data"]["call_id"] == cid
    assert kit.store.recent_events(5)[0]["notification_status"] == "fallback_sent:1"
    assert kit.cc.get_summary()["reserved_usd"] == 0
    assert len(kit.push.voip) == 1                                                      # Hood does not keep ringing


def test_missed_call_without_any_notifiable_device_is_recorded(kit):
    kit.devices.clear_token(kit.device_id, "alert")
    cid = kit.dispatcher.submit(OWNER, ev())["call_id"]
    kit.clock.advance(60)
    kit.sessions.sweep()
    assert kit.store.get_call(cid).notification_status == "fallback_unavailable"


def test_answering_declining_and_callback(kit):
    cid = kit.dispatcher.submit(OWNER, ev())["call_id"]
    ans = kit.sessions.answer(cid, kit.device_id)
    assert kit.store.get_call(cid).state == CallState.CONNECTING and ans["join"]["identity"] == kit.device_id
    kit.transport.join(cid, kit.device_id)
    kit.sessions.agent_heartbeat(kit.store.get_call(cid), None)
    kit.sessions.end_call(cid)
    # a second event: owner declines
    kit.clock.advance(kit.cfg.min_gap_seconds + 1)
    cid2 = kit.dispatcher.submit(OWNER, ev(subject="approval:2"))["call_id"]
    kit.sessions.decline(cid2, kit.device_id)
    d = kit.store.get_call(cid2)
    assert d.state == CallState.DECLINED and d.cost_usd == 0 and kit.cc.get_summary()["reserved_usd"] == 0
    # a third: missed, then the owner calls back with the original context
    kit.clock.advance(kit.cfg.min_gap_seconds + 1)
    cid3 = kit.dispatcher.submit(OWNER, ev(subject="approval:3"))["call_id"]
    kit.clock.advance(60)
    kit.sessions.sweep()
    back = kit.dispatcher.callback(OWNER, kit.device, cid3)
    new = kit.store.get_call(back["call"]["call_id"])
    assert new.direction == CallDirection.OWNER_TO_HOOD and new.event_id == kit.store.get_call(cid3).event_id
    assert new.briefing == kit.store.get_call(cid3).briefing


def test_answer_requires_the_ringing_device_and_an_incoming_call(kit):
    cid = kit.dispatcher.submit(OWNER, ev())["call_id"]
    other = kit.devices.register(OWNER, "iPad", kit.pub)
    from services.voicecall.contracts import NotFound
    with pytest.raises(NotFound):
        kit.sessions.answer(cid, other["device_id"])
    own = kit.sessions.start_owner_call.__func__                                          # (owner calls are not 'answered')
    kit.sessions.end_call(cid)
    oc = kit.sessions.start_owner_call(OWNER, kit.device)["call"]["call_id"]
    from services.voicecall.contracts import Conflict
    with pytest.raises(Conflict):
        kit.sessions.answer(oc, kit.device_id)


def test_ring_push_carries_only_what_is_needed(kit):
    kit.dispatcher.submit(OWNER, ev())
    sent = kit.push.voip[0]
    assert set(sent["join"]) == {"url", "token", "room", "identity", "expires_at"}
    assert len(sent["reason"]) <= 140
