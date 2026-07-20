#!/usr/bin/env python3
"""
Automated Answering Machine for Mr. Singh Pizza
A voice-based AI assistant for handling customer calls, taking orders,
answering FAQs, and managing reservations.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sqlite3
from dataclasses import dataclass
from enum import Enum

# Anchor all runtime paths to the project root so the app behaves the same
# no matter which directory it is launched from.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")

# The log directory must exist before the FileHandler is created, otherwise
# logging.basicConfig raises FileNotFoundError on a fresh clone.
os.makedirs(LOG_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'pizza_bot.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class CallStatus(Enum):
    """Enumeration for call status"""
    RINGING = "ringing"
    ANSWERED = "answered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    MISSED = "missed"
    VOICEMAIL = "voicemail"

class CallType(Enum):
    """Types of calls we handle"""
    NEW_ORDER = "new_order"
    ORDER_STATUS = "order_status"
    MENU_INQUIRY = "menu_inquiry"
    RESERVATION = "reservation"
    HOURS_INQUIRY = "hours_inquiry"
    COMPLAINT = "complaint"
    OTHER = "other"

@dataclass
class MenuItem:
    """Menu item data structure"""
    id: int
    name: str
    description: str
    price: float
    category: str
    available: bool = True

@dataclass
class Customer:
    """Customer data structure"""
    id: int
    name: str
    phone: str
    address: Optional[str] = None
    preferences: Optional[Dict] = None
    loyalty_points: int = 0

@dataclass
class Order:
    """Order data structure"""
    id: int
    customer_id: int
    items: List[Dict]
    total: float
    status: str
    order_time: datetime
    estimated_ready: datetime
    special_instructions: Optional[str] = None

class PizzaBot:
    """Main class for Mr. Singh Pizza automated answering machine"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(DATA_DIR, "pizza_bot.db")
        self.db_path = db_path
        self.menu_items: Dict[int, MenuItem] = {}
        self.customers: Dict[int, Customer] = {}
        self.active_calls: Dict[str, Dict] = {}

        # Ensure runtime directories exist, including the parent of whatever
        # database path was supplied (which may be a relative test path).
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        os.makedirs(os.path.join(BASE_DIR, "static", "audio"), exist_ok=True)

        # Initialize database
        self.init_database()

        # Load menu and sample data
        self.load_menu()
        self.load_sample_data()

        logger.info("PizzaBot initialized successfully")

    def init_database(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Customers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                address TEXT,
                preferences TEXT,
                loyalty_points INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Menu items table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS menu_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                category TEXT NOT NULL,
                available BOOLEAN DEFAULT 1
            )
        ''')

        # Orders table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER,
                items TEXT NOT NULL,
                total REAL NOT NULL,
                status TEXT DEFAULT 'received',
                order_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                estimated_ready TIMESTAMP,
                special_instructions TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers (id)
            )
        ''')

        # Call logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS call_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                caller_id TEXT,
                call_type TEXT,
                status TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                duration INTEGER,
                notes TEXT,
                sentiment_score REAL
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")

    def load_menu(self):
        """Load menu items from database or initialize with default menu"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM menu_items WHERE available = 1")
        rows = cursor.fetchall()

        if not rows:
            # Initialize with default Mr. Singh Pizza menu
            default_menu = [
                (1, "Margherita Pizza", "Classic margherita with fresh mozzarella and basil", 12.99, "Pizza", True),
                (2, "Pepperoni Pizza", "Classic pepperoni with extra cheese", 14.99, "Pizza", True),
                (3, "Vegetarian Supreme", "Mixed vegetables with olives and feta", 15.99, "Pizza", True),
                (4, "Meat Lovers", "Pepperoni, sausage, bacon, and ham", 16.99, "Pizza", True),
                (5, "Chicken Tikka Pizza", "Indian-spiced chicken tikka with onions", 16.99, "Specialty Pizza", True),
                (6, "Paneer Tikka Pizza", "Indian cottage cheese with bell peppers", 15.99, "Specialty Pizza", True),
                (7, "Garlic Bread", "Fresh baked garlic bread with herbs", 4.99, "Sides", True),
                (8, "Garlic Knots", "Twisted garlic bread parmesan", 5.99, "Sides", True),
                (9, "Caesar Salad", "Fresh romaine with caesar dressing", 6.99, "Salads", True),
                (10, "Greek Salad", "Greek salad with feta and olives", 7.99, "Salads", True),
                (11, "Coca Cola", "Soft drink - 2L bottle", 2.99, "Beverages", True),
                (12, "Sprite", "Lemon-lime soda - 2L bottle", 2.99, "Beverages", True),
                (13, "Masala Chai", "Indian spiced tea", 2.49, "Beverages", True),
                (14, "Mango Lassi", "Sweet yogurt drink with mango", 3.99, "Beverages", True),
                (15, "Chocolate Lava Cake", "Warm chocolate cake with molten center", 5.99, "Desserts", True),
                (16, "Gulab Jamun", "Indian sweet milk dumplings", 4.99, "Desserts", True)
            ]

            cursor.executemany('''
                INSERT INTO menu_items (id, name, description, price, category, available)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', default_menu)

            conn.commit()
            rows = default_menu

        for row in rows:
            menu_item = MenuItem(
                id=row[0],
                name=row[1],
                description=row[2],
                price=row[3],
                category=row[4],
                available=bool(row[5])
            )
            self.menu_items[menu_item.id] = menu_item

        conn.close()
        logger.info(f"Loaded {len(self.menu_items)} menu items")

    def load_sample_data(self):
        """Load sample customer data"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM customers")
        count = cursor.fetchone()[0]

        if count == 0:
            # Add sample customers
            sample_customers = [
                (1, "Mr. Singh", "555-0101", "123 Pizza Street", '{"favorite": "Margherita Pizza"}', 150),
                (2, "Priya Patel", "555-0102", "456 Curry Lane", '{"vegetarian": true, "spice_level": "medium"}', 200),
                (3, "Amit Sharma", "555-0103", "789 Naan Avenue", '{"loves_spicy": true}', 75),
                (4, "Sunita Gupta", "555-0104", "321 Samosa Street", '{"jain_food": true}', 300),
                (5, "Rajesh Kumar", "555-0105", "654 Biryani Boulevard", '{"extra_cheese": true}', 120)
            ]

            cursor.executemany('''
                INSERT INTO customers (id, name, phone, address, preferences, loyalty_points)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', sample_customers)

            conn.commit()
            logger.info("Sample customer data loaded")

        conn.close()

    def get_customer_by_phone(self, phone: str) -> Optional[Customer]:
        """Retrieve customer by phone number"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM customers WHERE phone = ?", (phone,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return Customer(
                id=row[0],
                name=row[1],
                phone=row[2],
                address=row[3],
                preferences=json.loads(row[4]) if row[4] else None,
                loyalty_points=row[5]
            )
        return None

    def get_customer_by_id(self, customer_id: int) -> Optional[Customer]:
        """Retrieve customer by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return Customer(
                id=row[0],
                name=row[1],
                phone=row[2],
                address=row[3],
                preferences=json.loads(row[4]) if row[4] else None,
                loyalty_points=row[5]
            )
        return None

    def get_menu_by_category(self, category: str) -> List[MenuItem]:
        """Get menu items by category"""
        return [item for item in self.menu_items.values()
                if item.category.lower() == category.lower() and item.available]

    def get_menu_item(self, item_id: int) -> Optional[MenuItem]:
        """Get specific menu item by ID"""
        return self.menu_items.get(item_id)

    def create_customer(self, name: str, phone: str, address: str = None,
                       preferences: Dict = None) -> Customer:
        """Create a new customer"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO customers (name, phone, address, preferences)
            VALUES (?, ?, ?, ?)
        ''', (name, phone, address, json.dumps(preferences) if preferences else None))

        customer_id = cursor.lastrowid
        conn.commit()
        conn.close()

        customer = Customer(
            id=customer_id,
            name=name,
            phone=phone,
            address=address,
            preferences=preferences,
            loyalty_points=0
        )

        self.customers[customer_id] = customer
        logger.info(f"New customer created: {name} ({phone})")
        return customer

    def create_order(self, customer_id: int, items: List[Dict],
                    special_instructions: str = None) -> Order:
        """Create a new order"""
        # Calculate total
        total = 0.0
        for item in items:
            menu_item = self.get_menu_item(item['id'])
            if menu_item:
                total += menu_item.price * item.get('quantity', 1)

        # Calculate estimated ready time (20-45 minutes based on order size)
        prep_time = min(45, 20 + len(items) * 5)
        estimated_ready = datetime.now() + timedelta(minutes=prep_time)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO orders (customer_id, items, total, estimated_ready, special_instructions)
            VALUES (?, ?, ?, ?, ?)
        ''', (customer_id, json.dumps(items), total, estimated_ready, special_instructions))

        order_id = cursor.lastrowid
        conn.commit()
        conn.close()

        order = Order(
            id=order_id,
            customer_id=customer_id,
            items=items,
            total=total,
            status="received",
            order_time=datetime.now(),
            estimated_ready=estimated_ready,
            special_instructions=special_instructions
        )

        logger.info(f"New order created: #{order_id} for customer {customer_id} - ${total:.2f}")
        return order

    def get_order_status(self, order_id: int) -> Optional[Order]:
        """Get order status by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT o.*, c.name, c.phone
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.id = ?
        ''', (order_id,))

        row = cursor.fetchone()
        conn.close()

        if row:
            # orders columns: id, customer_id, items, total, status,
            # order_time, estimated_ready, special_instructions (then c.name,
            # c.phone from the join).
            return Order(
                id=row[0],
                customer_id=row[1],
                items=json.loads(row[2]),
                total=row[3],
                status=row[4],
                order_time=datetime.fromisoformat(row[5]) if row[5] else datetime.now(),
                estimated_ready=datetime.fromisoformat(row[6]) if row[6] else None,
                special_instructions=row[7]
            )
        return None

    def update_order_status(self, order_id: int, status: str):
        """Update order status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE orders SET status = ? WHERE id = ?
        ''', (status, order_id))

        conn.commit()
        conn.close()
        logger.info(f"Order #{order_id} status updated to: {status}")

    def order_exists(self, order_id: int) -> bool:
        """Return True if an order with this id exists."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,))
        found = cursor.fetchone() is not None
        conn.close()
        return found

    def list_orders(self, include_completed: bool = False,
                    limit: int = 200) -> List[Dict]:
        """List orders for the kitchen dashboard, oldest first, joined with
        the customer name/phone. Completed and cancelled orders are excluded
        unless ``include_completed`` is True."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = '''
            SELECT o.id, o.customer_id, o.items, o.total, o.status,
                   o.order_time, o.estimated_ready, o.special_instructions,
                   c.name AS customer_name, c.phone AS customer_phone
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.id
        '''
        if not include_completed:
            query += " WHERE o.status NOT IN ('completed', 'cancelled')"
        query += " ORDER BY o.order_time ASC, o.id ASC LIMIT ?"

        rows = cursor.execute(query, (limit,)).fetchall()
        conn.close()

        orders = []
        for row in rows:
            orders.append({
                "id": row["id"],
                "customer_id": row["customer_id"],
                "customer_name": row["customer_name"] or "Guest",
                "customer_phone": row["customer_phone"] or "",
                "items": json.loads(row["items"]) if row["items"] else [],
                "total": row["total"],
                "status": row["status"],
                "order_time": row["order_time"],
                "estimated_ready": row["estimated_ready"],
                "special_instructions": row["special_instructions"],
            })
        return orders

    def log_call(self, caller_id: str, call_type: CallType, status: CallStatus,
                 notes: str = None, sentiment_score: float = None):
        """Log call details"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO call_logs (caller_id, call_type, status, start_time, notes, sentiment_score)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (caller_id, call_type.value, status.value, datetime.now(), notes, sentiment_score))

        conn.commit()
        conn.close()
        logger.info(f"Call logged: {caller_id} - {call_type.value} - {status.value}")

    def handle_new_order(self, caller_id: str, customer: Customer = None) -> Dict:
        """Handle a new order request"""
        if not customer:
            # Ask for customer details
            return {
                "action": "gather_info",
                "message": "Hello! Welcome to Mr. Singh Pizza. To take your order, I'll need your name and phone number. Could you please tell me your name?",
                "next_step": "get_name"
            }

        # Show menu and take order
        menu_by_category = {}
        for item in self.menu_items.values():
            if item.available:
                if item.category not in menu_by_category:
                    menu_by_category[item.category] = []
                menu_by_category[item.category].append(item)

        menu_text = "Here's our menu:\n\n"
        for category, items in menu_by_category.items():
            menu_text += f"{category.upper()}:\n"
            for item in items:
                menu_text += f"  {item.id}. {item.name} - ${item.price:.2f}\n"
                menu_text += f"     {item.description}\n\n"

        menu_text += "Please tell me what you'd like to order by telling me the item numbers and quantities. "
        menu_text += "For example: '2 Margherita pizzas and 1 garlic bread'."

        return {
            "action": "take_order",
            "message": menu_text,
            "customer": customer
        }

    def process_order_items(self, customer: Customer, order_text: str) -> Dict:
        """Process the customer's order text into items"""
        # Simple NLP for order processing - in production would use more sophisticated NLP
        items = []

        # Simple parsing - look for numbers and item names. Lower-case the
        # words up front so item matching is case-insensitive.
        words = order_text.lower().split()
        i = 0
        while i < len(words):
            # Look for quantities
            if words[i].isdigit():
                qty = int(words[i])
                i += 1

                # Look for item name
                item_name_parts = []
                while i < len(words) and not words[i].isdigit():
                    item_name_parts.append(words[i])
                    i += 1

                item_name = " ".join(item_name_parts).strip()

                # Find the best-matching menu item by counting how many words
                # of its name the caller actually said. Picking the highest
                # overlap (rather than the first item containing any word)
                # stops a generic word like "pizza" from matching the first
                # pizza on the menu instead of the specific one requested.
                requested_words = set(item_name.split())
                matched_item = None
                best_score = 0
                for menu_item in self.menu_items.values():
                    name_words = set(menu_item.name.lower().split())
                    score = len(name_words & requested_words)
                    if score > best_score:
                        best_score = score
                        matched_item = menu_item

                if matched_item:
                    items.append({
                        "id": matched_item.id,
                        "name": matched_item.name,
                        "price": matched_item.price,
                        "quantity": qty
                    })
            else:
                i += 1

        if not items:
            return {
                "action": "clarify",
                "message": "I'm sorry, I didn't understand your order. Could you please repeat it by telling me the item numbers from our menu?",
                "customer": customer
            }

        # Calculate total
        total = sum(item["price"] * item["quantity"] for item in items)

        # Ask for special instructions
        return {
            "action": "get_instructions",
            "message": f"Great! I've got your order:\n",
            "items": items,
            "total": total,
            "customer": customer
        }

    def confirm_order(self, customer: Customer, items: List[Dict],
                     special_instructions: str = None) -> Dict:
        """Confirm the order with customer"""
        total = sum(item["price"] * item["quantity"] for item in items)

        order_summary = "Here's your order summary:\n\n"
        for item in items:
            order_summary += f"{item['quantity']} x {item['name']} - ${item['price'] * item['quantity']:.2f}\n"

        if special_instructions:
            order_summary += f"\nSpecial instructions: {special_instructions}\n"

        order_summary += f"\nTotal: ${total:.2f}\n"
        order_summary += f"Estimated ready time: Approximately {(datetime.now() + timedelta(minutes=30)).strftime('%I:%M %p')}\n"
        order_summary += "\nIs this correct? Please say 'yes' to confirm or 'no' to make changes."

        return {
            "action": "confirm_order",
            "message": order_summary,
            "items": items,
            "total": total,
            "special_instructions": special_instructions,
            "customer": customer
        }

    def handle_menu_inquiry(self) -> Dict:
        """Handle menu inquiry"""
        categories = list(set(item.category for item in self.menu_items.values() if item.available))

        message = "Here's what we have available at Mr. Singh Pizza:\n\n"
        for category in sorted(categories):
            items = self.get_menu_by_category(category)
            message += f"{category.upper()}:\n"
            for item in items:
                message += f"  • {item.name} - ${item.price:.2f}\n"
                message += f"    {item.description}\n"
            message += "\n"

        message += "Would you like to place an order or do you have any other questions?"

        return {
            "action": "menu_info",
            "message": message
        }

    def handle_hours_inquiry(self) -> Dict:
        """Handle hours inquiry"""
        message = "Mr. Singh Pizza hours:\n\n"
        message += "Monday-Thursday: 11:00 AM - 10:00 PM\n"
        message += "Friday-Saturday: 11:00 AM - 11:00 PM\n"
        message += "Sunday: 12:00 PM - 9:00 PM\n\n"
        message += "We offer free delivery within 5 miles for orders over $25.\n"
        message += "Would you like to place an order for delivery or pickup?"

        return {
            "action": "hours_info",
            "message": message
        }

    def handle_reservation(self, customer: Customer = None) -> Dict:
        """Handle reservation request"""
        if not customer:
            return {
                "action": "gather_info",
                "message": "Hello! I'd be happy to help you make a reservation. First, may I have your name and phone number?",
                "next_step": "get_name_for_reservation"
            }

        message = f"Hello {customer.name}! I can help you make a reservation.\n\n"
        message += "Our restaurant has tables for 2, 4, 6, and 8 people.\n"
        message += "Please let me know:\n"
        message += "1. Date and time you'd like to reserve\n"
        message += "2. Number of people in your party\n"
        message += "3. Any special requests (high chair, wheelchair access, etc.)\n\n"
        message += "When would you like to come in?"

        return {
            "action": "take_reservation",
            "message": message,
            "customer": customer
        }

    def handle_general_inquiry(self) -> Dict:
        """Handle general inquiries"""
        message = "Hello! Thank you for calling Mr. Singh Pizza. How can I help you today?\n\n"
        message += "You can:\n"
        message += "1. Place a new order\n"
        message += "2. Check on an existing order\n"
        message += "3. Ask about our menu\n"
        message += "4. Ask about our hours or location\n"
        message += "5. Make a reservation\n"
        message += "6. Speak with a manager\n\n"
        message += "What would you like to do?"

        return {
            "action": "greeting",
            "message": message
        }

    def process_call(self, caller_id: str, speech_text: str = None,
                    call_type: CallType = CallType.OTHER) -> Dict:
        """Main processing function for incoming calls"""
        logger.info(f"Processing call from {caller_id}: {speech_text}")

        # Get or create customer
        customer = self.get_customer_by_phone(caller_id)

        # Log the call start
        self.log_call(caller_id, call_type, CallStatus.IN_PROGRESS)

        # Determine intent based on speech
        intent = self.determine_intent(speech_text or "")

        # Route to appropriate handler
        if intent == CallType.NEW_ORDER:
            return self.handle_new_order(caller_id, customer)
        elif intent == CallType.MENU_INQUIRY:
            return self.handle_menu_inquiry()
        elif intent == CallType.HOURS_INQUIRY:
            return self.handle_hours_inquiry()
        elif intent == CallType.RESERVATION:
            return self.handle_reservation(customer)
        elif intent == CallType.ORDER_STATUS:
            return self.handle_order_status(caller_id, speech_text)
        else:
            return self.handle_general_inquiry()

    def determine_intent(self, speech_text: str) -> CallType:
        """Determine caller intent from speech text"""
        if not speech_text:
            return CallType.OTHER

        text_lower = speech_text.lower()

        # Complaints first: a caller saying "my pizza was cold" should be
        # routed to a complaint, not mistaken for a new order.
        if any(word in text_lower for word in ['complaint', 'problem', 'issue', 'wrong', 'cold', 'late']):
            return CallType.COMPLAINT

        # Order-related keywords
        if any(word in text_lower for word in ['order', 'pizza', 'food', 'hungry', 'delivery', 'pickup']):
            if any(word in text_lower for word in ['status', 'where is', 'when will', 'tracking']):
                return CallType.ORDER_STATUS
            return CallType.NEW_ORDER

        # Menu inquiries
        elif any(word in text_lower for word in ['menu', 'what do you have', 'what are your options', 'food']):
            return CallType.MENU_INQUIRY

        # Hours/location
        elif any(word in text_lower for word in ['hour', 'open', 'close', 'time', 'location', 'address']):
            return CallType.HOURS_INQUIRY

        # Reservations
        elif any(word in text_lower for word in ['reserve', 'reservation', 'table', 'book', 'seating']):
            return CallType.RESERVATION

        return CallType.OTHER

    def handle_order_status(self, caller_id: str, speech_text: str) -> Dict:
        """Handle order status inquiry"""
        # Extract order number from speech (simplified)
        # In production, would use better NLP
        import re
        numbers = re.findall(r'\d+', speech_text)

        if numbers:
            order_id = int(numbers[0])
            order = self.get_order_status(order_id)

            if order:
                customer = self.get_customer_by_id(order.customer_id)
                customer_name = customer.name if customer else "Valued Customer"

                message = f"Hello {customer_name}! Let me check your order #{order_id}.\n\n"
                message += f"Status: {order.status.title()}\n"
                message += f"Order time: {order.order_time.strftime('%I:%M %p')}\n"

                if order.estimated_ready:
                    message += f"Estimated ready: {order.estimated_ready.strftime('%I:%M %p')}\n"

                if order.status == "ready" or order.status == "out for delivery":
                    message += "\nYour order is ready for pickup! Please come to the counter.\n"
                elif order.status == "out for delivery":
                    message += "\nYour order is out for delivery and should arrive soon.\n"

                message += "\nIs there anything else I can help you with?"

                return {
                    "action": "order_status",
                    "message": message,
                    "order": order
                }

        # If we can't find the order
        return {
            "action": "order_not_found",
            "message": "I'm sorry, I couldn't find that order. Could you please provide your order number again, or alternatively, I can look it up by your phone number if you'd prefer?"
        }

    def save_order_and_respond(self, customer: Customer, items: List[Dict],
                              special_instructions: str = None) -> Dict:
        """Save the order and provide confirmation"""
        order = self.create_order(customer.id, items, special_instructions)

        # Calculate loyalty points earned (1 point per dollar spent)
        points_earned = int(order.total)
        new_balance = customer.loyalty_points + points_earned

        # Update customer loyalty points
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE customers SET loyalty_points = ? WHERE id = ?",
            (new_balance, customer.id)
        )
        conn.commit()
        conn.close()

        # Update customer in memory
        if customer.id in self.customers:
            self.customers[customer.id].loyalty_points = new_balance

        message = f"Perfect! Your order #{order.id} has been placed successfully.\n\n"
        message += f"Order Summary:\n"
        for item in items:
            message += f"  {item['quantity']} x {item['name']} - ${item['price'] * item['quantity']:.2f}\n"

        if special_instructions:
            message += f"\nSpecial instructions: {special_instructions}\n"

        message += f"\nSubtotal: ${order.total:.2f}\n"
        message += f"Estimated ready time: {order.estimated_ready.strftime('%I:%M %p')}\n"
        message += f"Loyalty points earned: {points_earned} (New total: {new_balance})\n\n"

        if order.total >= 25:
            message += "You qualify for free delivery!\n"
        else:
            message += f"Delivery fee: $3.00 (free on orders over $25)\n"

        message += "\nThank you for choosing Mr. Singh Pizza! Is there anything else I can help you with?"

        # Log successful order completion
        self.log_call(customer.phone, CallType.NEW_ORDER, CallStatus.COMPLETED,
                     f"Order #{order.id} placed for ${order.total:.2f}")

        return {
            "action": "order_complete",
            "message": message,
            "order": order,
            "loyalty_points_earned": points_earned
        }

