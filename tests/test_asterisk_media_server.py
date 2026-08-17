"""
End-to-end test of the AudioSocket media server: a real asyncio TCP client
plays the part of Asterisk (speaking the actual wire protocol over a real
loopback socket - not mocked), against a real AudioSocketServer wired to a
real CallAgent/ConversationManager/PizzaBot. This is the strongest proof
that the whole self-hosted pipeline (protocol -> VAD -> STT -> order logic
-> TTS -> protocol) works together, short of a live Asterisk box.
"""

import asyncio
import struct
import uuid as uuid_module

import pytest

from main import PizzaBot
from conversation import ConversationManager
from telephony_asterisk import audiosocket
from telephony_asterisk.agent import CallAgent
from telephony_asterisk.media_server import AudioSocketServer, CallRegistry
from telephony_asterisk.stt import FakeSTT
from telephony_asterisk.tts import FakeTTS
from telephony_asterisk.vad import EnergyVAD, TurnDetector

FRAME_MS = 20
SAMPLE_RATE = 8000
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 320 bytes
SAMPLES_PER_FRAME = FRAME_BYTES // 2

DOWNTOWN_NUMBER = "+1 (234) 567-0001"


def _tone_frame(amplitude=2000):
    return struct.pack(f"<{SAMPLES_PER_FRAME}h", *([amplitude] * SAMPLES_PER_FRAME))


def _silence_frame():
    return b"\x00\x00" * SAMPLES_PER_FRAME


class FakeAsteriskClient:
    """A minimal, real TCP client speaking the actual AudioSocket protocol
    against our server - standing in for Asterisk's res_audiosocket."""

    def __init__(self, reader, writer, call_uuid: str):
        self.reader = reader
        self.writer = writer
        self.call_uuid = call_uuid

    @classmethod
    async def connect(cls, host, port, call_uuid: str):
        reader, writer = await asyncio.open_connection(host, port)
        uuid_bytes = uuid_module.UUID(call_uuid).bytes
        writer.write(audiosocket.AudioSocketFrame(audiosocket.FrameType.UUID, uuid_bytes).encode())
        await writer.drain()
        return cls(reader, writer, call_uuid)

    async def send_audio(self, pcm_frame: bytes):
        writer_frame = audiosocket.audio_frame(pcm_frame)
        self.writer.write(writer_frame.encode())
        await self.writer.drain()

    async def send_terminate(self):
        self.writer.write(audiosocket.terminate_frame().encode())
        await self.writer.drain()

    async def recv_reply_audio(self, idle_timeout=0.3, overall_timeout=5.0):
        """Read AUDIO frames until either a TERMINATE frame arrives (the
        server hanging up) or a short idle gap follows at least one frame
        (the server has nothing more to say for this turn - real Asterisk
        doesn't send an explicit end-of-reply marker either, so this mirrors
        how the real protocol is consumed). Always returns (audio, hung_up);
        never None, so callers can safely unpack the result."""
        collected = bytearray()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + overall_timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            wait_time = idle_timeout if collected else remaining
            try:
                frame = await asyncio.wait_for(audiosocket.read_frame(self.reader), wait_time)
            except asyncio.TimeoutError:
                break
            if frame.type == audiosocket.FrameType.TERMINATE:
                return bytes(collected), True
            if frame.type == audiosocket.FrameType.AUDIO:
                collected.extend(frame.payload)
        return bytes(collected), False

    async def close(self):
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass


