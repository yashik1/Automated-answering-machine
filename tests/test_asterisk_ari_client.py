"""
Tests for the ARI orchestration (telephony_asterisk/ari_client.py).

There's no live Asterisk to test against here, so these verify the two
things that matter and don't require one: (1) StasisApp's orchestration
logic reacts correctly to canned ARI events, driving a fake AriClient
double and a real CallRegistry; (2) AriClient's REST methods build the
correct HTTP request shape, verified against an injected fake aiohttp
session rather than a live server.
"""

import pytest

from telephony_asterisk.ari_client import AriClient, AriConfig, StasisApp, DEFAULT_DID_VARIABLE
from telephony_asterisk.media_server import CallRegistry


# -- StasisApp orchestration (fake AriClient double, real CallRegistry) ----

class FakeAriClient:
    """Records every call made to it instead of hitting a real ARI server."""

    def __init__(self, variables=None):
        self.calls = []
        self._variables = variables or {}

    async def answer(self, channel_id):
        self.calls.append(("answer", channel_id))

    async def get_variable(self, channel_id, name):
        self.calls.append(("get_variable", channel_id, name))
        return self._variables.get(name)

    async def set_variable(self, channel_id, name, value):
        self.calls.append(("set_variable", channel_id, name, value))

    async def continue_dialplan(self, channel_id, context, extension, priority=1):
        self.calls.append(("continue_dialplan", channel_id, context, extension, priority))


def _stasis_start_event(channel_id="chan-1", caller_number="555-0101",
                        dialplan_exten="s"):
    return {
        "type": "StasisStart",
        "channel": {
            "id": channel_id,
            "caller": {"number": caller_number, "name": ""},
            "dialplan": {"context": "from-pstn", "exten": dialplan_exten, "priority": 1},
        },
    }


@pytest.fixture
def app_and_client():
    client = FakeAriClient()
    registry = CallRegistry()
    app = StasisApp(
        client=client, registry=registry,
        audiosocket_context="ai-order-media", audiosocket_extension="s",
        uuid_factory=lambda: "fixed-test-uuid",
    )
    return app, client, registry


async def test_stasis_start_registers_caller_and_did_in_registry(app_and_client):
    app, client, registry = app_and_client
    await app.handle_event(_stasis_start_event(caller_number="555-0102"))

    meta = registry.pop("fixed-test-uuid")
    assert meta["caller_number"] == "555-0102"


async def test_stasis_start_answers_sets_uuid_var_and_continues_dialplan(app_and_client):
    app, client, registry = app_and_client
    await app.handle_event(_stasis_start_event(channel_id="chan-42"))

    assert ("answer", "chan-42") in client.calls
    assert ("set_variable", "chan-42", "AUDIOSOCKET_UUID", "fixed-test-uuid") in client.calls
    assert ("continue_dialplan", "chan-42", "ai-order-media", "s", 1) in client.calls


async def test_non_stasis_start_events_are_ignored(app_and_client):
    app, client, registry = app_and_client
    await app.handle_event({"type": "StasisEnd", "channel": {"id": "chan-1"}})
    assert client.calls == []


async def test_event_with_no_channel_id_is_ignored_not_raised(app_and_client):
    app, client, registry = app_and_client
    await app.handle_event({"type": "StasisStart", "channel": {}})
    assert client.calls == []


async def test_did_resolution_prefers_channel_variable_over_dialplan_exten():
    """FreePBX inbound routes commonly set a channel variable with the
    actually-dialed DID (dialplan.exten alone is unreliable across
    configurations) - confirm that's preferred when present."""
    client = FakeAriClient(variables={DEFAULT_DID_VARIABLE: "+12345670001"})
    registry = CallRegistry()
    app = StasisApp(client=client, registry=registry,
                    audiosocket_context="ctx", audiosocket_extension="s",
                    uuid_factory=lambda: "u1")

    await app.handle_event(_stasis_start_event(dialplan_exten="s"))
    meta = registry.pop("u1")
    assert meta["dialed_number"] == "+12345670001"


async def test_did_resolution_falls_back_to_dialplan_exten_when_var_unset():
    client = FakeAriClient(variables={})  # DID variable not set on this channel
    registry = CallRegistry()
    app = StasisApp(client=client, registry=registry,
                    audiosocket_context="ctx", audiosocket_extension="s",
                    uuid_factory=lambda: "u2")

    await app.handle_event(_stasis_start_event(dialplan_exten="+19998887777"))
    meta = registry.pop("u2")
    assert meta["dialed_number"] == "+19998887777"


async def test_missing_caller_number_defaults_to_unknown():
    client = FakeAriClient()
    registry = CallRegistry()
    app = StasisApp(client=client, registry=registry,
                    audiosocket_context="ctx", audiosocket_extension="s",
                    uuid_factory=lambda: "u3")

    event = {"type": "StasisStart", "channel": {"id": "c1", "caller": {}}}
    await app.handle_event(event)
    meta = registry.pop("u3")
    assert meta["caller_number"] == "unknown"


# -- AriClient REST request shaping (fake aiohttp session, no real network) --

class _FakeResponse:
    def __init__(self, json_body=None, content_type="application/json"):
        self._json_body = json_body
        self.content_type = content_type

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self._json_body


class _FakeSession:
    def __init__(self, response_body=None):
        self.requests = []  # (method, url, kwargs)
        self._response_body = response_body

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return _FakeResponse(self._response_body)

    async def close(self):
        pass


def _client(session_body=None):
    session = _FakeSession(session_body)
    config = AriConfig(base_url="http://pbx.local:8088/ari", ws_url="ws://x",
                       username="pizzabot", password="secret")
    return AriClient(config, session=session), session


async def test_answer_posts_to_correct_endpoint():
    client, session = _client()
    await client.answer("chan-9")
    method, url, kwargs = session.requests[0]
    assert method == "POST"
    assert url == "http://pbx.local:8088/ari/channels/chan-9/answer"


async def test_set_variable_sends_name_and_value_as_params():
    client, session = _client()
    await client.set_variable("chan-9", "AUDIOSOCKET_UUID", "abc-123")
    method, url, kwargs = session.requests[0]
    assert url.endswith("/channels/chan-9/variable")
    assert kwargs["params"] == {"variable": "AUDIOSOCKET_UUID", "value": "abc-123"}


async def test_get_variable_extracts_value_from_response_body():
    client, session = _client(session_body={"value": "+15551234567"})
    result = await client.get_variable("chan-9", "FROM_DID")
    assert result == "+15551234567"


async def test_get_variable_returns_none_when_unset():
    client, session = _client(session_body={})
    result = await client.get_variable("chan-9", "FROM_DID")
    assert result is None


async def test_continue_dialplan_sends_context_extension_priority():
    client, session = _client()
    await client.continue_dialplan("chan-9", "ai-order-media", "s", priority=2)
    _, url, kwargs = session.requests[0]
    assert url.endswith("/channels/chan-9/continue")
    assert kwargs["params"] == {"context": "ai-order-media", "extension": "s", "priority": "2"}


async def test_redirect_sends_target_endpoint():
    client, session = _client()
    await client.redirect("chan-9", "Local/600@from-internal")
    _, url, kwargs = session.requests[0]
    assert url.endswith("/channels/chan-9/redirect")
    assert kwargs["params"] == {"endpoint": "Local/600@from-internal"}
