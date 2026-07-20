"""
Unit tests for the Mr. Singh Pizza answering machine.

Each test gets its own throwaway SQLite database via the ``bot`` fixture so
tests stay isolated and never touch the real data/ directory.
"""

import pytest

from main import PizzaBot, CallType, CallStatus


@pytest.fixture
def bot(tmp_path):
    """A PizzaBot backed by a temporary database, fresh for every test."""
    db_path = tmp_path / "test_pizza_bot.db"
    return PizzaBot(str(db_path))


# --- Setup / data loading -------------------------------------------------

def test_menu_loads(bot):
    assert len(bot.menu_items) == 16
    names = [item.name.lower() for item in bot.menu_items.values()]
    assert any("margherita" in n for n in names)
    assert any("pepperoni" in n for n in names)


def test_database_tables_created(bot):
    import sqlite3
    conn = sqlite3.connect(bot.db_path)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    assert {"customers", "menu_items", "orders", "call_logs"} <= tables


def test_sample_customers_loaded(bot):
    customer = bot.get_customer_by_phone("555-0101")
    assert customer is not None
    assert customer.name == "Mr. Singh"


# --- Intent detection -----------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("I want to order a pizza", CallType.NEW_ORDER),
    ("where is my order status", CallType.ORDER_STATUS),
    ("what's on the menu", CallType.MENU_INQUIRY),
    ("what are your hours", CallType.HOURS_INQUIRY),
    ("I'd like to book a table", CallType.RESERVATION),
    ("my pizza was cold, this is a complaint", CallType.COMPLAINT),
    ("", CallType.OTHER),
])
def test_determine_intent(bot, text, expected):
    assert bot.determine_intent(text) == expected


# --- Order parsing --------------------------------------------------------

def test_process_order_items_parses_quantities(bot):
    customer = bot.get_customer_by_phone("555-0101")
    result = bot.process_order_items(customer, "2 margherita 1 garlic bread")
    assert result["action"] == "get_instructions"

    by_name = {item["name"]: item for item in result["items"]}
    assert by_name["Margherita Pizza"]["quantity"] == 2
    assert by_name["Garlic Bread"]["quantity"] == 1
    # 12.99 * 2 + 4.99 * 1
    assert result["total"] == pytest.approx(30.97)


def test_process_order_items_is_case_insensitive(bot):
    customer = bot.get_customer_by_phone("555-0101")
    result = bot.process_order_items(customer, "1 MARGHERITA")
    assert result["action"] == "get_instructions"
    assert result["items"][0]["name"] == "Margherita Pizza"


def test_process_order_items_unrecognized(bot):
    customer = bot.get_customer_by_phone("555-0101")
    result = bot.process_order_items(customer, "hello there")
    assert result["action"] == "clarify"


# --- Orders & customers ---------------------------------------------------

def test_create_order_computes_total(bot):
    items = [
        {"id": 1, "quantity": 2},   # Margherita 12.99
        {"id": 7, "quantity": 1},   # Garlic Bread 4.99
    ]
    order = bot.create_order(customer_id=1, items=items)
    assert order.total == pytest.approx(30.97)
    assert order.status == "received"
    assert order.estimated_ready > order.order_time


def test_get_customer_by_id(bot):
    customer = bot.get_customer_by_id(1)
    assert customer is not None
    assert customer.name == "Mr. Singh"


def test_order_status_resolves_customer_name(bot):
    """Regression test: order-status lookup must resolve the customer name
    via customer_id, not by mistakenly treating the id as a phone number."""
    order = bot.create_order(customer_id=1, items=[{"id": 1, "quantity": 1}])
    result = bot.handle_order_status("555-0101", f"status of order {order.id}")
    assert result["action"] == "order_status"
    assert "Mr. Singh" in result["message"]


def test_save_order_awards_loyalty_points(bot):
    customer = bot.get_customer_by_phone("555-0101")   # starts with 150 points
    starting = customer.loyalty_points
    items = [{"id": 1, "name": "Margherita Pizza", "price": 12.99, "quantity": 1}]
    result = bot.save_order_and_respond(customer, items)

    assert result["action"] == "order_complete"
    assert result["loyalty_points_earned"] == 12   # int(12.99)

    refreshed = bot.get_customer_by_id(customer.id)
    assert refreshed.loyalty_points == starting + 12


def test_create_and_fetch_new_customer(bot):
    new = bot.create_customer("Test User", "555-9999", "1 Test St")
    fetched = bot.get_customer_by_phone("555-9999")
    assert fetched is not None
    assert fetched.id == new.id
    assert fetched.name == "Test User"


# --- Call logging ---------------------------------------------------------

def test_log_call_writes_row(bot):
    import sqlite3
    bot.log_call("555-0101", CallType.NEW_ORDER, CallStatus.COMPLETED, notes="test")
    conn = sqlite3.connect(bot.db_path)
    count = conn.execute("SELECT COUNT(*) FROM call_logs").fetchone()[0]
    conn.close()
    assert count >= 1
