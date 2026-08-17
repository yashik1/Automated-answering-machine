"""
Voice activity detection: deciding when the caller has stopped talking so we
know when to stop listening and run STT + generate a reply.

This is the "turn-taking" problem Twilio's <Gather> solved for free via
speech_timeout="auto". On our own Asterisk box we do it ourselves by
watching frame energy: once the caller has been quiet for
``silence_ms_to_end_turn`` after some speech was heard, the turn ends.

Two implementations are provided behind the same ``VoiceActivityDetector``
interface:

- ``EnergyVAD`` (default): a small pure-Python RMS-threshold detector. No
  compiled dependencies, so it always installs and is what the test suite
  exercises. Good enough to ship with; tune ``energy_threshold`` for the
  restaurant's real line noise during Phase 4 hardening.
- ``WebRtcVAD`` (optional, better in noisy environments): wraps Google's
  battle-tested WebRTC VAD via the ``webrtcvad`` package, when installed.
"""

import math
import struct
from dataclasses import dataclass, field
from typing import Protocol


class VoiceActivityDetector(Protocol):
    def is_speech(self, pcm_frame: bytes) -> bool:
        """Return True if this single audio frame contains speech."""
        ...


def _rms_16bit(pcm_frame: bytes) -> float:
    """Root-mean-square amplitude of a 16-bit signed little-endian PCM
    frame. Implemented by hand (rather than the stdlib `audioop` module)
    because audioop is deprecated and slated for removal in Python 3.13."""
    n_samples = len(pcm_frame) // 2
    if n_samples == 0:
        return 0.0
    samples = struct.unpack(f"<{n_samples}h", pcm_frame[:n_samples * 2])
    sum_squares = sum(s * s for s in samples)
    return math.sqrt(sum_squares / n_samples)


@dataclass
class EnergyVAD:
    """RMS-energy threshold VAD for 16-bit signed linear PCM frames."""
    energy_threshold: int = 300  # tune against real call audio; see DEPLOYMENT_ASTERISK.md

    def is_speech(self, pcm_frame: bytes) -> bool:
        if not pcm_frame:
            return False
        return _rms_16bit(pcm_frame) >= self.energy_threshold


class WebRtcVAD:
    """Adapter around the optional `webrtcvad` package. Only 10/20/30ms
    frames at 8/16/32/48kHz are valid input, per WebRTC VAD's own contract."""

    def __init__(self, sample_rate: int = 8000, aggressiveness: int = 2):
        import webrtcvad  # optional dependency; raises ImportError if absent
        self._vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate

    def is_speech(self, pcm_frame: bytes) -> bool:
        return self._vad.is_speech(pcm_frame, self.sample_rate)


@dataclass
class TurnDetector:
    """Stateful wrapper that turns a per-frame VAD signal into "the caller's
    turn just ended" events, with hangover so brief pauses mid-sentence don't
    end the turn early."""
    vad: VoiceActivityDetector
    frame_ms: int = 20
    silence_ms_to_end_turn: int = 700
    min_speech_ms_to_count: int = 150

    _speech_ms: int = field(default=0, init=False)
    _silence_ms: int = field(default=0, init=False)
    _heard_speech: bool = field(default=False, init=False)

    def reset(self):
        self._speech_ms = 0
        self._silence_ms = 0
        self._heard_speech = False

    @property
    def heard_speech(self) -> bool:
        """Whether enough speech has been seen since the last reset() for
        this turn to count as more than silence."""
        return self._heard_speech

    def push_frame(self, pcm_frame: bytes) -> bool:
        """Feed one frame; return True exactly when this frame completes a
        turn (i.e. enough trailing silence followed enough real speech)."""
        if self.vad.is_speech(pcm_frame):
            self._speech_ms += self.frame_ms
            self._silence_ms = 0
            if self._speech_ms >= self.min_speech_ms_to_count:
                self._heard_speech = True
            return False

        if not self._heard_speech:
            return False  # ignore leading silence entirely

        self._silence_ms += self.frame_ms
        return self._silence_ms >= self.silence_ms_to_end_turn
