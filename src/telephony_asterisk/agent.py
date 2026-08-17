"""
Per-call agent: the Asterisk-side equivalent of app.py's /voice and
/voice/collect webhooks.

This is the only piece that talks to conversation.ConversationManager, and
it does so through the exact same interface Twilio's TwiML layer uses -
``manager.greeting(session)`` and ``manager.handle(session, text)``, both
returning a ``TurnResult(message, expect_reply, hangup)``. Everything in
main.py and conversation.py is unmodified and untouched by this migration.

What Twilio's <Gather input="speech" speech_timeout="auto"> did for free,
this module does explicitly:
  - turn-taking:      vad.TurnDetector (fed frame-by-frame as audio arrives)
  - no-speech timeout: `max_silence_ms_no_speech` - if the caller says
                       nothing at all, we still prompt again rather than
                       hang the call forever waiting for a turn that never
                       completes.
  - runaway safety:    `max_utterance_ms` bounds how long we'll buffer one
                       utterance, and repeated empty turns end the call
                       instead of looping indefinitely.
"""

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from .vad import TurnDetector

if TYPE_CHECKING:
    # src/ is on sys.path when the app runs (see main.py's BASE_DIR handling
    # and pytest.ini's pythonpath=src), so these resolve at type-check time
    # without telephony_asterisk depending on conversation.py at import time.
    from conversation import ConversationManager
    from .stt import SpeechToText
    from .tts import TextToSpeech


@dataclass
class AgentReply:
    """What the media server should do after one step of the call."""
    audio: bytes
    hangup: bool


@dataclass
class CallAgent:
    """Drives one phone call end-to-end. One instance per call; the media
    server (audiosocket.py's server loop) creates one when a new connection
    arrives and discards it when the call ends."""

    manager: "ConversationManager"
    stt: "SpeechToText"
    tts: "TextToSpeech"
    turn_detector: TurnDetector
    sample_rate: int = 8000
    max_silence_ms_no_speech: int = 6000
    max_utterance_ms: int = 15000
    max_consecutive_empty_turns: int = 3

    session: Optional[object] = field(default=None, init=False)
    _utterance: bytearray = field(default_factory=bytearray, init=False)
    _frames_since_turn_start: int = field(default=0, init=False)
    _consecutive_empty_turns: int = field(default=0, init=False)
    ended: bool = field(default=False, init=False)

    async def start_call(self, call_sid: str, caller_number: str,
                         dialed_number: Optional[str] = None) -> AgentReply:
        """Called once when the call connects. Mirrors app.py's POST /voice."""
        self.session = self.manager.get_session(call_sid, caller_number, dialed_number)
        result = self.manager.greeting(self.session)
        self._reset_turn_state()
        return await self._to_reply(result)

    async def push_audio(self, pcm_frame: bytes) -> Optional[AgentReply]:
        """Feed one inbound audio frame (raw slin PCM, `frame_ms` worth of
        samples matching turn_detector.frame_ms). Returns an AgentReply once
        the caller's turn is complete (or a safety timeout fires); otherwise
        None, meaning "keep listening"."""
        if self.ended:
            return None

        self._utterance.extend(pcm_frame)
        self._frames_since_turn_start += 1
        turn_ended = self.turn_detector.push_frame(pcm_frame)

        elapsed_ms = self._elapsed_ms()
        no_speech_timeout = (
            not self.turn_detector.heard_speech
            and elapsed_ms >= self.max_silence_ms_no_speech
        )
        hit_max_length = elapsed_ms >= self.max_utterance_ms

        if not (turn_ended or no_speech_timeout or hit_max_length):
            return None

        return await self._complete_turn()

    async def _complete_turn(self) -> AgentReply:
        utterance_pcm = bytes(self._utterance)
        heard_speech = self.turn_detector.heard_speech
        self._reset_turn_state()

        text = ""
        if heard_speech and utterance_pcm:
            text = await self.stt.transcribe(utterance_pcm, self.sample_rate)

        if not text.strip():
            self._consecutive_empty_turns += 1
        else:
            self._consecutive_empty_turns = 0

        if self._consecutive_empty_turns > self.max_consecutive_empty_turns:
            self.manager.end_session(self.session)
            self.ended = True
            return AgentReply(
                audio=await self.tts.synthesize(
                    "I'm having trouble hearing you. Please call back when "
                    "you're ready to order. Goodbye!", self.sample_rate,
                ),
                hangup=True,
            )

        result = self.manager.handle(self.session, text)
        reply = await self._to_reply(result)
        if not reply.hangup:
            self._reset_turn_state()
        return reply

    async def _to_reply(self, result) -> AgentReply:
        if result.hangup or not result.expect_reply:
            self.ended = True
        audio = (
            await self.tts.synthesize(result.message, self.sample_rate)
            if result.message else b""
        )
        return AgentReply(audio=audio, hangup=self.ended)

    def _reset_turn_state(self):
        """Clear buffered audio and turn-detector state so the next
        push_audio() call starts listening for a fresh utterance."""
        self.turn_detector.reset()
        self._utterance = bytearray()
        self._frames_since_turn_start = 0

    def _elapsed_ms(self) -> float:
        """Logical audio time elapsed since this turn started, derived from
        the number of frames pushed rather than wall-clock time - each frame
        represents a fixed slice of real call audio (frame_ms), so this
        stays correct regardless of how fast our own processing runs, and
        makes the timeouts exercisable in tests without real sleeps."""
        return self._frames_since_turn_start * self.turn_detector.frame_ms
