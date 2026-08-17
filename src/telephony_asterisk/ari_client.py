"""
Asterisk REST Interface (ARI) client.

ARI is how we learn *who's calling and which DID they dialed* before the
call ever reaches AudioSocket (AudioSocket's own protocol only carries a
UUID - see media_server.py's module docstring), and later, how we hand a
call off to a human via a FreePBX ring group.

Call flow this drives (see DEPLOYMENT_ASTERISK.md for the matching FreePBX
side): the dialplan sends new calls into ``Stasis(pizzabot)``. Our
``StasisApp.run()`` receives the resulting ``StasisStart`` event over the ARI
WebSocket, reads the caller number and dialed DID off the channel, mints a
UUID for this call, records it in the shared ``CallRegistry`` (media_server.
py) under that UUID, stores the UUID on the channel as a variable, answers
it, and continues the dialplan back out of Stasis to the extension that runs
``AudioSocket(${AUDIOSOCKET_UUID}, host:port)``. The AudioSocket server then
looks that UUID up in the same registry the instant the media connection
arrives.

The REST layer takes an injected aiohttp.ClientSession (or an ARI websocket
factory) so this module is unit-testable without a live Asterisk box -
tests assert on the requests we *would* send and the orchestration logic
against canned events, not against a real PBX.
"""

import base64
import json
import logging
import uuid as uuid_module
from dataclasses import dataclass
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


@dataclass
class AriConfig:
    base_url: str          # e.g. "http://127.0.0.1:8088/ari"
    ws_url: str             # e.g. "ws://127.0.0.1:8088/ari/events"
    username: str
    password: str
    app_name: str = "pizzabot"


class AriClient:
    """Thin wrapper over the ARI REST + WebSocket API. Only the handful of
    operations this integration needs - see the FreePBX setup in
    DEPLOYMENT_ASTERISK.md for how the `ari_user`/`ari_password` referenced
    here are created (Settings -> Asterisk REST Interface)."""

    def __init__(self, config: AriConfig, session=None):
        self.config = config
        self._session = session  # optional injected aiohttp.ClientSession

    def _auth_header(self) -> dict:
        # Built by hand (rather than aiohttp.BasicAuth, deprecated as of
        # aiohttp 3.x and slated for removal in 4.0) so this doesn't depend
        # on which aiohttp version is installed.
        token = base64.b64encode(
            f"{self.config.username}:{self.config.password}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    async def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        session = self._session
        owns_session = session is None
        if owns_session:
            import aiohttp  # only needed when we have to open a real session
            session = aiohttp.ClientSession()
        try:
            url = f"{self.config.base_url}{path}"
            headers = {**self._auth_header(), **kwargs.pop("headers", {})}
            async with session.request(method, url, headers=headers, **kwargs) as resp:
                resp.raise_for_status()
                if resp.content_type == "application/json":
                    return await resp.json()
                return None
        finally:
            if owns_session:
                await session.close()

    async def answer(self, channel_id: str):
        await self._request("POST", f"/channels/{channel_id}/answer")

    async def get_channel(self, channel_id: str) -> dict:
        return await self._request("GET", f"/channels/{channel_id}")

    async def set_variable(self, channel_id: str, name: str, value: str):
        await self._request(
            "POST", f"/channels/{channel_id}/variable",
            params={"variable": name, "value": value},
        )

    async def get_variable(self, channel_id: str, name: str) -> Optional[str]:
        body = await self._request(
            "GET", f"/channels/{channel_id}/variable", params={"variable": name},
        )
        return (body or {}).get("value")

    async def continue_dialplan(self, channel_id: str, context: str,
                                extension: str, priority: int = 1):
        await self._request(
            "POST", f"/channels/{channel_id}/continue",
            params={"context": context, "extension": extension, "priority": str(priority)},
        )

    async def redirect(self, channel_id: str, endpoint: str):
        """Send a live channel to a different destination - used for the
        "talk to a person" handoff to a FreePBX ring group, e.g.
        endpoint="Local/600@from-internal" for ring-group extension 600."""
        await self._request(
            "POST", f"/channels/{channel_id}/redirect", params={"endpoint": endpoint},
        )

    async def hangup(self, channel_id: str):
        await self._request("DELETE", f"/channels/{channel_id}")

    async def events(self) -> AsyncIterator[dict]:
        """Yield parsed ARI events from the WebSocket stream. Runs until the
        connection closes; the caller (StasisApp.run) is expected to loop
        forever and let a process supervisor (systemd) restart on failure.
        Unlike _request(), this always needs aiohttp (for WSMsgType) even
        with an injected session, so no test double stands in for it - see
        StasisApp.handle_event() for the orchestration logic tested without
        a live WebSocket."""
        import aiohttp

        session = self._session
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()
        try:
            params = {"api_key": f"{self.config.username}:{self.config.password}",
                      "app": self.config.app_name, "subscribeAll": "true"}
            async with session.ws_connect(self.config.ws_url, params=params) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        yield json.loads(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        finally:
            if owns_session:
                await session.close()


DEFAULT_DID_VARIABLE = "FROM_DID"  # set by most FreePBX inbound routes; see runbook


class StasisApp:
    """Orchestrates the handoff described in this module's docstring: ARI
    StasisStart -> capture caller/DID -> register in CallRegistry -> answer
    -> continue dialplan to the AudioSocket extension."""

    def __init__(self, client: AriClient, registry, audiosocket_context: str,
                audiosocket_extension: str, did_variable: str = DEFAULT_DID_VARIABLE,
                uuid_factory=lambda: str(uuid_module.uuid4())):
        self.client = client
        self.registry = registry
        self.audiosocket_context = audiosocket_context
        self.audiosocket_extension = audiosocket_extension
        self.did_variable = did_variable
        self._uuid_factory = uuid_factory

    async def run(self):
        async for event in self.client.events():
            await self.handle_event(event)

    async def handle_event(self, event: dict):
        if event.get("type") != "StasisStart":
            return
        channel = event.get("channel") or {}
        channel_id = channel.get("id")
        if not channel_id:
            logger.warning(f"StasisStart event with no channel id: {event}")
            return
        await self._handle_new_call(channel_id, channel)

    async def _handle_new_call(self, channel_id: str, channel: dict):
        caller_number = (channel.get("caller") or {}).get("number") or "unknown"
        dialed_number = await self._resolve_dialed_number(channel_id, channel)

        call_uuid = self._uuid_factory()
        self.registry.register(call_uuid, caller_number, dialed_number)

        logger.info(f"Stasis call {channel_id}: caller={caller_number} "
                   f"dialed={dialed_number} -> audiosocket uuid={call_uuid}")

        await self.client.answer(channel_id)
        await self.client.set_variable(channel_id, "AUDIOSOCKET_UUID", call_uuid)
        await self.client.continue_dialplan(
            channel_id, self.audiosocket_context, self.audiosocket_extension,
        )

    async def _resolve_dialed_number(self, channel_id: str, channel: dict) -> Optional[str]:
        """The dialed DID isn't reliably in the StasisStart payload across
        FreePBX inbound-route configurations, so prefer a channel variable
        (set by the inbound route / dialplan) and fall back to the
        dialplan's `exten`. Confirm which your setup actually populates -
        see DEPLOYMENT_ASTERISK.md."""
        via_variable = await self.client.get_variable(channel_id, self.did_variable)
        if via_variable:
            return via_variable
        return (channel.get("dialplan") or {}).get("exten")