def create_voice_response(text: str) -> str:
    """
    Convert text to SSML for better text-to-speech output
    In a real implementation, this would integrate with a TTS service
    """
    # Basic SSML formatting for better speech
    ssml = f'<speak>{text}</speak>'
    return ssml

def main():
    """Main function to run the pizza bot"""
    print("🍕 Starting Mr. Singh Pizza Automated Answering Machine...")

    # Initialize the bot
    bot = PizzaBot()

    # In a real implementation, this would connect to a telephony system
    # For demonstration, we'll simulate some calls

    print("📞 PizzaBot is ready to take calls!")
    print("💡 In a production environment, this would connect to your phone system")
    print("📋 Available commands:")
    print("   - Type 'demo' to see a demonstration")
    print("   - Type 'menu' to see the menu")
    print("   - Type 'help' for help")
    print("   - Type 'quit' to exit")

    while True:
        try:
            user_input = input("\n> ").strip().lower()

            if user_input == 'quit':
                print("👋 Goodbye! Thanks for using Mr. Singh Pizza Bot!")
                break
            elif user_input == 'demo':
                run_demo(bot)
            elif user_input == 'menu':
                show_menu(bot)
            elif user_input == 'help':
                show_help()
            else:
                # Simulate a call
                result = bot.process_call("555-0100", user_input)
                print(f"\n🤖 Bot: {result['message']}")

        except KeyboardInterrupt:
            print("\n👋 Goodbye! Thanks for using Mr. Singh Pizza Bot!")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            print("❌ An error occurred. Please try again.")

