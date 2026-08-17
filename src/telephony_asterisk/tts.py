"""
Text-to-speech providers.

Twilio's <Say> gave us TTS for free; here we synthesize the bot's reply
ourselves and stream it back as raw PCM over AudioSocket. All providers
return audio already resampled/encoded as 16-bit signed linear PCM mono at
the call's sample rate (8kHz for a classic PSTN call) so the agent loop can
hand it straight to ``audiosocket.iter_audio_chunks`` without extra
conversion.
"""

import io
from typing import Optional, Protocol


class TextToSpeech(Protocol):
    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        """Return raw 16-bit signed linear PCM (mono, `sample_rate` Hz) audio
        for the given text."""
        ...


class FakeTTS:
    """Deterministic TTS for local development and tests: returns a short
    burst of silence sized proportionally to the text length, so the media
    server's chunking/streaming path can be exercised without any real
    speech engine, audio codec, or network access."""

    def __init__(self, ms_per_char: int = 40):
        self.ms_per_char = ms_per_char
        self.calls = []  # texts requested, for assertions

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        self.calls.append(text)
        duration_ms = max(20, len(text) * self.ms_per_char)
        n_samples = int(sample_rate * duration_ms / 1000)
        return b"\x00\x00" * n_samples  # silence: 2 bytes/sample, 16-bit


class PollyTTS:
    """Amazon Polly neural voices. The boto3 client is injectable so tests
    can supply a fake without AWS credentials or the boto3 package present;
    in production, pass a real `boto3.client("polly", region_name=...)`.
    """

    def __init__(self, client, voice_id: str = "Joanna", engine: str = "neural"):
        self._client = client
        self.voice_id = voice_id
        self.engine = engine

    @classmethod
    def from_default_credentials(cls, region_name: str = "us-east-1",
                                 voice_id: str = "Joanna", engine: str = "neural"):
        """Convenience constructor for production use: builds a real boto3
        Polly client from the environment's AWS credentials."""
        import boto3
        return cls(boto3.client("polly", region_name=region_name),
                   voice_id=voice_id, engine=engine)

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        # boto3 is synchronous; Polly calls are short (order-reply length
        # text), so run it directly rather than pulling in a thread executor
        # for this first cut. If latency profiling in Phase 4 shows this
        # blocking the event loop matters, wrap with
        # asyncio.get_event_loop().run_in_executor(...).
        response = self._synthesize_sync(text, sample_rate)
        return response.read() if hasattr(response, "read") else response

    def _synthesize_sync(self, text: str, sample_rate: int):
        result = self._client.synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            SampleRate=str(sample_rate),
            VoiceId=self.voice_id,
            Engine=self.engine,
        )
        return result["AudioStream"]


class PiperTTS:
    """Offline neural TTS via Piper (https://github.com/rhasspy/piper) - no
    per-character fee, no network round-trip, runs entirely on your own
    server. Use this for the "full on-prem control" path once cloud TTS
    quality/cost has been validated. Requires the `piper-tts` package and a
    downloaded voice model (.onnx); invoked as a subprocess so the model
    stays out of this process's memory unless actually in use.
    """

    def __init__(self, model_path: str, piper_binary: str = "piper"):
        self.model_path = model_path
        self.piper_binary = piper_binary

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            self.piper_binary, "--model", self.model_path,
            "--output-raw", "--sample-rate", str(sample_rate),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(text.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(f"piper TTS failed: {stderr.decode(errors='replace')}")
        return stdout