@pytest.fixture
async def server_setup(tmp_path, unused_tcp_port):
    bot = PizzaBot(str(tmp_path / "media.db"))
    manager = ConversationManager(bot)
    registry = CallRegistry()
    stt = FakeSTT()
    tts = FakeTTS()

    def agent_factory():
        turn_detector = TurnDetector(
            vad=EnergyVAD(energy_threshold=300), frame_ms=FRAME_MS,
            silence_ms_to_end_turn=100, min_speech_ms_to_count=40,
        )
        return CallAgent(manager=manager, stt=stt, tts=tts, turn_detector=turn_detector,
                         sample_rate=SAMPLE_RATE, max_silence_ms_no_speech=100_000)

    server = AudioSocketServer(agent_factory=agent_factory, registry=registry,
                               frame_bytes=FRAME_BYTES, host="127.0.0.1",
                               port=unused_tcp_port)
    await server.start()
    yield server, registry, bot, stt, tts
    await server.close()


@pytest.fixture
def unused_tcp_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_connecting_gets_a_spoken_greeting_back(server_setup):
    server, registry, bot, stt, tts = server_setup
    call_uuid = str(uuid_module.uuid4())
    registry.register(call_uuid, "555-0101", DOWNTOWN_NUMBER)

    client = await FakeAsteriskClient.connect("127.0.0.1", server.port, call_uuid)
    try:
        audio, hung_up = await client.recv_reply_audio()
        assert len(audio) > 0
        assert hung_up is False
        assert "Singh" in tts.calls[0]  # known caller greeted by name
    finally:
        await client.close()


async def test_registry_metadata_reaches_the_conversation(server_setup):
    """Proves the ARI hand-off contract works: caller number + dialed DID
    registered under a UUID *before* the AudioSocket connection arrives are
    correctly picked up and drive location routing / customer lookup."""
    server, registry, bot, stt, tts = server_setup
    call_uuid = str(uuid_module.uuid4())
    registry.register(call_uuid, "555-0102", DOWNTOWN_NUMBER)  # Priya Patel

    client = await FakeAsteriskClient.connect("127.0.0.1", server.port, call_uuid)
    try:
        await client.recv_reply_audio()
        assert "Priya" in tts.calls[0]
    finally:
        await client.close()


async def test_unregistered_uuid_falls_back_to_anonymous_caller(server_setup):
    """A call that reaches AudioSocket without prior ARI registration (e.g.
    AudioSocket wired directly in the dialplan) should still be handled, not
    dropped."""
    server, registry, bot, stt, tts = server_setup
    call_uuid = str(uuid_module.uuid4())  # never registered

    client = await FakeAsteriskClient.connect("127.0.0.1", server.port, call_uuid)
    try:
        audio, hung_up = await client.recv_reply_audio()
        assert len(audio) > 0
        assert hung_up is False
    finally:
        await client.close()


async def test_full_voice_order_over_real_tcp_saves_an_order(server_setup):
    server, registry, bot, stt, tts = server_setup
    stt._script = ["I want to order", "one pepperoni pizza", "that's all", "yes"]
    call_uuid = str(uuid_module.uuid4())
    registry.register(call_uuid, "555-0101", DOWNTOWN_NUMBER)

    client = await FakeAsteriskClient.connect("127.0.0.1", server.port, call_uuid)
    try:
        await client.recv_reply_audio()  # greeting

        hung_up = False
        for _ in range(4):
            for _ in range(3):
                await client.send_audio(_tone_frame())
            for _ in range(6):
                await client.send_audio(_silence_frame())
            _, hung_up = await client.recv_reply_audio()
            if hung_up:
                break

        assert hung_up is True
        order = bot.get_order_status(1)
        assert order is not None
        assert order.total == pytest.approx(14.99)  # Pepperoni Pizza
    finally:
        await client.close()


async def test_caller_hangup_terminate_frame_ends_the_connection_cleanly(server_setup):
    server, registry, bot, stt, tts = server_setup
    call_uuid = str(uuid_module.uuid4())
    registry.register(call_uuid, "555-0101", DOWNTOWN_NUMBER)

    client = await FakeAsteriskClient.connect("127.0.0.1", server.port, call_uuid)
    try:
        await client.recv_reply_audio()
        await client.send_terminate()
        # Server should close its side without raising; give it a beat.
        await asyncio.sleep(0.1)
    finally:
        await client.close()
