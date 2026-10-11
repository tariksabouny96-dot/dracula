"""Call lifecycle, limits, watchdog and cost enforcement (transport is the in-memory SIMULATED one)."""
import pytest

from services.voicecall.contracts import (CallDirection, CallState, Conflict, EndReason, Forbidden, NotFound,
                                          RateLimited, VoiceBudgetExceeded)
from services.voicecall.policy import update_settings
from services.voicecall.profiles import get_profile
from tests.voicecall.conftest import OWNER


def _start(kit):
    return kit.sessions.start_owner_call(OWNER, kit.device)


def _connect(kit, call_id):
    """Simulate the worker's first heartbeat (the worker is what proves media is flowing)."""
    call = kit.store.get_call(call_id)
    return kit.sessions.agent_heartbeat(call, {"audio_in_s": 10, "audio_out_s": 5})


def test_owner_call_creates_scoped_session_and_reserves_budget(kit):
    r = _start(kit)
    call = kit.store.get_call(r["call"]["call_id"])
    assert call.state == CallState.CONNECTING and call.direction == CallDirection.OWNER_TO_HOOD
    assert call.reserved_usd > 0 and call.reservation_id                      # worst case reserved on Hood's caps
    assert kit.cc.get_summary()["reserved_usd"] == pytest.approx(call.reserved_usd)
    minted = kit.transport.minted[-1]
    assert minted["identity"] == kit.device_id and minted["dispatch_agent"] and minted["ttl"] == 600
    assert r["join"]["room"] == call.room == call.call_id


def test_second_call_is_refused_and_double_tap_is_the_same_call(kit):
    first = _start(kit)
    again = _start(kit)                                                       # same device: same call, new token
    assert again["call"]["call_id"] == first["call"]["call_id"]
    other_key = kit.devices.register(OWNER, "iPad", kit.pub)
    other = kit.devices.get(other_key["device_id"])
    with pytest.raises(Conflict):
        kit.sessions.start_owner_call(OWNER, other)


def test_only_the_registered_owner_device_can_start(kit):
    with pytest.raises(Forbidden):
        kit.sessions.start_owner_call("user_viewer_02", {**kit.device, "owner_id": "user_viewer_02"})
    other_owner_dev = {**kit.device, "owner_id": "user_viewer_02"}
    with pytest.raises(Forbidden):
        kit.sessions.start_owner_call(OWNER, other_owner_dev)                 # device belongs to someone else
    kit.devices.revoke(OWNER, kit.device_id)
    with pytest.raises(Forbidden):
        kit.sessions.start_owner_call(OWNER, kit.devices.get(kit.device_id))   # revoked


def test_disabled_or_unconfigured_is_refused(kit):
    kit.cfg.enabled = False
    from services.voicecall.contracts import NotConfigured
    with pytest.raises(NotConfigured):
        _start(kit)


def test_owner_start_rate_limit(kit):
    for _ in range(20):
        r = _start(kit)
        kit.sessions.end_call(r["call"]["call_id"])
    with pytest.raises(RateLimited):
        _start(kit)


