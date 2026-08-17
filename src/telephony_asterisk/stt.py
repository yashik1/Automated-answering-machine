"""
Speech-to-text providers.

Twilio's <Gather input="speech"> gave us transcription for free; on our own
Asterisk box we call an STT provider ourselves. The VAD-driven turn detector
(``vad.TurnDetector``) already buffers one caller utterance at a time, so
this module transcribes complete utterances rather than a live stream -
simpler and just as accurate for order-taking, at the cost of a bit more
latency than true incremental streaming. If sub-300ms responsiveness turns
out to matter once this is live, swap in Deepgram's streaming WebSocket API
using the same ``SpeechToText`` interface.

``DeepgramSTT`` is the recommended default (see DEPLOYMENT_ASTERISK.md for
account setup). The HTTP call is a thin, injectable method
(``_post_audio``) specifically so tests can verify the request is built
correctly without hitting the network or requiring a live API key.
"""

import json
from typing import Optional, Protocol


class SpeechToText(Protocol):
    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Transcribe one complete utterance of raw 16-bit signed linear PCM
        (mono) and return the recognized text (empty string if nothing was
        understood)."""
        ...


class FakeSTT:
    """Deterministic STT for local development and tests: returns
    pre-scripted transcripts in order, one per call, regardless of the audio
    given. Lets the rest of the pipeline (VAD -> agent -> conversation -> TTS)
    be exercised without any real speech engine or network access."""

    def __init__(self, scripted_transcripts: Optional[list] = None):
        self._script = list(scripted_transcripts or [])
        self.calls = []  # (pcm_len, sample_rate) for each call, for assertions

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        self.calls.append((len(pcm), sample_rate))
        if self._script:
            return self._script.pop(0)
        return ""


class DeepgramSTT:
    """Deepgram's prerecorded/batch REST endpoint, fed one buffered
    utterance at a time. https://developers.deepgram.com/reference/listen-file
    """

    API_URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, model: str = "nova-2-phonecall",
                 language: str = "en-US", session=None):
        if not api_key:
            raise ValueError("DeepgramSTT requires an API key (DEEPGRAM_API_KEY)")
        self.api_key = api_key
        self.model = model
        self.language = language
        self._session = session  # optional injected aiohttp.ClientSession

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if not pcm:
            return ""
        body = await self._post_audio(pcm, sample_rate)
        return self._extract_transcript(body)

    async def _post_audio(self, pcm: bytes, sample_rate: int) -> dict:
        """Issue the HTTP request and return the parsed JSON body. Split out
        from transcribe() so tests can monkeypatch just this method and
        assert on the URL/headers/params without a real network call."""
        import aiohttp

        params = {
            "encoding": "linear16",
            "sample_rate": str(sample_rate),
            "channels": "1",
            "model": self.model,
            "language": self.language,
            "punctuate": "true",
        }
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/raw",
        }
        session = self._session
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()
        try:
            async with session.post(self.API_URL, params=params, headers=headers,
                                    data=pcm) as resp:
                resp.raise_for_status()
                return await resp.json()
        finally:
            if owns_session:
                await session.close()

    @staticmethod
    def _extract_transcript(body: dict) -> str:
        try:
            return body["results"]["channels"][0]["alternatives"][0]["transcript"] or ""
        except (KeyError, IndexError, TypeError):
            return ""


class GoogleSTT:
    """Google Cloud Speech-to-Text v1 REST, as an alternative to Deepgram.
    Requires the `google-cloud-speech` package or a bare API key + REST call;
    implemented here as a plain REST call (api_key) to avoid pulling in the
    full google-cloud SDK as a hard dependency."""

    API_URL = "https://speech.googleapis.com/v1/speech:recognize"

    def __init__(self, api_key: str, language_code: str = "en-US", session=None):
        if not api_key:
            raise ValueError("GoogleSTT requires an API key (GOOGLE_STT_API_KEY)")
        self.api_key = api_key
        self.language_code = language_code
        self._session = session

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if not pcm:
            return ""
        body = await self._post_audio(pcm, sample_rate)
        return self._extract_transcript(body)

    async def _post_audio(self, pcm: bytes, sample_rate: int) -> dict:
        import base64
        import aiohttp

        payload = {
            "config": {
                "encoding": "LINEAR16",
                "sampleRateHertz": sample_rate,
                "languageCode": self.language_code,
                "model": "phone_call",
            },
            "audio": {"content": base64.b64encode(pcm).decode("ascii")},
        }
        session = self._session
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()
        try:
            async with session.post(
                self.API_URL, params={"key": self.api_key},
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        finally:
            if owns_session:
                await session.close()

    @staticmethod
    def _extract_transcript(body: dict) -> str:
        try:
            results = body.get("results", [])
            return " ".join(
                r["alternatives"][0]["transcript"] for r in results if r.get("alternatives")
            )
        except (KeyError, IndexError, TypeError):
            return ""
