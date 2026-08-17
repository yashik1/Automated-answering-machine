"""
AudioSocket wire protocol.

AudioSocket is Asterisk's simple TCP protocol for streaming raw call audio
to an external process (``res_audiosocket`` / the dialplan ``AudioSocket()``
application). Each frame is a 3-byte header (1-byte type + 2-byte big-endian
length) followed by that many bytes of payload.

Frame types (confirmed against the Asterisk docs and community protocol
write-ups — verify against your installed Asterisk version if you see
unexpected bytes, as this has evolved slightly across releases):

    0x01  UUID       16-byte call UUID, sent once when the connection opens.
    0x10  AUDIO      Signed 16-bit linear PCM (slin), mono. Classic 8 kHz
                      calls use exactly 320 bytes (20ms) per frame; Asterisk
                      versions with 16kHz/32kHz slin support send larger
                      frames at the same 20ms cadence - do not hardcode 320.
    0x03  DTMF       1-byte payload: ASCII digit (0-9, *, #, A-D).
    0x00  TERMINATE  Empty payload (or the connection is simply closed).
    0xFF  ERROR       Payload is an application-specific error code.

This module only implements the framing - it has no dependency on Asterisk,
sockets, or any AI provider, which makes it fully unit-testable offline.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum


class FrameType(IntEnum):
    TERMINATE = 0x00
    UUID = 0x01
    DTMF = 0x03
    AUDIO = 0x10
    ERROR = 0xFF


_HEADER = struct.Struct(">BH")  # 1-byte type, 2-byte big-endian length
MAX_PAYLOAD = 0xFFFF


@dataclass
class AudioSocketFrame:
    type: FrameType
    payload: bytes = b""

    def encode(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(
                f"AudioSocket payload too large: {len(self.payload)} bytes "
                f"(max {MAX_PAYLOAD})"
            )
        return _HEADER.pack(self.type, len(self.payload)) + self.payload

    @property
    def uuid_hex(self) -> str:
        """For UUID frames: the call UUID as a hyphenated hex string."""
        if self.type != FrameType.UUID or len(self.payload) != 16:
            raise ValueError("not a valid UUID frame")
        h = self.payload.hex()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    @property
    def dtmf_digit(self) -> str:
        if self.type != FrameType.DTMF or len(self.payload) != 1:
            raise ValueError("not a valid DTMF frame")
        return self.payload.decode("ascii")


def audio_frame(pcm: bytes) -> AudioSocketFrame:
    """Build an outgoing AUDIO frame from raw slin PCM bytes."""
    return AudioSocketFrame(FrameType.AUDIO, pcm)


def terminate_frame() -> AudioSocketFrame:
    return AudioSocketFrame(FrameType.TERMINATE, b"")


class FrameDecodeError(Exception):
    """Raised when a malformed header is encountered on the wire."""


async def read_frame(reader) -> AudioSocketFrame:
    """Read exactly one AudioSocket frame from an asyncio.StreamReader.

    Raises ``asyncio.IncompleteReadError`` if the peer closes mid-frame (the
    caller should treat that the same as a TERMINATE), or ``FrameDecodeError``
    for a header that doesn't parse.
    """
    header = await reader.readexactly(_HEADER.size)
    try:
        type_byte, length = _HEADER.unpack(header)
        frame_type = FrameType(type_byte)
    except ValueError as exc:
        raise FrameDecodeError(f"bad AudioSocket header {header!r}: {exc}") from exc

    payload = await reader.readexactly(length) if length else b""
    return AudioSocketFrame(frame_type, payload)


def iter_audio_chunks(pcm: bytes, chunk_bytes: int):
    """Split raw PCM into fixed-size chunks suitable for individual AUDIO
    frames (e.g. 320 bytes = 20ms at 8kHz mono 16-bit). The final partial
    chunk, if any, is zero-padded to a full frame so playback timing stays
    consistent."""
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    for i in range(0, len(pcm), chunk_bytes):
        chunk = pcm[i:i + chunk_bytes]
        if len(chunk) < chunk_bytes:
            chunk = chunk + b"\x00" * (chunk_bytes - len(chunk))
        yield chunk
