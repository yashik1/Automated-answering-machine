"""
Tests for the kitchen staff dashboard: order listing, status updates, and
optional Basic-auth protection.
"""

import base64

import pytest

from main import PizzaBot
from app import create_app


@pytest.fixture
def env(tmp_path):
    bot = PizzaBot(str(tmp_path / "staff.db"))
    app = create_app(bot)
    app.config["TESTING"] = True
    return bot, app.test_client()


def _order(bot, customer_id=1, item_id=1, qty=1):
    return bot.create_order(customer_id=customer_id,
                            items=[{"id": item_id, "quantity": qty}])


# -- dashboard page --------------------------------------------------------

def test_staff_page_renders(env):
    _, client = env
    resp = client.get("/staff")
    assert resp.status_code == 200
    assert b"Kitchen" in resp.data


# -- order listing ---------------------------------------------------------

def test_api_orders_lists_active(env):
    bot, client = env
    o = _order(bot)
    data = client.get("/api/orders").get_json()
    ids = [row["id"] for row in data["orders"]]
    assert o.id in ids
    row = next(r for r in data["orders"] if r["id"] == o.id)
    assert row["status"] == "received"
    assert row["customer_name"] == "Mr. Singh"
    assert row["items"][0]["name"] == "Margherita Pizza"   # name resolved from menu


def test_api_orders_excludes_completed_by_default(env):
    bot, client = env
    o = _order(bot)
    bot.update_order_status(o.id, "completed")

    active = client.get("/api/orders").get_json()["orders"]
    assert all(r["id"] != o.id for r in active)

    withall = client.get("/api/orders?include_completed=1").get_json()["orders"]
    assert any(r["id"] == o.id for r in withall)


# -- status updates --------------------------------------------------------

def test_update_status_advances_order(env):
    bot, client = env
    o = _order(bot)
    resp = client.post(f"/api/orders/{o.id}/status", json={"status": "preparing"})
    assert resp.status_code == 200
    assert bot.get_order_status(o.id).status == "preparing"


def test_update_status_rejects_invalid(env):
    bot, client = env
    o = _order(bot)
    resp = client.post(f"/api/orders/{o.id}/status", json={"status": "teleporting"})
    assert resp.status_code == 400
    assert bot.get_order_status(o.id).status == "received"   # unchanged


def test_update_status_unknown_order(env):
    _, client = env
    resp = client.post("/api/orders/9999/status", json={"status": "preparing"})
    assert resp.status_code == 404


# -- optional auth ---------------------------------------------------------

def test_dashboard_requires_auth_when_password_set(tmp_path, monkeypatch):
    monkeypatch.setenv("STAFF_PASSWORD", "secret")
    monkeypatch.setenv("STAFF_USERNAME", "chef")
    bot = PizzaBot(str(tmp_path / "auth.db"))
    client = create_app(bot).test_client()

    # No credentials -> 401 challenge.
    resp = client.get("/staff")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers

    # Correct credentials -> allowed.
    token = base64.b64encode(b"chef:secret").decode()
    ok = client.get("/staff", headers={"Authorization": f"Basic {token}"})
    assert ok.status_code == 200

    # Wrong credentials -> still blocked.
    bad = base64.b64encode(b"chef:wrong").decode()
    denied = client.get("/staff", headers={"Authorization": f"Basic {bad}"})
    assert denied.status_code == 401