def test_normal_call_settles_measured_cost_and_cleans_up(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    assert _connect(kit, cid)["continue"] is True
    assert kit.store.get_call(cid).state == CallState.ACTIVE
    kit.clock.advance(120)
    ended = kit.sessions.end_call(cid, EndReason.OWNER_HANGUP, actor="device")
    assert ended.state == CallState.ENDED and ended.duration_s == pytest.approx(120)
    prof = get_profile("gemini-live")
    expected = prof.usd(audio_in_s=10, audio_out_s=5) + kit.cfg.livekit_usd_per_minute * 2
    assert ended.cost_usd == pytest.approx(expected, rel=1e-3) and ended.cost_measured
    assert cid not in kit.transport.rooms                                      # room deleted
    assert kit.cc.get_summary()["reserved_usd"] == 0                          # reservation settled, not leaked
    assert kit.cc.get_summary()["daily_spend_usd"] == pytest.approx(ended.cost_usd, abs=1e-4)
    assert kit.sessions.end_call(cid).state == CallState.ENDED                # idempotent


def test_connected_call_without_usage_is_never_free(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.sessions.agent_heartbeat(kit.store.get_call(cid), None)               # connected, but worker reported nothing
    kit.clock.advance(300)
    ended = kit.sessions.end_call(cid)
    assert ended.cost_usd > 0 and not ended.cost_measured                     # duration-based worst case, capped by reservation
    assert ended.cost_usd <= ended.reserved_usd


def test_abandoned_call_that_never_connected_costs_nothing_but_is_recorded(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.clock.advance(120)                                                    # phone never joined the room
    done = kit.sessions.sweep()
    assert any(d.startswith("connect-timeout") for d in done)
    c = kit.store.get_call(cid)
    assert c.state == CallState.FAILED and c.cost_usd == 0 and c.end_reason == EndReason.NETWORK.value
    assert kit.cc.get_summary()["reserved_usd"] == 0


def test_watchdog_enforces_max_duration_server_side(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    _connect(kit, cid)
    for _ in range(20):                                                       # worker keeps heart-beating; phone stays
        kit.clock.advance(60)
        kit.sessions.agent_heartbeat(kit.store.get_call(cid), None)
        kit.sessions.sweep()
        if kit.store.get_call(cid).state.terminal:
            break
    c = kit.store.get_call(cid)
    assert c.state == CallState.ENDED and c.end_reason == EndReason.MAX_DURATION.value
    assert c.duration_s <= kit.cfg.max_call_seconds + 60
    assert cid not in kit.transport.rooms


def test_budget_cap_ends_the_call(kit):
    update_settings(kit.store, kit.cfg, OWNER, {"per_call_usd": 0.01})
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    _connect(kit, cid)
    kit.clock.advance(60)                                                     # worst case 0.0225+0.001/min > 0.01 cap
    verdict = kit.sessions.agent_heartbeat(kit.store.get_call(cid), {"audio_in_s": 60, "audio_out_s": 60})
    assert verdict == {**verdict, "continue": False, "reason": EndReason.BUDGET.value}
    kit.sessions.sweep()
    assert kit.store.get_call(cid).end_reason == EndReason.BUDGET.value


def test_lost_worker_ends_the_call(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    _connect(kit, cid)
    kit.clock.advance(kit.cfg.heartbeat_stale_seconds + 5)
    assert any(d.startswith("worker-lost") for d in kit.sessions.sweep())
    assert kit.store.get_call(cid).end_reason == EndReason.WORKER_LOST.value


def test_owner_dropping_off_the_network_ends_after_grace(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    _connect(kit, cid)                                                         # device never in the room list
    kit.sessions.sweep()
    kit.clock.advance(70)
    kit.sessions.agent_heartbeat(kit.store.get_call(cid), None)
    assert any(d.startswith("owner-absent") for d in kit.sessions.sweep())


def test_unexpected_participants_are_removed(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    kit.transport.join(cid, "intruder")
    _connect(kit, cid)
    assert "removed:intruder" in kit.sessions.sweep()
    assert "intruder" not in kit.transport.participants[cid]


def test_rejoin_gives_a_fresh_token_only_for_live_calls_of_that_device(kit):
    r = _start(kit)
    cid = r["call"]["call_id"]
    kit.sessions.rejoin(cid, kit.device_id)
    assert len(kit.transport.minted) == 2
    other = kit.devices.register(OWNER, "iPad", kit.pub)
    with pytest.raises(NotFound):
        kit.sessions.rejoin(cid, other["device_id"])
    kit.sessions.end_call(cid)
    with pytest.raises(Conflict):
        kit.sessions.rejoin(cid, kit.device_id)


def test_confirm_connected_is_verified_with_the_transport(kit):
    cid = _start(kit)["call"]["call_id"]
    with pytest.raises(Conflict):
        kit.sessions.confirm_connected(cid, kit.device_id)                    # phone claims it joined; it did not
    kit.transport.join(cid, kit.device_id)
    assert kit.sessions.confirm_connected(cid, kit.device_id)["state"] == "ACTIVE"


def test_agent_credentials_are_per_call_and_signed(kit):
    cid = _start(kit)["call"]["call_id"]
    call = kit.store.get_call(cid)
    token = kit.sessions.agent_token(call)
    assert kit.sessions.verify_agent_token(token).call_id == cid
    with pytest.raises(Forbidden):
        kit.sessions.verify_agent_token(token + "x")
    from services.voicecall.tokens import jwt_hs256
    forged = jwt_hs256({"sub": cid, "scope": "agent", "exp": kit.clock() + 100}, "wrong" * 10)
    with pytest.raises(Forbidden):
        kit.sessions.verify_agent_token(forged)
    wrong_scope = jwt_hs256({"sub": cid, "scope": "owner", "exp": kit.clock() + 100}, kit.cfg.agent_secret.reveal())
    with pytest.raises(Forbidden):
        kit.sessions.verify_agent_token(wrong_scope)


def _costly_call(kit, seconds=60):
    """A call that connects and uses a full minute of listening and speaking (~$0.0235)."""
    cid = _start(kit)["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    kit.sessions.agent_heartbeat(kit.store.get_call(cid), {"audio_in_s": seconds, "audio_out_s": seconds})
    kit.clock.advance(seconds)
    return kit.sessions.end_call(cid)


def test_daily_voice_budget_and_minutes_are_enforced(kit):
    update_settings(kit.store, kit.cfg, OWNER, {"daily_usd": 0.07, "per_call_usd": 0.03})
    assert _costly_call(kit).cost_usd == pytest.approx(0.0235, rel=0.05)
    _costly_call(kit)
    with pytest.raises(VoiceBudgetExceeded):                                   # 0.047 spent + 0.03 reserved > 0.07
        _start(kit)
    update_settings(kit.store, kit.cfg, OWNER, {"daily_usd": 3.0, "per_call_usd": 1.0, "daily_minutes": 1})
    with pytest.raises(VoiceBudgetExceeded):                                   # two minutes already used today
        _start(kit)


def test_global_hood_cap_also_blocks_voice(kit):
    kit.cc.budgets.max_daily_spend_usd = 0.0001
    with pytest.raises(VoiceBudgetExceeded):
        _start(kit)


def test_settings_can_lower_but_never_raise_server_limits(kit):
    from services.voicecall.contracts import VoiceError
    with pytest.raises(VoiceError):
        update_settings(kit.store, kit.cfg, OWNER, {"daily_usd": 999})
    with pytest.raises(VoiceError):
        update_settings(kit.store, kit.cfg, OWNER, {"max_call_seconds": 10 ** 6})
    update_settings(kit.store, kit.cfg, OWNER, {"monthly_usd": 5.0})
    assert kit.cost.limits_for(OWNER)["monthly_usd"] == 5.0


def test_spend_alerts_fire_once_per_threshold(kit):
    alerts = []
    kit.cost.alert_sink = lambda kind, d: alerts.append(d)
    update_settings(kit.store, kit.cfg, OWNER, {"daily_usd": 0.06, "per_call_usd": 0.03})
    _costly_call(kit)                                                          # 39% of the day's cap: no alert
    assert not [a for a in alerts if a["period"] == "day"]
    _costly_call(kit)                                                          # 78%: the 50% threshold alerts, once
    assert [a["threshold"] for a in alerts if a["period"] == "day"] == [0.5]
    kit.cost._alerts(OWNER)                                                    # re-evaluating does not repeat it
    assert [a["threshold"] for a in alerts if a["period"] == "day"] == [0.5]


def test_recovery_settles_calls_left_open_by_a_crash(kit):
    cid = _start(kit)["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    _connect(kit, cid)
    assert kit.sessions.recover() == 1
    assert kit.store.get_call(cid).state.terminal
    assert kit.cc.get_summary()["reserved_usd"] == 0


def test_every_state_change_is_in_the_audit_trail(kit):
    cid = _start(kit)["call"]["call_id"]
    kit.sessions.end_call(cid)
    kinds = [e["kind"] for e in kit.store.events_after(cid)]
    assert kinds[0] == "created" and "state" in kinds and kinds[-1] == "ended"
