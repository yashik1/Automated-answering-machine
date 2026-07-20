# Deployment & Testing Guide

This guide takes the Mr. Singh Pizza answering machine from your test
machine to a live phone line on the client's machine.

There are three layers:

1. **The bot core** (`src/main.py`) — menu, orders, customers, SQLite.
2. **The conversation state machine** (`src/conversation.py`) — turns spoken
   words into replies and drives the order flow.
3. **The web/phone server** (`src/app.py`) — a Flask app exposing Twilio
   voice webhooks, the kitchen dashboard, a small website, and a JSON API.

Phone orders flow straight to a live **kitchen dashboard** at `/staff`, where
staff advance each order through received → preparing → ready → out for
delivery → completed.

Twilio provides the phone number, converts the caller's speech to text, and
speaks our replies. Our server only has to answer HTTP webhooks with TwiML.

```
Caller ──dials──> Twilio number ──HTTPS POST /voice──> our Flask server
       <──speech──                <──── TwiML reply ────
```

---

## 1. Test it on your machine (no phone needed)

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

pytest                            # 30 tests: bot logic + full voice flow
python src/app.py                 # starts the server on http://localhost:5000
```

With the server running, simulate a call from another terminal exactly the
way Twilio would (form POSTs with `CallSid`, `From`, `SpeechResult`):

```bash
# Incoming call
curl -X POST localhost:5000/voice -d "CallSid=CA1&From=555-0101"

# Caller speaks, one turn at a time (reuse the same CallSid)
curl -X POST localhost:5000/voice/collect -d "CallSid=CA1&From=555-0101&SpeechResult=I want to order"
curl -X POST localhost:5000/voice/collect -d "CallSid=CA1&From=555-0101&SpeechResult=two margherita pizzas"
curl -X POST localhost:5000/voice/collect -d "CallSid=CA1&From=555-0101&SpeechResult=that's all"
curl -X POST localhost:5000/voice/collect -d "CallSid=CA1&From=555-0101&SpeechResult=yes"
```

Each response is TwiML — the `<Say>` text is what the caller would hear.
You can also open `http://localhost:5000/` for the website and
`http://localhost:5000/menu` for the JSON menu.

---

## 2. Test with a real phone (your machine + Twilio trial)

1. Create a free Twilio account and buy (or use the trial) phone number.
2. Install a tunnel so Twilio can reach your laptop:
   ```bash
   # ngrok (https://ngrok.com) — or use cloudflared
   ngrok http 5000
   ```
   Copy the `https://<something>.ngrok-free.app` URL it prints.
3. Set your credentials and start the server:
   ```bash
   export TWILIO_AUTH_TOKEN=your_real_token   # enables request validation
   python src/app.py
   ```
4. In the Twilio console, open your number → **Voice Configuration** →
   "A call comes in" → **Webhook**, set it to:
   ```
   https://<something>.ngrok-free.app/voice        Method: HTTP POST
   ```
5. Call your Twilio number from any phone and place an order.

> Twilio trial accounts can only call verified numbers and prepend a trial
> notice. Upgrade the account to remove those limits.

---

## 3. Deploy on the client's machine (production)

### 3a. Run it as a proper server

Do **not** use `python src/app.py` (Flask's dev server) in production. Use
gunicorn (already in `requirements.txt`), via the included `Procfile`:

```bash
gunicorn --chdir src "app:create_app()" --bind 0.0.0.0:5000 --workers 1
```

Keep `--workers 1` for now: in-progress call state is held in memory, so
multiple workers would not share it. To scale to multiple workers later,
move that state to Redis (see `ConversationManager.sessions`).

### 3b. Keep it running (systemd, Linux)

Create `/etc/systemd/system/pizza-bot.service`:

```ini
[Unit]
Description=Mr. Singh Pizza answering machine
After=network.target

[Service]
WorkingDirectory=/opt/pizza-bot
EnvironmentFile=/opt/pizza-bot/.env
ExecStart=/opt/pizza-bot/venv/bin/gunicorn --chdir src "app:create_app()" --bind 0.0.0.0:5000 --workers 1
Restart=always
User=pizza

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now pizza-bot
sudo journalctl -u pizza-bot -f      # view logs
```

On Windows, run the same gunicorn/waitress command under NSSM or Task
Scheduler so it restarts on boot.

### 3c. Make it reachable over HTTPS

Twilio requires a public HTTPS URL. Options, easiest first:

- **A cloud host** (Render, Railway, Fly.io, a small VPS). This is usually
  simpler and more reliable than exposing the client's own machine.
- **The client's machine + a tunnel** (`cloudflared tunnel`, ngrok paid) if
  the bot must run on-premises.
- **The client's machine behind a reverse proxy** (nginx + Let's Encrypt)
  with a static IP / domain and a firewall port-forward.

Point the Twilio number's Voice webhook at `https://<public-host>/voice`.

### 3d. The kitchen dashboard

Staff open `https://<public-host>/staff` on any tablet or screen in the
kitchen. It polls for orders every few seconds, shows items / customer /
total / special instructions, and has buttons to advance each order's
status. New orders flash and (once the "Sound" button is switched on) chime.

Protect it with HTTP Basic auth by setting these in the environment / `.env`:

```
STAFF_USERNAME=kitchen
STAFF_PASSWORD=choose-a-strong-password
```

If `STAFF_PASSWORD` is unset the dashboard is open (fine for local testing,
not for production). The dashboard uses these API endpoints, which are
guarded by the same auth:

- `GET /api/orders` — active orders (`?include_completed=1` for the full list)
- `POST /api/orders/<id>/status` — set an order's status

### 3e. Security checklist

- **Set `TWILIO_AUTH_TOKEN`** in the environment so the server rejects any
  request not signed by Twilio (returns 403). Without it, anyone who finds
  the URL can drive the bot.
- **Set `STAFF_PASSWORD`** so the kitchen dashboard isn't publicly open.
- Serve only over HTTPS.
- Never commit `.env` (already covered by `.gitignore`).

### 3f. Data & backups

- Real customer and order data lives in `data/pizza_bot.db`. Back it up on a
  schedule (e.g. a nightly `cp`/`sqlite3 .backup` to off-machine storage).
- Logs are written to `logs/pizza_bot.log`; rotate them (logrotate) so they
  don't grow unbounded.

---

## What still needs a human / future work

- **Payment** is not collected on the call; orders are placed as "received".
  Integrate a payment step or take payment on pickup/delivery.
- **Reservations and complaints** are acknowledged and flagged for a
  callback, not fully automated.
- The order parser is keyword-based. It handles common phrasings ("two
  margherita pizzas", "a garlic bread") but a production system may want a
  proper NLU service for messy speech.
