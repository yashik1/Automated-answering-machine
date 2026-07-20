#!/usr/bin/env python3
"""
Test script to verify the PizzaBot installation and basic functionality
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_imports():
    """Test that all modules can be imported"""
    try:
        from main import PizzaBot
        print("✓ Main module imported successfully")
        return True
    except ImportError as e:
        print(f"✗ Failed to import main module: {e}")
        return False

def test_basic_functionality():
    """Test basic bot initialization"""
    try:
        from main import PizzaBot
        bot = PizzaBot("test_pizza_bot.db")
        print("✓ PizzaBot instantiated successfully")

        # Clean up test database
        if os.path.exists("test_pizza_bot.db"):
            os.remove("test_pizza_bot.db")

        return True
    except Exception as e:
        print(f"✗ Failed to initialize PizzaBot: {e}")
        return False

def test_menu_loading():
    """Test that menu loads correctly"""
    try:
        from main import PizzaBot
        bot = PizzaBot("test_pizza_bot.db")

        # Check that we have menu items
        assert len(bot.menu_items) > 0, "No menu items loaded"
        print(f"✓ Menu loaded successfully with {len(bot.menu_items)} items")

        # Check for some expected items
        item_names = [item.name.lower() for item in bot.menu_items.values()]
        assert any('margherita' in name for name in item_names), "Margherita pizza not found"
        assert any('pepperoni' in name for name in item_names), "Pepperoni pizza not found"
        print("✓ Menu contains expected items")

        # Clean up
        if os.path.exists("test_pizza_bot.db"):
            os.remove("test_pizza_bot.db")

        return True
    except Exception as e:
        print(f"✗ Menu loading test failed: {e}")
        return False

def test_database_creation():
    """Test that database tables are created"""
    try:
        import sqlite3
        from main import PizzaBot
        bot = PizzaBot("test_pizza_bot.db")

        # Check that we can connect to the database
        conn = sqlite3.connect("test_pizza_bot.db")
        cursor = conn.cursor()

        # Check for expected tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        expected_tables = ['customers', 'menu_items', 'orders', 'call_logs']

        for table in expected_tables:
            assert table in tables, f"Table {table} not found"

        print("✓ Database tables created successfully")
        conn.close()

        # Clean up
        if os.path.exists("test_pizza_bot.db"):
            os.remove("test_pizza_bot.db")

        return True
    except Exception as e:
        print(f"✗ Database test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("🧪 Testing Mr. Singh Pizza Automated Answering Machine")
    print("=" * 60)

    tests = [
        test_imports,
        test_basic_functionality,
        test_menu_loading,
        test_database_creation
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1
        print()  # Empty line between tests

    print("=" * 60)
    print(f"Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! The system is ready to use.")
        return 0
    else:
        print("❌ Some tests failed. Please check the installation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())