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

    # -- Twilio voice webhooks --------------------------------------------

    @app.post("/voice")
    def voice():
        if not twilio_request_is_valid():
            abort(403)
        call_sid = request.values.get("CallSid", "local-test")
        caller = request.values.get("From", "")
        session = manager.get_session(call_sid, caller)
        return voice_reply(manager.greeting(session))

    @app.post("/voice/collect")
    def collect():
        if not twilio_request_is_valid():
            abort(403)
        call_sid = request.values.get("CallSid", "local-test")
        caller = request.values.get("From", "")
        speech = request.values.get("SpeechResult", "")
        session = manager.get_session(call_sid, caller)
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
