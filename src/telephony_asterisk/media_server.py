"""
AudioSocket TCP media server.

This is the process Asterisk's dialplan ``AudioSocket(<uuid>,<host>:<port>)``
application connects to for each call. One TCP connection = one call. On
connect, Asterisk immediately sends a UUID frame identifying the call
(matching the uuid the dialplan was given); everything after that is
bidirectional AUDIO frames until either side sends TERMINATE or closes the
socket.

The UUID alone doesn't carry caller ID or the dialed DID - those come from
the ARI layer (ari_client.py), which captures them from the channel when the
call first hits Stasis() and stores them in ``CallRegistry`` keyed by the
same UUID *before* the dialplan continues on to AudioSocket(). See
DEPLOYMENT_ASTERISK.md for the exact dialplan wiring this expects.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from . import audiosocket
from .agent import CallAgent

logger = logging.getLogger(__name__)


@dataclass
class CallRegistry:
    """Shared, in-process handoff point between the ARI client and the
    AudioSocket server: the ARI client learns a call's caller number and
    dialed DID first (from the Stasis channel) and registers them here under
    the UUID it hands the dialplan; the AudioSocket server looks them up the
    moment that same UUID's TCP connection arrives."""

    _pending: Dict[str, dict] = field(default_factory=dict)

    def register(self, call_uuid: str, caller_number: str,
                dialed_number: Optional[str] = None):
        self._pending[call_uuid] = {
            "caller_number": caller_number,
            "dialed_number": dialed_number,
        }

    def pop(self, call_uuid: str) -> dict:
        """Look up and remove a pending call's metadata. Falls back to an
        anonymous caller if the UUID is unknown (e.g. AudioSocket wired
        directly in the dialplan without the ARI capture step, or a stale
        reconnect) so a call still gets handled instead of dropped."""
        return self._pending.pop(call_uuid, {
            "caller_number": "unknown",
            "dialed_number": None,
        })


AgentFactory = Callable[[], CallAgent]


class AudioSocketServer:
    """asyncio TCP server implementing the AudioSocket protocol, one
    ``CallAgent`` per connection."""

    def __init__(self, agent_factory: AgentFactory, registry: CallRegistry,
                frame_bytes: int = 320, host: str = "0.0.0.0", port: int = 9092):
        """``frame_bytes``: bytes per AUDIO frame in both directions - 320 is
        20ms of 8kHz 16-bit mono slin, the classic PSTN call rate. Must match
        the sample rate the agent's STT/TTS/VAD are configured for."""
        self.agent_factory = agent_factory
        self.registry = registry
        self.frame_bytes = frame_bytes
        self.host = host
        self.port = port
        self._server: Optional[asyncio.base_events.Server] = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle_connection,
                                                   self.host, self.port)
        logger.info(f"AudioSocket server listening on {self.host}:{self.port}")
        return self._server

    async def serve_forever(self):
        await self.start()
        async with self._server:
            await self._server.serve_forever()

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                 writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        logger.info(f"AudioSocket connection from {peer}")
        try:
            await self._run_call(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            logger.info(f"AudioSocket connection from {peer} closed by peer")
        except audiosocket.FrameDecodeError as exc:
            logger.warning(f"Malformed AudioSocket frame from {peer}: {exc}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass

    async def _run_call(self, reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter):
        first = await audiosocket.read_frame(reader)
        if first.type != audiosocket.FrameType.UUID:
            logger.warning(
                f"Expected UUID as the first AudioSocket frame, got {first.type!r}"
            )
            return

        call_uuid = first.uuid_hex
        meta = self.registry.pop(call_uuid)
        logger.info(f"Call {call_uuid}: caller={meta['caller_number']} "
                   f"dialed={meta['dialed_number']}")

        agent = self.agent_factory()
        reply = await agent.start_call(call_uuid, meta["caller_number"],
                                       meta["dialed_number"])
        await self._send_audio(writer, reply.audio)
        if reply.hangup:
            await self._terminate(writer)
            return

        while True:
            frame = await audiosocket.read_frame(reader)
            if frame.type == audiosocket.FrameType.TERMINATE:
                logger.info(f"Call {call_uuid}: caller hung up")
                return
            if frame.type != audiosocket.FrameType.AUDIO:
                continue  # ignore DTMF/error frames for now

            reply = await agent.push_audio(frame.payload)
            if reply is None:
                continue  # still listening for the rest of this utterance

            await self._send_audio(writer, reply.audio)
            if reply.hangup:
                await self._terminate(writer)
                return

    async def _send_audio(self, writer: asyncio.StreamWriter, pcm: bytes):
        if not pcm:
            return
        for chunk in audiosocket.iter_audio_chunks(pcm, self.frame_bytes):
            writer.write(audiosocket.audio_frame(chunk).encode())
        await writer.drain()

    async def _terminate(self, writer: asyncio.StreamWriter):
        try:
            writer.write(audiosocket.terminate_frame().encode())
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