def run_demo(bot: PizzaBot):
    """Run a demonstration of the bot's capabilities"""
    print("\n🎯 Running demonstration...")

    # Simulate a new customer calling to order
    print("\n📞 Simulating call from new customer...")
    result = bot.process_call("555-0123", "Hello, I'd like to order a pizza")
    print(f"🤖 Bot: {result['message']}")

    # Simulate providing name
    if result.get("action") == "gather_info":
        result = bot.process_call("555-0123", "My name is John Smith")
        print(f"🤖 Bot: {result['message']}")

    # Simulate providing phone (already have it from caller ID)
    # Simulate ordering
    result = bot.process_call("555-0123", "I'll have 2 Margherita pizzas and 1 garlic bread")
    print(f"🤖 Bot: {result['message']}")

    # Simulate confirming order
    if result.get("action") == "get_instructions":
        result = bot.process_call("555-0123", "No special instructions please")
        print(f"🤖 Bot: {result['message']}")

    # Simulate confirming
    if result.get("action") == "confirm_order":
        result = bot.process_call("555-0123", "yes")
        print(f"🤖 Bot: {result['message']}")

def show_menu(bot: PizzaBot):
    """Display the menu"""
    print("\n🍕 Mr. Singh Pizza Menu:")
    print("=" * 50)

    categories = {}
    for item in bot.menu_items.values():
        if item.available:
            if item.category not in categories:
                categories[item.category] = []
            categories[item.category].append(item)

    for category, items in categories.items():
        print(f"\n{category.upper()}:")
        print("-" * len(category))
        for item in items:
            print(f"  {item.id:2d}. {item.name:<20} ${item.price:>5.2f}")
            print(f"      {item.description}")

def show_help():
    """Display help information"""
    print("\n📋 Mr. Singh Pizza Bot Help:")
    print("=" * 30)
    print("Commands:")
    print("  demo   - Run a demonstration")
    print("  menu   - Show the full menu")
    print("  help   - Show this help")
    print("  quit   - Exit the program")
    print("\nYou can also talk to the bot naturally:")
    print("  - 'I want to order pizza'")
    print("  - 'What's on the menu?'")
    print("  - 'What are your hours?'")
    print("  - 'I want to make a reservation'")
    print("  - 'Where is my order #123?'")

if __name__ == "__main__":
    main()