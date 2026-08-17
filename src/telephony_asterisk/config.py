"""
Environment-driven configuration for the Asterisk telephony transport.

Mirrors the pattern used by the project's root config.py and src/app.py:
plain os.getenv() reads with sensible defaults, gathered into one object so
server.py has a single source of truth. See DEPLOYMENT_ASTERISK.md for what
each variable maps to on the FreePBX/Asterisk side.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class TelephonyConfig:
    # AudioSocket media server (Asterisk's AudioSocket() dialplan app connects here)
    audiosocket_host: str = "0.0.0.0"
    audiosocket_port: int = 9092
    sample_rate: int = 8000
    frame_ms: int = 20

    # ARI (captures caller ID + dialed DID, routes calls, drives handoff)
    ari_base_url: str = "http://127.0.0.1:8088/ari"
    ari_ws_url: str = "ws://127.0.0.1:8088/ari/events"
    ari_username: str = "pizzabot"
    ari_password: str = ""
    ari_app_name: str = "pizzabot"
    dialplan_context: str = "ai-order-media"
    dialplan_extension: str = "s"
    did_variable: str = "FROM_DID"

    # Human handoff (Phase 3) - a FreePBX ring group/extension to redirect to
    # when the caller asks for a person. Leave unset to disable handoff.
    handoff_endpoint: Optional[str] = None

    # Speech providers
    stt_provider: str = "deepgram"   # deepgram | google | fake
    tts_provider: str = "polly"       # polly | piper | fake
    deepgram_api_key: Optional[str] = None
    deepgram_model: str = "nova-2-phonecall"
    google_stt_api_key: Optional[str] = None
    polly_region: str = "us-east-1"
    polly_voice_id: str = "Joanna"
    piper_model_path: Optional[str] = None

    # VAD / turn detection
    vad_backend: str = "energy"       # energy | webrtc
    vad_energy_threshold: int = 300
    vad_aggressiveness: int = 2
    silence_ms_to_end_turn: int = 700
    min_speech_ms_to_count: int = 150
    max_silence_ms_no_speech: int = 6000
    max_utterance_ms: int = 15000

    @classmethod
    def from_env(cls) -> "TelephonyConfig":
        defaults = cls()

        def _int(name, default):
            return int(os.getenv(name, str(default)))

        return cls(
            audiosocket_host=os.getenv("AUDIOSOCKET_HOST", defaults.audiosocket_host),
            audiosocket_port=_int("AUDIOSOCKET_PORT", defaults.audiosocket_port),
            sample_rate=_int("TELEPHONY_SAMPLE_RATE", defaults.sample_rate),
            frame_ms=_int("TELEPHONY_FRAME_MS", defaults.frame_ms),
            ari_base_url=os.getenv("ARI_BASE_URL", defaults.ari_base_url),
            ari_ws_url=os.getenv("ARI_WS_URL", defaults.ari_ws_url),
            ari_username=os.getenv("ARI_USERNAME", defaults.ari_username),
            ari_password=os.getenv("ARI_PASSWORD", defaults.ari_password),
            ari_app_name=os.getenv("ARI_APP_NAME", defaults.ari_app_name),
            dialplan_context=os.getenv("AUDIOSOCKET_DIALPLAN_CONTEXT", defaults.dialplan_context),
            dialplan_extension=os.getenv("AUDIOSOCKET_DIALPLAN_EXTENSION", defaults.dialplan_extension),
            did_variable=os.getenv("ARI_DID_VARIABLE", defaults.did_variable),
            handoff_endpoint=os.getenv("HANDOFF_ENDPOINT") or None,
            stt_provider=os.getenv("STT_PROVIDER", defaults.stt_provider),
            tts_provider=os.getenv("TTS_PROVIDER", defaults.tts_provider),
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY") or None,
            deepgram_model=os.getenv("DEEPGRAM_MODEL", defaults.deepgram_model),
            google_stt_api_key=os.getenv("GOOGLE_STT_API_KEY") or None,
            polly_region=os.getenv("POLLY_REGION", defaults.polly_region),
            polly_voice_id=os.getenv("POLLY_VOICE_ID", defaults.polly_voice_id),
            piper_model_path=os.getenv("PIPER_MODEL_PATH") or None,
            vad_backend=os.getenv("VAD_BACKEND", defaults.vad_backend),
            vad_energy_threshold=_int("VAD_ENERGY_THRESHOLD", defaults.vad_energy_threshold),
            vad_aggressiveness=_int("VAD_AGGRESSIVENESS", defaults.vad_aggressiveness),
            silence_ms_to_end_turn=_int("SILENCE_MS_TO_END_TURN", defaults.silence_ms_to_end_turn),
            min_speech_ms_to_count=_int("MIN_SPEECH_MS_TO_COUNT", defaults.min_speech_ms_to_count),
            max_silence_ms_no_speech=_int("MAX_SILENCE_MS_NO_SPEECH", defaults.max_silence_ms_no_speech),
            max_utterance_ms=_int("MAX_UTTERANCE_MS", defaults.max_utterance_ms),
        )

    @property
    def frame_bytes(self) -> int:
        """Bytes per AUDIO frame: sample_rate * frame_ms/1000 samples * 2 bytes (16-bit)."""
        return int(self.sample_rate * self.frame_ms / 1000) * 2


def build_stt(config: TelephonyConfig):
    """Instantiate the configured STT provider. Raises a clear error rather
    than a bare KeyError/ImportError trace if required credentials or an
    optional dependency are missing."""
    from . import stt as stt_module

    if config.stt_provider == "fake":
        return stt_module.FakeSTT()
    if config.stt_provider == "deepgram":
        if not config.deepgram_api_key:
            raise RuntimeError(
                "STT_PROVIDER=deepgram requires DEEPGRAM_API_KEY to be set"
            )
        return stt_module.DeepgramSTT(config.deepgram_api_key, model=config.deepgram_model)
    if config.stt_provider == "google":
        if not config.google_stt_api_key:
            raise RuntimeError(
                "STT_PROVIDER=google requires GOOGLE_STT_API_KEY to be set"
            )
        return stt_module.GoogleSTT(config.google_stt_api_key)
    raise RuntimeError(f"Unknown STT_PROVIDER: {config.stt_provider!r}")


def build_tts(config: TelephonyConfig):
    """Instantiate the configured TTS provider. See build_stt() for the
    fail-fast rationale."""
    from . import tts as tts_module

    if config.tts_provider == "fake":
        return tts_module.FakeTTS()
    if config.tts_provider == "polly":
        return tts_module.PollyTTS.from_default_credentials(
            region_name=config.polly_region, voice_id=config.polly_voice_id,
        )
    if config.tts_provider == "piper":
        if not config.piper_model_path:
            raise RuntimeError("TTS_PROVIDER=piper requires PIPER_MODEL_PATH to be set")
        return tts_module.PiperTTS(config.piper_model_path)
    raise RuntimeError(f"Unknown TTS_PROVIDER: {config.tts_provider!r}")


def build_vad(config: TelephonyConfig):
    """Instantiate the configured VAD backend."""
    from . import vad as vad_module

    if config.vad_backend == "webrtc":
        return vad_module.WebRtcVAD(sample_rate=config.sample_rate,
                                    aggressiveness=config.vad_aggressiveness)
    return vad_module.EnergyVAD(energy_threshold=config.vad_energy_threshold)
