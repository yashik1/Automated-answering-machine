r"""
Conversation state machine for phone calls.

Each Twilio webhook request is a separate HTTP POST, so the conversation
state for an in-progress call is tracked server-side, keyed by Twilio's
CallSid. ``ConversationManager`` wraps a :class:`~main.PizzaBot` and turns a
caller's spoken utterances into spoken replies and state transitions.

The flow:

    greeting -> main -> ordering -> confirm -> (await_name) -> finalize
                     \-> await_order_number
                     \-> menu / hours / reservation / complaint

The manager is transport-agnostic: it returns :class:`TurnResult` objects
describing what to say and whether to keep listening. The Flask layer turns
those into TwiML. This makes the whole conversation testable without Twilio.
"""

import re
from typing import Dict, List, Optional

from main import PizzaBot, CallType


# Spoken-number handling: callers say "two pizzas", not "2 pizzas", and
# Twilio's transcription usually keeps the words.
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

DONE_WORDS = ["that's all", "thats all", "that's it", "thats it", "that is all",
              "nothing else", "no thanks", "no thank you", "im done", "i'm done",
              "done", "finished", "nope", "no more"]
YES_WORDS = ["yes", "yeah", "yep", "yup", "correct", "confirm", "sure",
             "that's right", "thats right", "sounds good", "go ahead", "please do"]
NO_WORDS = ["no", "nope", "wrong", "change", "cancel", "start over"]


def normalize_numbers(text: str) -> str:
    """Replace spoken number words with digits so the order parser can read them."""
    out = []
    for word in text.split():
        key = word.lower().strip(".,!?")
        out.append(str(NUMBER_WORDS[key]) if key in NUMBER_WORDS else word)
    return " ".join(out)


def _contains_any(text: str, phrases: List[str]) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in phrases)


class TurnResult:
    """What the bot wants to do at the end of one conversational turn."""

    def __init__(self, message: str, expect_reply: bool = True, hangup: bool = False):
        self.message = message
        self.expect_reply = expect_reply
        self.hangup = hangup


class CallSession:
    """Mutable state for a single in-progress call."""

    def __init__(self, call_sid: str, caller_number: str):
        self.call_sid = call_sid
        self.caller_number = caller_number
        self.state = "main"
        self.cart: List[Dict] = []
        self.customer = None
        self.location = None            # which store this order is for
        self.dialed_number = None       # the number the caller dialed (Twilio "To")
        self.special_instructions: Optional[str] = None


