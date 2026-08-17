"""
Tests for CallAgent (telephony_asterisk/agent.py) - the piece that drives
one phone call by gluing VAD turn-detection, STT, the *real*
ConversationManager/PizzaBot (unmodified from the Twilio integration), and
TTS together. FakeSTT/FakeTTS stand in for the network-calling providers so
these tests run offline and deterministically, while still exercising the
real order-taking logic end-to-end.
"""

import struct

import pytest

from main import PizzaBot
from conversation import ConversationManager
from telephony_asterisk.agent import CallAgent
from telephony_asterisk.stt import FakeSTT
from telephony_asterisk.tts import FakeTTS
from telephony_asterisk.vad import EnergyVAD, TurnDetector

FRAME_MS = 20
SAMPLE_RATE = 8000
SAMPLES_PER_FRAME = int(SAMPLE_RATE * FRAME_MS / 1000)

# PizzaBot seeds two default locations; dialing this number auto-routes the
# call to Downtown (same convention as tests/test_voice.py's DOWNTOWN_NUMBER)
# so these tests can focus on the order flow rather than location selection.
DOWNTOWN_NUMBER = "+1 (234) 567-0001"


def _tone_frame(amplitude: int = 2000) -> bytes:
    return struct.pack(f"<{SAMPLES_PER_FRAME}h", *([amplitude] * SAMPLES_PER_FRAME))


def _silence_frame() -> bytes:
    return b"\x00\x00" * SAMPLES_PER_FRAME


def _make_agent(bot, transcripts=None, **overrides):
    manager = ConversationManager(bot)
    stt = FakeSTT(transcripts or [])
    tts = FakeTTS()
    turn_detector = TurnDetector(
        vad=EnergyVAD(energy_threshold=300), frame_ms=FRAME_MS,
        silence_ms_to_end_turn=overrides.pop("silence_ms_to_end_turn", 100),
        min_speech_ms_to_count=overrides.pop("min_speech_ms_to_count", 40),
    )
    agent = CallAgent(manager=manager, stt=stt, tts=tts, turn_detector=turn_detector,
                      sample_rate=SAMPLE_RATE, **overrides)
    return agent, stt, tts


async def _speak_and_pause(agent, n_speech_frames=3, n_silence_frames=6):
    """Push frames simulating one spoken utterance followed by enough
    silence to end the turn; return the reply from whichever push_audio call
    completes the turn (None if it never completes, which is a test bug)."""
    for _ in range(n_speech_frames):
        result = await agent.push_audio(_tone_frame())
        assert result is None
    for _ in range(n_silence_frames):
        result = await agent.push_audio(_silence_frame())
        if result is not None:
            return result
    return None


@pytest.fixture
def bot(tmp_path):
    return PizzaBot(str(tmp_path / "agent.db"))


# -- basic call flow ---------------------------------------------------

async def test_start_call_greets_and_speaks_via_tts(bot):
    agent, stt, tts = _make_agent(bot)
    reply = await agent.start_call("CALL1", "555-0101", DOWNTOWN_NUMBER)
    assert reply.hangup is False
    assert len(tts.calls) == 1
    assert "Singh" in tts.calls[0]  # known caller greeted by name
    assert len(reply.audio) > 0     # FakeTTS returns non-empty PCM for non-empty text


async def test_full_order_flow_reaches_conversation_manager_and_saves_order(bot):
    agent, stt, tts = _make_agent(bot, transcripts=[
        "I want to order", "one margherita pizza", "that's all", "yes",
    ])
    await agent.start_call("CALL2", "555-0101", DOWNTOWN_NUMBER)

    for _ in range(4):
        reply = await _speak_and_pause(agent)
        assert reply is not None

    assert reply.hangup is True
    assert "order number" in tts.calls[-1].lower()

    order = bot.get_order_status(1)
    assert order is not None
    assert order.total == pytest.approx(12.99)


async def test_stt_receives_the_buffered_utterance_audio(bot):
    """Confirms real audio bytes (not just silence-trimmed nothing) are
    handed to STT - i.e. the agent is actually buffering caller speech."""
    agent, stt, tts = _make_agent(bot, transcripts=["what's on the menu"])
    await agent.start_call("CALL3", "555-0101", DOWNTOWN_NUMBER)
    await _speak_and_pause(agent, n_speech_frames=5)
    assert len(stt.calls) == 1
    pcm_len, sample_rate = stt.calls[0]
    assert sample_rate == SAMPLE_RATE
    assert pcm_len > 0


# -- safety timeouts (the things Twilio's Gather timeout used to handle) --

async def test_no_speech_timeout_reprompts_instead_of_hanging_forever(bot):
    agent, stt, tts = _make_agent(bot, max_silence_ms_no_speech=200)
    await agent.start_call("CALL4", "555-0101", DOWNTOWN_NUMBER)
    tts.calls.clear()

    reply = None
    for _ in range(20):  # well past the 200ms no-speech timeout
        reply = await agent.push_audio(_silence_frame())
        if reply is not None:
            break

    assert reply is not None
    assert reply.hangup is False   # reprompts, doesn't just die
    assert len(stt.calls) == 0     # never even attempted STT on pure silence


async def test_max_utterance_length_forces_a_turn_even_without_silence(bot):
    """A caller who never stops talking (or line noise VAD mistakes for
    speech) must not buffer forever."""
    agent, stt, tts = _make_agent(bot, transcripts=["long order"],
                                  max_utterance_ms=200)
    await agent.start_call("CALL5", "555-0101", DOWNTOWN_NUMBER)

    reply = None
    for _ in range(30):  # keep "talking" well past the 200ms cap
        reply = await agent.push_audio(_tone_frame())
        if reply is not None:
            break

    assert reply is not None
    assert len(stt.calls) == 1  # forced turn still got transcribed


async def test_repeated_empty_turns_end_the_call(bot):
    agent, stt, tts = _make_agent(bot, max_silence_ms_no_speech=100,
                                  max_consecutive_empty_turns=2)
    await agent.start_call("CALL6", "555-0101", DOWNTOWN_NUMBER)

    last_reply = None
    for _turn in range(4):
        for _ in range(20):
            last_reply = await agent.push_audio(_silence_frame())
            if last_reply is not None:
                break
        if last_reply and last_reply.hangup:
            break

    assert last_reply is not None
    assert last_reply.hangup is True
    assert agent.ended is True


async def test_a_real_utterance_resets_the_empty_turn_counter(bot):
    """One real answer after some silence shouldn't count toward the
    hang-up-on-silence limit."""
    agent, stt, tts = _make_agent(
        bot, transcripts=["what are your hours"],
        max_silence_ms_no_speech=100, max_consecutive_empty_turns=2,
    )
    await agent.start_call("CALL7", "555-0101", DOWNTOWN_NUMBER)

    for _ in range(20):  # one empty (silent) turn
        r = await agent.push_audio(_silence_frame())
        if r is not None:
            break
    assert agent.ended is False

    reply = await _speak_and_pause(agent)  # then a real, understood turn
    assert reply is not None
    assert reply.hangup is False
    assert agent._consecutive_empty_turns == 0
