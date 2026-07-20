"""
Flask web application for the Mr. Singh Pizza answering machine.

Exposes the Twilio voice webhooks that let the bot answer real phone calls,
plus a small informational website and JSON API.

Twilio call flow:

    Incoming call ---> POST /voice           (greeting + <Gather> speech)
                       POST /voice/collect   (each caller utterance)

Run locally:

    python src/app.py

Then expose it to Twilio with a public HTTPS tunnel (see README) and point
your Twilio number's Voice webhook at  https://<public-host>/voice  (POST).
"""

import os
import datetime

from flask import Flask, request, render_template, jsonify, Response, abort
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.request_validator import RequestValidator

from main import PizzaBot, BASE_DIR
from conversation import ConversationManager

TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Business info shown on the website / spoken to callers.
RESTAURANT_PHONE = os.getenv("RESTAURANT_PHONE", "+1 (234) 567-890")
RESTAURANT_ADDRESS = os.getenv(
    "RESTAURANT_ADDRESS", "123 Pizza Street, Food City, FC 12345"
)

# Kitchen order lifecycle. The dashboard advances an order along these.
ORDER_STATUSES = [
    "received", "preparing", "ready", "out_for_delivery", "completed", "cancelled",
]


def _parse_dt(value):
    """Parse a SQLite timestamp string (space- or T-separated) to a datetime."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def create_app(bot: PizzaBot = None) -> Flask:
    app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
    bot = bot or PizzaBot()
    manager = ConversationManager(bot)

    # -- Twilio request authenticity --------------------------------------

    def twilio_request_is_valid() -> bool:
        """Validate the X-Twilio-Signature when an auth token is configured.

        If TWILIO_AUTH_TOKEN is unset (e.g. local dev or tests) validation is
        skipped so the endpoints remain callable, but a warning is logged at
        startup. In production you MUST set the token.
        """
        token = os.getenv("TWILIO_AUTH_TOKEN")
        if not token or app.config.get("TESTING"):
            return True
        validator = RequestValidator(token)
        signature = request.headers.get("X-Twilio-Signature", "")
        # Honour proxy headers so the URL matches what Twilio signed.
        url = request.url
        return validator.validate(url, request.form, signature)

    # -- Staff dashboard auth ---------------------------------------------

    def staff_authorized() -> bool:
        """Optional HTTP Basic auth for the kitchen dashboard.

        If STAFF_PASSWORD is unset (local dev/tests) the dashboard is open.
        If it is set, the browser must supply matching Basic-auth creds; the
        browser then resends them automatically to the /api/orders calls.
        """
        password = os.getenv("STAFF_PASSWORD")
        if not password or app.config.get("TESTING_STAFF_OPEN", False):
            return True
        username = os.getenv("STAFF_USERNAME", "staff")
        auth = request.authorization
        return bool(auth and auth.username == username and auth.password == password)

    def require_staff():
        """Return a 401 challenge response if the staff request isn't authed,
        otherwise None."""
        if staff_authorized():
            return None
        resp = Response("Authentication required", 401)
        resp.headers["WWW-Authenticate"] = 'Basic realm="Kitchen dashboard"'
        return resp

    def serialize_order(order: dict) -> dict:
        """Enrich a raw order row for the dashboard: resolve item names and
        add human-friendly times and an elapsed-minutes figure."""
        items = []
        for it in order["items"]:
            name = it.get("name")
            if not name:
                menu_item = bot.get_menu_item(it.get("id"))
                name = menu_item.name if menu_item else f"Item {it.get('id')}"
            items.append({"name": name, "quantity": it.get("quantity", 1)})

        placed = _parse_dt(order.get("order_time"))
        ready = _parse_dt(order.get("estimated_ready"))
        now = datetime.datetime.now()
        minutes_ago = int((now - placed).total_seconds() // 60) if placed else None
        overdue = bool(ready and now > ready and order["status"] not in
                       ("completed", "cancelled"))

        return {
            "id": order["id"],
            "customer_name": order["customer_name"],
            "customer_phone": order["customer_phone"],
            "items": items,
            "total": order["total"],
            "status": order["status"],
            "special_instructions": order["special_instructions"],
            "location_slug": order.get("location_slug"),
            "location_name": order.get("location_name"),
            "placed_at": placed.strftime("%I:%M %p") if placed else "",
            "ready_by": ready.strftime("%I:%M %p") if ready else "",
            "minutes_ago": minutes_ago,
            "overdue": overdue,
        }

    def voice_reply(result) -> Response:
        """Convert a TurnResult into a TwiML response."""
        vr = VoiceResponse()
        if result.expect_reply and not result.hangup:
            gather = Gather(
                input="speech",
                action="/voice/collect",
                method="POST",
                speech_timeout="auto",
                language="en-US",
            )
            gather.say(result.message)
            vr.append(gather)
            # Reached only if the caller says nothing during the Gather.
            vr.say("Sorry, I didn't hear anything. Please call again. Goodbye.")
            vr.hangup()
        else:
            vr.say(result.message)
            vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # -- Website & JSON API -----------------------------------------------

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            phone=RESTAURANT_PHONE,
            address=RESTAURANT_ADDRESS,
            year=datetime.date.today().year,
        )

    @app.get("/health")
    def health():
        return jsonify(status="ok", menu_items=len(bot.menu_items))

    @app.get("/menu")
    def menu():
        return jsonify(menu=[
            {
                "id": i.id,
                "name": i.name,
                "description": i.description,
                "price": i.price,
                "category": i.category,
            }
            for i in bot.menu_items.values() if i.available
        ])

    @app.get("/orders/<int:order_id>")
    def order_status(order_id: int):
        order = bot.get_order_status(order_id)
        if not order:
            return jsonify(error="order not found"), 404
        return jsonify(
            id=order.id,
            status=order.status,
            total=order.total,
            estimated_ready=order.estimated_ready.isoformat() if order.estimated_ready else None,
            items=order.items,
        )

    # -- Kitchen staff dashboard ------------------------------------------

    @app.get("/staff")
    @app.get("/staff/<location_slug>")
    def staff_dashboard(location_slug=None):
        challenge = require_staff()
        if challenge:
            return challenge
        locations = [{"slug": loc.slug, "name": loc.name}
                     for loc in bot.list_locations()]
        return render_template("staff.html", locations=locations,
                               selected_slug=location_slug or "")

    @app.get("/api/locations")
    def api_locations():
        challenge = require_staff()
        if challenge:
            return challenge
        return jsonify(locations=[
            {"slug": loc.slug, "name": loc.name, "address": loc.address}
            for loc in bot.list_locations()
        ])

    @app.get("/api/orders")
    def api_orders():
        challenge = require_staff()
        if challenge:
            return challenge
        include_completed = request.args.get("include_completed", "0") == "1"
        location_slug = request.args.get("location", "").strip()
        location_id = None
        if location_slug:
            loc = bot.get_location_by_slug(location_slug)
            location_id = loc.id if loc else -1   # -1 => unknown slug, match nothing
        raw = bot.list_orders(include_completed=include_completed,
                              location_id=location_id)
        return jsonify(
            orders=[serialize_order(o) for o in raw],
            server_time=datetime.datetime.now().strftime("%I:%M:%S %p"),
        )

    @app.post("/api/orders/<int:order_id>/status")
    def api_update_status(order_id: int):
        challenge = require_staff()
        if challenge:
            return challenge
        data = request.get_json(silent=True) or request.form
        status = (data.get("status") or "").strip()
        if status not in ORDER_STATUSES:
            return jsonify(error=f"invalid status '{status}'"), 400
        if not bot.order_exists(order_id):
            return jsonify(error="order not found"), 404
        bot.update_order_status(order_id, status)
        return jsonify(id=order_id, status=status)

    # -- Twilio voice webhooks --------------------------------------------

    @app.post("/voice")
    def voice():
        if not twilio_request_is_valid():
            abort(403)
        call_sid = request.values.get("CallSid", "local-test")
        caller = request.values.get("From", "")
        dialed = request.values.get("To", "")
        session = manager.get_session(call_sid, caller, dialed_number=dialed)
        return voice_reply(manager.greeting(session))

    @app.post("/voice/collect")
    def collect():
        if not twilio_request_is_valid():
            abort(403)
        call_sid = request.values.get("CallSid", "local-test")
        caller = request.values.get("From", "")
        dialed = request.values.get("To", "")
        speech = request.values.get("SpeechResult", "")
        session = manager.get_session(call_sid, caller, dialed_number=dialed)
        return voice_reply(manager.handle(session, speech))

    if not os.getenv("TWILIO_AUTH_TOKEN"):
        app.logger.warning(
            "TWILIO_AUTH_TOKEN is not set: Twilio request signature validation "
            "is DISABLED. Set it in production so only Twilio can reach /voice."
        )

    return app


# Expose a module-level app for gunicorn:  gunicorn "app:create_app()"
app = None


def main():
    global app
    app = create_app()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("DEBUG", "False").lower() == "true"
    print(f"🍕 Mr. Singh Pizza web/phone server starting on {host}:{port}")
    print(f"   Point your Twilio number's Voice webhook (POST) at /voice")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