class ConversationManager:
    """Drives the spoken conversation for every active call."""

    def __init__(self, bot: PizzaBot):
        self.bot = bot
        self.sessions: Dict[str, CallSession] = {}

    # -- session lifecycle -------------------------------------------------

    def get_session(self, call_sid: str, caller_number: str,
                    dialed_number: str = None) -> CallSession:
        session = self.sessions.get(call_sid)
        if session is None:
            session = CallSession(call_sid, caller_number)
            session.dialed_number = dialed_number
            session.customer = self.bot.get_customer_by_phone(caller_number)
            # Optional auto-routing: if the dialed number maps to a location we
            # skip the question. With a single shared number it won't match, so
            # the caller is asked which location instead (see greeting()).
            if dialed_number:
                session.location = self.bot.get_location_by_phone(dialed_number)
            self.sessions[call_sid] = session
        return session

    def end_session(self, session: CallSession):
        self.sessions.pop(session.call_sid, None)

    # -- entry point -------------------------------------------------------

    def greeting(self, session: CallSession) -> TurnResult:
        # Decide which store this call is for. If it wasn't auto-routed from
        # the dialed number, ask the caller (unless there's only one store).
        if session.location is None:
            locations = self.bot.list_locations()
            if len(locations) == 1:
                session.location = locations[0]
            elif len(locations) > 1:
                session.state = "await_location"
                return TurnResult(
                    "Thank you for calling Mr Singh Pizza. Which location would "
                    f"you like to order from? We have "
                    f"{self._location_options_phrase(locations)}."
                )
        session.state = "main"
        return TurnResult(self._main_greeting(session))

    def handle(self, session: CallSession, speech: str) -> TurnResult:
        speech = (speech or "").strip()
        handler = {
            "await_location": self._handle_await_location,
            "main": self._handle_main,
            "ordering": self._handle_ordering,
            "confirm": self._handle_confirm,
            "await_name": self._handle_await_name,
            "await_order_number": self._handle_order_number,
        }.get(session.state, self._handle_main)
        return handler(session, speech)

    def _main_greeting(self, session: CallSession) -> str:
        place = session.location.name if session.location else "Mr Singh Pizza"
        who = f", {session.customer.name}" if session.customer else ""
        return (
            f"Thank you for calling {place}{who}. "
            "You can place an order, check an existing order, hear our menu, "
            "or ask about our hours. How can I help you?"
        )

    def _handle_await_location(self, session: CallSession, speech: str) -> TurnResult:
        location = self._match_location(speech)
        if not location:
            options = self._location_options_phrase(self.bot.list_locations())
            return TurnResult(
                f"Sorry, I didn't catch which location. We have {options}. "
                "Which one would you like?"
            )
        session.location = location
        session.state = "main"
        return TurnResult(
            f"Great, ordering from {self._short_location_label(location)}. "
            "You can place an order, check an existing order, hear our menu, "
            "or ask about our hours. How can I help you?"
        )

    # -- state handlers ----------------------------------------------------

    def _handle_main(self, session: CallSession, speech: str) -> TurnResult:
        if not speech:
            return TurnResult(
                "Sorry, I didn't catch that. You can say 'place an order', "
                "'check my order', 'menu', or 'hours'."
            )

        intent = self.bot.determine_intent(speech)

        if intent == CallType.NEW_ORDER:
            session.state = "ordering"
            return TurnResult(
                "Great. What would you like to order? For example, say "
                "'one margherita pizza and a garlic bread'."
            )
        if intent == CallType.ORDER_STATUS:
            session.state = "await_order_number"
            return TurnResult("Sure. What is your order number?")
        if intent == CallType.MENU_INQUIRY:
            session.state = "ordering"
            return TurnResult(self._spoken_menu() + " What would you like to order?")
        if intent == CallType.HOURS_INQUIRY:
            return TurnResult(
                "We're open Monday to Thursday 11 a.m. to 10 p.m., "
                "Friday and Saturday 11 a.m. to 11 p.m., and Sunday noon to 9 p.m. "
                "Delivery is free within 5 miles on orders over 25 dollars. "
                "Would you like to place an order?"
            )
        if intent == CallType.RESERVATION:
            return TurnResult(
                "I can pass your reservation request to our team and someone will "
                "call you back shortly to confirm. In the meantime, is there "
                "anything else I can help you with, like placing an order?"
            )
        if intent == CallType.COMPLAINT:
            return TurnResult(
                "I'm very sorry to hear that. I've noted your concern and a manager "
                "will call you back. Is there anything else I can help you with?"
            )
        return TurnResult(
            "I can help you place an order, check an order, hear the menu, or "
            "give our hours. Which would you like?"
        )

    def _handle_ordering(self, session: CallSession, speech: str) -> TurnResult:
        if not speech:
            return TurnResult(
                "What would you like to order? Say 'that's all' when you're finished."
            )

        if _contains_any(speech, DONE_WORDS) and session.cart:
            return self._go_to_confirm(session)

        items = self._parse_order(session, speech)
        if not items:
            if session.cart:
                return TurnResult(
                    "Sorry, I didn't recognise that item. You can try again, "
                    "or say 'that's all' to finish your order."
                )
            return TurnResult(
                "Sorry, I didn't catch an item from our menu. What would you like "
                "to order? You can say the pizza or item name."
            )

        session.cart.extend(items)
        summary = ", ".join(f"{i['quantity']} {i['name']}" for i in items)
        return TurnResult(
            f"I've added {summary}. Anything else? Say 'that's all' when you're done."
        )

    def _handle_confirm(self, session: CallSession, speech: str) -> TurnResult:
        # Check "no/cancel" before "yes" so "no thanks" is never read as yes.
        if _contains_any(speech, NO_WORDS):
            session.cart = []
            session.state = "ordering"
            return TurnResult("No problem, let's start over. What would you like to order?")
        if _contains_any(speech, YES_WORDS):
            if session.customer is None:
                session.state = "await_name"
                return TurnResult("Can I get your name for the order?")
            return self._finalize(session)
        return TurnResult("Please say yes to place the order, or no to change it.")

    def _handle_await_name(self, session: CallSession, speech: str) -> TurnResult:
        name = self._extract_name(speech)
        if not name:
            return TurnResult("Sorry, I didn't catch your name. Could you say it again?")
        session.customer = self.bot.create_customer(name, session.caller_number)
        return self._finalize(session)

    def _handle_order_number(self, session: CallSession, speech: str) -> TurnResult:
        if not re.search(r"\d", speech or ""):
            return TurnResult("Sorry, I didn't get a number. What is your order number?")
        result = self.bot.handle_order_status(session.caller_number, speech)
        session.state = "main"
        return TurnResult(self._speakable(result["message"]))

    # -- helpers -----------------------------------------------------------

    def _go_to_confirm(self, session: CallSession) -> TurnResult:
        session.state = "confirm"
        total = sum(i["price"] * i["quantity"] for i in session.cart)
        lines = "; ".join(f"{i['quantity']} {i['name']}" for i in session.cart)
        return TurnResult(
            f"Here's your order: {lines}. Your total is {total:.2f} dollars. "
            "Should I place the order? Please say yes or no."
        )

    def _finalize(self, session: CallSession) -> TurnResult:
        result = self.bot.save_order_and_respond(
            session.customer, session.cart, session.special_instructions,
            location_id=session.location.id if session.location else None,
        )
        order = result["order"]
        eta = order.estimated_ready.strftime("%I:%M %p")
        where = (f" from {self._short_location_label(session.location)}"
                 if session.location else "")
        message = (
            f"Thank you {session.customer.name}. Your order number is {order.id}"
            f"{where}. Your total is {order.total:.2f} dollars and it should be "
            f"ready around {eta}. You earned {result['loyalty_points_earned']} "
            "loyalty points. Thanks for choosing Mr Singh Pizza. Goodbye!"
        )
        self.end_session(session)
        return TurnResult(message, expect_reply=False, hangup=True)

    def _match_location(self, speech: str):
        """Match spoken text to a location by its slug or short name."""
        low = (speech or "").lower()
        best, best_score = None, 0
        for loc in self.bot.list_locations():
            score = 2 if loc.slug.lower() in low else 0
            for word in self._short_location_label(loc).lower().split():
                if len(word) > 2 and word in low:
                    score += 1
            if score > best_score:
                best, best_score = loc, score
        return best if best_score > 0 else None

    @staticmethod
    def _short_location_label(location) -> str:
        """A speakable short name, e.g. 'Downtown' from 'Mr Singh Pizza - Downtown'."""
        name = location.name
        for sep in ("—", "-", ":", "|"):
            if sep in name:
                return name.split(sep)[-1].strip()
        return name.strip()

    def _location_options_phrase(self, locations) -> str:
        labels = [self._short_location_label(loc) for loc in locations]
        if len(labels) <= 1:
            return ", ".join(labels)
        return ", ".join(labels[:-1]) + f", or {labels[-1]}"

    def _parse_order(self, session: CallSession, speech: str) -> List[Dict]:
        """Turn a spoken order into cart items, tolerant of spoken numbers and
        bare item names (assumed quantity 1)."""
        normalized = normalize_numbers(speech)
        result = self.bot.process_order_items(session.customer, normalized)
        if result["action"] != "clarify":
            return result["items"]

        # Fallback: caller named an item without a quantity ("a margherita").
        low = speech.lower()
        items = []
        for menu_item in self.bot.menu_items.values():
            if menu_item.name.lower() in low:
                items.append({
                    "id": menu_item.id,
                    "name": menu_item.name,
                    "price": menu_item.price,
                    "quantity": 1,
                })
        return items

    def _spoken_menu(self) -> str:
        categories: Dict[str, List] = {}
        for item in self.bot.menu_items.values():
            categories.setdefault(item.category, []).append(item)
        parts = []
        for category, items in categories.items():
            names = ", ".join(i.name for i in items[:3])
            parts.append(f"For {category}, we have {names}")
        return "Here are some favourites. " + ". ".join(parts) + "."

    @staticmethod
    def _extract_name(speech: str) -> str:
        if not speech:
            return ""
        name = speech.strip()
        for prefix in ("my name is ", "it's ", "its ", "this is ", "i'm ", "i am ", "name is "):
            if name.lower().startswith(prefix):
                name = name[len(prefix):].strip()
                break
        return name.title()

    @staticmethod
    def _speakable(text: str) -> str:
        """Flatten multi-line bot messages into something TTS reads cleanly."""
        return " ".join(line.strip() for line in text.splitlines() if line.strip())
