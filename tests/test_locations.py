"""
Tests for multi-location support: location seeding/lookup, the spoken
"which location?" flow on a shared number, and per-location dashboard
filtering.
"""

import xml.etree.ElementTree as ET

import pytest

from main import PizzaBot, normalize_phone
from app import create_app


@pytest.fixture
def bot(tmp_path):
    return PizzaBot(str(tmp_path / "loc.db"))


@pytest.fixture
def client(bot):
    app = create_app(bot)
    app.config["TESTING"] = True
    c = app.test_client()
    c.bot = bot
    return c


def say_text(resp):
    root = ET.fromstring(resp.data.decode())
    return " ".join(el.text or "" for el in root.iter("Say"))


def has_gather(resp):
    return ET.fromstring(resp.data.decode()).find(".//Gather") is not None


# -- data model ------------------------------------------------------------

def test_default_locations_seeded(bot):
    locs = bot.list_locations()
    slugs = {l.slug for l in locs}
    assert slugs == {"downtown", "uptown"}


def test_normalize_phone():
    assert normalize_phone("+1 (234) 567-0001") == "12345670001"
    assert normalize_phone("12345670001") == "12345670001"


def test_get_location_by_phone_and_slug(bot):
    downtown = bot.get_location_by_slug("downtown")
    assert downtown is not None
    # Different formatting of the same number still matches.
    assert bot.get_location_by_phone("1-234-567-0001").id == downtown.id
    assert bot.get_location_by_phone("+19998887777") is None


# -- shared-number "which location?" flow ----------------------------------

def _post(client, path, **data):
    return client.post(path, data=data)


def test_shared_number_asks_which_location(client):
    # Dial a number that maps to no location (the shared line).
    resp = _post(client, "/voice", CallSid="CZ", **{"From": "555-0101", "To": "+19998887777"})
    spoken = say_text(resp)
    assert "location" in spoken.lower()
    assert "Downtown" in spoken and "Uptown" in spoken
    assert has_gather(resp)


def test_choosing_location_then_ordering_records_it(client):
    _post(client, "/voice", CallSid="CZ", **{"From": "555-0101", "To": "+19998887777"})

    # Caller picks a store.
    resp = _post(client, "/voice/collect", CallSid="CZ",
                 **{"From": "555-0101", "To": "+19998887777", "SpeechResult": "uptown please"})
    assert "uptown" in say_text(resp).lower()

    # Then places an order (known customer 555-0101, so it completes on "yes").
    for utterance in ["I want to order", "one margherita", "that's all", "yes"]:
        resp = _post(client, "/voice/collect", CallSid="CZ",
                     **{"From": "555-0101", "To": "+19998887777", "SpeechResult": utterance})

    uptown = client.bot.get_location_by_slug("uptown")
    orders = client.bot.list_orders(include_completed=True, location_id=uptown.id)
    assert len(orders) == 1
    assert orders[0]["location_slug"] == "uptown"


# -- dashboard filtering ---------------------------------------------------

def test_api_locations_lists_stores(client):
    data = client.get("/api/locations").get_json()
    slugs = {l["slug"] for l in data["locations"]}
    assert slugs == {"downtown", "uptown"}


def test_orders_api_filters_by_location(client):
    bot = client.bot
    downtown = bot.get_location_by_slug("downtown")
    uptown = bot.get_location_by_slug("uptown")
    bot.create_order(customer_id=1, items=[{"id": 1, "quantity": 1}], location_id=downtown.id)
    bot.create_order(customer_id=2, items=[{"id": 2, "quantity": 1}], location_id=uptown.id)

    down = client.get("/api/orders?location=downtown").get_json()["orders"]
    assert len(down) == 1
    assert down[0]["location_slug"] == "downtown"

    up = client.get("/api/orders?location=uptown").get_json()["orders"]
    assert len(up) == 1
    assert up[0]["location_slug"] == "uptown"

    # No filter -> both stores' orders.
    both = client.get("/api/orders").get_json()["orders"]
    assert len(both) == 2


def test_orders_api_unknown_location_is_empty(client):
    client.bot.create_order(customer_id=1, items=[{"id": 1, "quantity": 1}])
    orders = client.get("/api/orders?location=nowhere").get_json()["orders"]
    assert orders == []


def test_staff_page_for_specific_location_renders(client):
    resp = client.get("/staff/downtown")
    assert resp.status_code == 200
    assert b"downtown" in resp.data   # preselected via data-selected attribute
