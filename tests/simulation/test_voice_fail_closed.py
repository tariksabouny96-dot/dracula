import pytest
from services.voice.adapters import GeminiLiveVoiceAdapter
from services.voice.voice_router import VoiceRouter


def test_voice_adapter_cannot_fabricate_recognized_speech():
    adapter = GeminiLiveVoiceAdapter()
    sid = adapter.start_session('fixture-voice')
    with pytest.raises(NotImplementedError, match='no audio was transcribed'):
        adapter.transcribe(b'\x01\x02', sid)
    with pytest.raises(NotImplementedError, match='no speech was generated'):
        adapter.speak('hello', sid)
    adapter.close_session(sid)


def test_router_reports_unimplemented_voices_without_connecting():
    router = VoiceRouter()
    capabilities = {x['provider']: x for x in router.get_provider_capabilities()}
    assert capabilities['GEMINI_LIVE']['availability'] == 'NOT_IMPLEMENTED'
    assert capabilities['GEMINI_LIVE']['cost_class'] == 'UNVERIFIED'
    assert capabilities['LOCAL']['availability'] == 'WAKE_VAD_ONLY_NO_STT_TTS'
    router.start_voice_session('sim')
    with pytest.raises(NotImplementedError):
        router.transcribe_audio(b'audio', 'sim')
    with pytest.raises(NotImplementedError):
        router.speak('hello', 'sim')
    router.end_voice_session('sim')
