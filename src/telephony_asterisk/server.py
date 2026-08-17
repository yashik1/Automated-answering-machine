#!/usr/bin/env python3
"""
Entry point for the Asterisk telephony transport.

Runs two concurrent asyncio services in one process:
  1. The ARI client (ari_client.StasisApp), which captures caller ID/DID
     for each new call and hands it off to AudioSocket.
  2. The AudioSocket media server (media_server.AudioSocketServer), which
     Asterisk's dialplan streams call audio to/from.

Both share one PizzaBot / ConversationManager instance (so orders, the
kitchen dashboard, and locations are the exact same data the Flask app in
src/app.py uses) and one CallRegistry (the in-process handoff between the
two services - see media_server.py's docstring).

Run this alongside (not instead of) src/app.py: app.py still serves the
kitchen dashboard and JSON API; this process only replaces Twilio as the
thing that answers the phone. See DEPLOYMENT_ASTERISK.md for the full
FreePBX-side setup and a systemd unit to run this in production.
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src/

from main import PizzaBot  # noqa: E402
from conversation import ConversationManager  # noqa: E402

from .config import TelephonyConfig, build_stt, build_tts, build_vad  # noqa: E402
from .agent import CallAgent  # noqa: E402
from .ari_client import AriClient, AriConfig, StasisApp  # noqa: E402
from .media_server import AudioSocketServer, CallRegistry  # noqa: E402
from .vad import TurnDetector  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def build_agent_factory(config: TelephonyConfig, manager: ConversationManager):
    """Returns a zero-arg factory that builds a fresh CallAgent (with its own
    turn-detector state) for each new call. STT/TTS provider *clients* are
    built once and shared - the providers themselves are stateless per call."""
    stt = build_stt(config)
    tts = build_tts(config)

    def factory() -> CallAgent:
        vad = build_vad(config)
        turn_detector = TurnDetector(
            vad=vad,
            frame_ms=config.frame_ms,
            silence_ms_to_end_turn=config.silence_ms_to_end_turn,
            min_speech_ms_to_count=config.min_speech_ms_to_count,
        )
        return CallAgent(
            manager=manager, stt=stt, tts=tts, turn_detector=turn_detector,
            sample_rate=config.sample_rate,
            max_silence_ms_no_speech=config.max_silence_ms_no_speech,
            max_utterance_ms=config.max_utterance_ms,
        )

    return factory


async def run(config: TelephonyConfig, bot: PizzaBot = None):
    bot = bot or PizzaBot()
    manager = ConversationManager(bot)
    registry = CallRegistry()

    audiosocket_server = AudioSocketServer(
        agent_factory=build_agent_factory(config, manager),
        registry=registry,
        frame_bytes=config.frame_bytes,
        host=config.audiosocket_host,
        port=config.audiosocket_port,
    )

    ari_client = AriClient(AriConfig(
        base_url=config.ari_base_url, ws_url=config.ari_ws_url,
        username=config.ari_username, password=config.ari_password,
        app_name=config.ari_app_name,
    ))
    stasis_app = StasisApp(
        client=ari_client, registry=registry,
        audiosocket_context=config.dialplan_context,
        audiosocket_extension=config.dialplan_extension,
        did_variable=config.did_variable,
    )

    logger.info("Starting Asterisk telephony transport "
               f"(AudioSocket :{config.audiosocket_port}, ARI app '{config.ari_app_name}')")
    await asyncio.gather(
        audiosocket_server.serve_forever(),
        stasis_app.run(),
    )


def main():
    config = TelephonyConfig.from_env()
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        logger.info("Shutting down")


if __name__ == "__main__":
    main()
