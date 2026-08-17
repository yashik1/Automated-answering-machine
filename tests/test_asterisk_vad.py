"""
Tests for voice activity detection and turn-taking
(telephony_asterisk/vad.py). Pure logic - no audio hardware, no Asterisk.
"""

import struct

from telephony_asterisk.vad import EnergyVAD, TurnDetector


def _tone_frame(amplitude: int, n_samples: int = 160) -> bytes:
    """A single flat-amplitude 16-bit PCM frame (not a real sine wave, but
    RMS energy only cares about magnitude, which this exercises directly)."""
    return struct.pack(f"<{n_samples}h", *([amplitude] * n_samples))


def _silence_frame(n_samples: int = 160) -> bytes:
    return b"\x00\x00" * n_samples


# -- EnergyVAD --------------------------------------------------------------

def test_energy_vad_flags_loud_frame_as_speech():
    vad = EnergyVAD(energy_threshold=300)
    assert vad.is_speech(_tone_frame(2000)) is True


def test_energy_vad_flags_silence_as_not_speech():
    vad = EnergyVAD(energy_threshold=300)
    assert vad.is_speech(_silence_frame()) is False


def test_energy_vad_empty_frame_is_not_speech():
    assert EnergyVAD().is_speech(b"") is False


# -- TurnDetector -------------------------------------------------------

def _detector(**overrides):
    defaults = dict(vad=EnergyVAD(energy_threshold=300), frame_ms=20,
                    silence_ms_to_end_turn=100, min_speech_ms_to_count=40)
    defaults.update(overrides)
    return TurnDetector(**defaults)


def test_leading_silence_never_ends_a_turn():
    d = _detector()
    for _ in range(50):  # 1 second of pure silence
        assert d.push_frame(_silence_frame()) is False
    assert d.heard_speech is False


def test_turn_ends_after_speech_then_enough_trailing_silence():
    d = _detector()  # needs 40ms speech, then 100ms silence = 2 frames + 5 frames
    assert d.push_frame(_tone_frame(2000)) is False   # 20ms speech
    assert d.push_frame(_tone_frame(2000)) is False   # 40ms speech -> counts
    assert d.heard_speech is True
    assert d.push_frame(_silence_frame()) is False    # 20ms silence
    assert d.push_frame(_silence_frame()) is False    # 40ms silence
    assert d.push_frame(_silence_frame()) is False    # 60ms silence
    assert d.push_frame(_silence_frame()) is False    # 80ms silence
    assert d.push_frame(_silence_frame()) is True     # 100ms silence -> turn ends


def test_brief_pause_mid_sentence_does_not_end_turn():
    """A short pause between words (less than silence_ms_to_end_turn) must
    not be mistaken for the end of the turn - otherwise 'two... margherita'
    would be cut into two separate utterances."""
    d = _detector()
    d.push_frame(_tone_frame(2000))
    d.push_frame(_tone_frame(2000))          # heard_speech = True
    d.push_frame(_silence_frame())           # 20ms pause
    d.push_frame(_silence_frame())           # 40ms pause (< 100ms threshold)
    resumed = d.push_frame(_tone_frame(2000))  # caller keeps talking
    assert resumed is False
    assert d.heard_speech is True


def test_reset_clears_state_for_next_turn():
    d = _detector()
    d.push_frame(_tone_frame(2000))
    d.push_frame(_tone_frame(2000))
    assert d.heard_speech is True
    d.reset()
    assert d.heard_speech is False
    # Leading silence after reset should again be fully ignored.
    assert d.push_frame(_silence_frame()) is False


def test_min_speech_ms_to_count_ignores_a_brief_blip():
    """A single loud frame shorter than min_speech_ms_to_count (e.g. a click
    or line noise burst) shouldn't count as the caller having started
    talking."""
    d = _detector(min_speech_ms_to_count=100)  # needs 5 frames of speech
    d.push_frame(_tone_frame(2000))  # only 20ms - below threshold
    assert d.heard_speech is False
