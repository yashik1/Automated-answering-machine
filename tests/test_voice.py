"""
End-to-end tests for the Twilio voice flow.

These drive the Flask app with form posts shaped exactly like Twilio's
webhooks (CallSid, From, SpeechResult), so the entire phone conversation is
exercised without a real phone or Twilio account. TwiML responses are parsed
as XML to read back what the bot would say and whether it keeps listening.
"""

import xml.etree.ElementTree as ET

import pytest

from main import PizzaBot
from app import create_app


@pytest.fixture
def client(tmp_path):
    bot = PizzaBot(str(tmp_path / "voice.db"))
    app = create_app(bot)
    app.config["TESTING"] = True
    client = app.test_client()
    client.bot = bot
    return client


def say_text(xml_body: str) -> str:
    """All <Say> text in the TwiML response, joined."""
    root = ET.fromstring(xml_body)
    return " ".join(el.text or "" for el in root.iter("Say"))


def has_gather(xml_body: str) -> bool:
    return ET.fromstring(xml_body).find(".//Gather") is not None


# Dial the Downtown store's number so calls auto-route there and these tests
# can focus on the order flow (location selection is covered in test_locations).
DOWNTOWN_NUMBER = "+1 (234) 567-0001"


def incoming(client, call_sid="CA_test", frm="555-0101", to=DOWNTOWN_NUMBER):
    return client.post("/voice", data={"CallSid": call_sid, "From": frm, "To": to})


def collect(client, speech, call_sid="CA_test", frm="555-0101", to=DOWNTOWN_NUMBER):
    return client.post(
        "/voice/collect",
        data={"CallSid": call_sid, "From": frm, "To": to, "SpeechResult": speech},
    )


# -- greeting --------------------------------------------------------------

def test_incoming_call_greets_known_customer(client):
    resp = incoming(client)
    body = resp.data.decode()
    assert resp.status_code == 200
    assert "Downtown" in say_text(body)   # auto-routed to the dialed location
    assert "Singh" in say_text(body)      # known caller greeted by name
    assert has_gather(body)               # bot is listening


def test_incoming_call_greets_unknown_caller(client):
    resp = incoming(client, frm="555-8888")
    body = resp.data.decode()
    assert "calling" in say_text(body).lower()
    assert "Downtown" in say_text(body)
    assert has_gather(body)


# -- full order, known customer -------------------------------------------

def test_full_order_flow_known_customer(client):
    incoming(client)

    body = collect(client, "I'd like to place an order").data.decode()
    assert "what would you like to order" in say_text(body).lower()

    body = collect(client, "two margherita pizzas").data.decode()
    assert "added" in say_text(body).lower()
    assert "2 Margherita Pizza" in say_text(body)

    body = collect(client, "a garlic bread").data.decode()
    assert "Garlic Bread" in say_text(body)

    body = collect(client, "that's all").data.decode()
    spoken = say_text(body).lower()
    assert "total" in spoken
    # 12.99 * 2 + 4.99 = 30.97
    assert "30.97" in say_text(body)

    body = collect(client, "yes").data.decode()
    spoken = say_text(body)
    assert "order number is 1" in spoken.lower()
    assert not has_gather(body)   # call ends after confirmation

    # The order was actually persisted.
    order = client.bot.get_order_status(1)
    assert order is not None
    assert order.total == pytest.approx(30.97)


# -- full order, new caller must give a name ------------------------------

def test_order_flow_new_customer_collects_name(client):
    incoming(client, frm="555-4321")
    collect(client, "I want to order", frm="555-4321")
    collect(client, "one pepperoni pizza", frm="555-4321")
    body = collect(client, "that's all", frm="555-4321").data.decode()
    assert "total" in say_text(body).lower()

    # Unknown caller: confirming should ask for a name, not place the order yet.
    body = collect(client, "yes", frm="555-4321").data.decode()
    assert "name" in say_text(body).lower()

    body = collect(client, "my name is Alex Doe", frm="555-4321").data.decode()
    assert "order number" in say_text(body).lower()
    assert not has_gather(body)

    customer = client.bot.get_customer_by_phone("555-4321")
    assert customer is not None
    assert customer.name == "Alex Doe"


# -- changing your mind at confirmation -----------------------------------

def test_saying_no_at_confirmation_restarts_order(client):
    incoming(client)
    collect(client, "place an order")
    collect(client, "one margherita")
    collect(client, "that's all")
    body = collect(client, "no").data.decode()
    assert "start over" in say_text(body).lower()
    assert has_gather(body)


# -- order status ----------------------------------------------------------

def test_order_status_lookup(client):
    # Seed an order for the known customer (id 1).
    client.bot.create_order(customer_id=1, items=[{"id": 1, "quantity": 1}])

    incoming(client)
    body = collect(client, "where is my order status").data.decode()
    assert "order number" in say_text(body).lower()

    body = collect(client, "order number 1").data.decode()
    assert "order #1" in say_text(body).lower() or "order 1" in say_text(body).lower()


# -- menu & hours ----------------------------------------------------------

def test_menu_inquiry(client):
    incoming(client)
    body = collect(client, "what's on the menu").data.decode()
    assert "margherita" in say_text(body).lower()


def test_hours_inquiry(client):
    incoming(client)
    body = collect(client, "what are your hours").data.decode()
    assert "open" in say_text(body).lower()


# -- website & API ---------------------------------------------------------

def test_index_page_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Mr. Singh Pizza" in resp.data


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_menu_api(client):
    resp = client.get("/menu")
    assert resp.status_code == 200
    assert len(resp.get_json()["menu"]) == 16
