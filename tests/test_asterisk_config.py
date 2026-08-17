"""
Tests for TelephonyConfig.from_env() and the STT/TTS/VAD provider factories
(telephony_asterisk/config.py). Verifies environment parsing and that
misconfiguration (missing API keys, unknown provider names) fails fast with
a clear error rather than a confusing traceback deep in a provider class.
"""

import pytest

from telephony_asterisk.config import TelephonyConfig, build_stt, build_tts, build_vad
from telephony_asterisk.stt import FakeSTT, DeepgramSTT
from telephony_asterisk.tts import FakeTTS, PiperTTS
from telephony_asterisk.vad import EnergyVAD, WebRtcVAD


def test_defaults_are_sane():
    config = TelephonyConfig()
    assert config.audiosocket_port == 9092
    assert config.sample_rate == 8000
    assert config.frame_bytes == 320  # 8000Hz * 20ms/1000 * 2 bytes


def test_frame_bytes_scales_with_sample_rate_and_frame_ms():
    config = TelephonyConfig(sample_rate=16000, frame_ms=20)
    assert config.frame_bytes == 640


def test_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("AUDIOSOCKET_PORT", "9999")
    monkeypatch.setenv("STT_PROVIDER", "fake")
    monkeypatch.setenv("ARI_USERNAME", "myuser")
    config = TelephonyConfig.from_env()
    assert config.audiosocket_port == 9999
    assert config.stt_provider == "fake"
    assert config.ari_username == "myuser"


def test_from_env_falls_back_to_defaults_when_unset(monkeypatch):
    for var in ("AUDIOSOCKET_PORT", "STT_PROVIDER", "TTS_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    config = TelephonyConfig.from_env()
    assert config.audiosocket_port == 9092
    assert config.stt_provider == "deepgram"
    assert config.tts_provider == "polly"


# -- build_stt ---------------------------------------------------------

def test_build_stt_fake():
    config = TelephonyConfig(stt_provider="fake")
    assert isinstance(build_stt(config), FakeSTT)


def test_build_stt_deepgram_requires_api_key():
    config = TelephonyConfig(stt_provider="deepgram", deepgram_api_key=None)
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        build_stt(config)


def test_build_stt_deepgram_with_key_succeeds():
    config = TelephonyConfig(stt_provider="deepgram", deepgram_api_key="dg-key")
    stt = build_stt(config)
    assert isinstance(stt, DeepgramSTT)
    assert stt.api_key == "dg-key"


def test_build_stt_unknown_provider_raises():
    config = TelephonyConfig(stt_provider="carrier-pigeon")
    with pytest.raises(RuntimeError, match="Unknown STT_PROVIDER"):
        build_stt(config)


# -- build_tts ---------------------------------------------------------

def test_build_tts_fake():
    config = TelephonyConfig(tts_provider="fake")
    assert isinstance(build_tts(config), FakeTTS)


def test_build_tts_piper_requires_model_path():
    config = TelephonyConfig(tts_provider="piper", piper_model_path=None)
    with pytest.raises(RuntimeError, match="PIPER_MODEL_PATH"):
        build_tts(config)


def test_build_tts_piper_with_path_succeeds():
    config = TelephonyConfig(tts_provider="piper", piper_model_path="/models/en.onnx")
    tts = build_tts(config)
    assert isinstance(tts, PiperTTS)
    assert tts.model_path == "/models/en.onnx"


def test_build_tts_unknown_provider_raises():
    config = TelephonyConfig(tts_provider="carrier-pigeon")
    with pytest.raises(RuntimeError, match="Unknown TTS_PROVIDER"):
        build_tts(config)


# -- build_vad -----------------------------------------------------------

def test_build_vad_energy_default():
    config = TelephonyConfig(vad_backend="energy", vad_energy_threshold=500)
    vad = build_vad(config)
    assert isinstance(vad, EnergyVAD)
    assert vad.energy_threshold == 500


def test_build_vad_webrtc_when_available_or_skipped_if_not_installed():
    config = TelephonyConfig(vad_backend="webrtc")
    try:
        vad = build_vad(config)
    except ImportError:
        pytest.skip("webrtcvad not installed in this environment")
    else:
        assert isinstance(vad, WebRtcVAD)
