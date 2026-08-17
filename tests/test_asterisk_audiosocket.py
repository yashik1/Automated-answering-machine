"""
Tests for the AudioSocket wire protocol (telephony_asterisk/audiosocket.py).
Pure protocol framing - no sockets, no Asterisk, no network.
"""

import asyncio

import pytest

from telephony_asterisk import audiosocket
from telephony_asterisk.audiosocket import AudioSocketFrame, FrameType


def test_encode_audio_frame_header():
    frame = audiosocket.audio_frame(b"\x01\x02" * 160)  # 320 bytes
    encoded = frame.encode()
    assert encoded[0] == FrameType.AUDIO
    assert encoded[1:3] == (320).to_bytes(2, "big")
    assert encoded[3:] == b"\x01\x02" * 160


def test_encode_terminate_frame_is_empty_payload():
    encoded = audiosocket.terminate_frame().encode()
    assert encoded == bytes([FrameType.TERMINATE, 0, 0])


def test_encode_rejects_oversized_payload():
    with pytest.raises(ValueError):
        AudioSocketFrame(FrameType.AUDIO, b"\x00" * (audiosocket.MAX_PAYLOAD + 1)).encode()


def test_uuid_hex_formats_as_hyphenated():
    raw = bytes.fromhex("12345678123412341234123456789abc")[:16]
    frame = AudioSocketFrame(FrameType.UUID, raw)
    hexed = frame.uuid_hex
    assert len(hexed) == 36
    assert hexed.count("-") == 4


def test_dtmf_digit_decodes_ascii():
    frame = AudioSocketFrame(FrameType.DTMF, b"5")
    assert frame.dtmf_digit == "5"


@pytest.mark.asyncio
async def test_read_frame_round_trips_through_a_real_stream():
    """Encode several frames, feed them through a real asyncio pipe (not a
    mock), and confirm read_frame decodes them back correctly and in order -
    this is what Asterisk's TCP connection looks like from our side."""
    frames = [
        AudioSocketFrame(FrameType.UUID, bytes(range(16))),
        audiosocket.audio_frame(b"\x00\x01" * 160),
        AudioSocketFrame(FrameType.DTMF, b"#"),
        audiosocket.terminate_frame(),
    ]
    wire = b"".join(f.encode() for f in frames)

    reader = asyncio.StreamReader()
    reader.feed_data(wire)
    reader.feed_eof()

    decoded = []
    for _ in frames:
        decoded.append(await audiosocket.read_frame(reader))

    assert [f.type for f in decoded] == [f.type for f in frames]
    assert [f.payload for f in decoded] == [f.payload for f in frames]


@pytest.mark.asyncio
async def test_read_frame_raises_on_truncated_stream():
    reader = asyncio.StreamReader()
    reader.feed_data(bytes([FrameType.AUDIO, 0, 10]) + b"\x00" * 3)  # says 10, gives 3
    reader.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError):
        await audiosocket.read_frame(reader)


def test_iter_audio_chunks_splits_evenly():
    pcm = b"\x01" * 640
    chunks = list(audiosocket.iter_audio_chunks(pcm, 320))
    assert len(chunks) == 2
    assert all(len(c) == 320 for c in chunks)


def test_iter_audio_chunks_pads_final_partial_chunk():
    pcm = b"\x01" * 500
    chunks = list(audiosocket.iter_audio_chunks(pcm, 320))
    assert len(chunks) == 2
    assert len(chunks[-1]) == 320
    assert chunks[-1][180:] == b"\x00" * 140  # padded tail is zero (silence)


def test_iter_audio_chunks_rejects_non_positive_chunk_size():
    with pytest.raises(ValueError):
        list(audiosocket.iter_audio_chunks(b"\x01\x02", 0))
