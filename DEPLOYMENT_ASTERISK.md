# Self-Hosted Telephony: FreePBX + Asterisk Deployment Guide

This is the alternative to `DEPLOYMENT.md` (Twilio) for running the answering
machine on your **own SIP trunk and PBX** instead of Twilio - full control of
the infrastructure, plus a real staff phone system (extensions, voicemail,
transfers) alongside the AI order line.

**Read this first:** on Twilio, speech-to-text and text-to-speech came free
with `<Gather>`/`<Say>`. Here, `src/telephony_asterisk/` does that work
itself (via Deepgram/Polly or offline Whisper/Piper), and you're responsible
for running and securing the PBX. It's more setup and ongoing ops than
Twilio in exchange for control and a real phone system. The order-taking
logic itself - `src/conversation.py` and `src/main.py`'s `PizzaBot` - is
**identical** to the Twilio path; only the transport underneath changes.

## Architecture

```
Caller ──PSTN──> SIP trunk (VoIP.ms, CA DID) ──SIP──> FreePBX/Asterisk
                                                          │
                        staff extensions / voicemail / transfers / IVR
                                                          │
                                          inbound route → Stasis(pizzabot)
                                                          ▼
                                    ARI client  ──(AUDIOSOCKET_UUID var)──┐
                              (src/telephony_asterisk/ari_client.py)      │
                                                                          ▼
                                        dialplan continues → AudioSocket(uuid, host:port)
                                                          │
                                     AudioSocket media server (:9092)
                              (src/telephony_asterisk/media_server.py)
                                    VAD → STT → conversation.py → TTS
                                                          │
                                     PizzaBot (orders/menu/locations, SQLite) ← same DB as Twilio path
                                                          │
                                     Kitchen dashboard (src/app.py, unchanged)
```

Two Python processes run together: `src/app.py` (unchanged - still serves
the kitchen dashboard and JSON API) and `src/telephony_asterisk/server.py`
(new - answers calls). Both share the same SQLite database.

**Why ARI *and* AudioSocket, not just one:** AudioSocket's own wire protocol
only carries a UUID - it has no caller ID or dialed-DID field. ARI (Asterisk
REST Interface) is how we capture that from the channel *before* the call
reaches AudioSocket, and it's also how we'd redirect a call to a staff ring
group for human handoff. AudioSocket alone would be simpler but would lose
returning-customer lookup and multi-location routing by dialed number.

## Prerequisites

- A server (VPS or on-prem) for FreePBX. **Ubuntu 22.04** is the most common
  base for a current FreePBX/Asterisk install (18-20.x). 2 vCPU / 4GB RAM is
  enough to start.
- A **Canadian SIP trunk account** - VoIP.ms is the usual budget choice
  (~CAD $1/mo per DID, ~$0.005-0.01/min). Telnyx is a solid alternative with
  a more developer-oriented dashboard.
- The app's normal server (can be the same box or a separate small one) for
  `src/app.py` and `src/telephony_asterisk/server.py`.
- Either a **Deepgram** account (STT) and an **AWS** account (Polly TTS), or
  see "Going fully offline" below to skip both.

---

## Part 1 - FreePBX / Asterisk setup

If FreePBX is already installed and running (staff extensions already
exist), skip to **1.3**.

### 1.1 Install FreePBX

Follow FreePBX's own installer (https://www.freepbx.org/downloads/) for a
supported OS - this isn't specific to this project and is well documented
upstream. Confirm you can log into the FreePBX admin GUI before continuing.

### 1.2 Staff phone system basics

Under **Applications**, set up what the restaurant needs: **Extensions**
for each staff phone, a **Ring Group** for "kitchen"/"front counter" (note
its extension number - you'll want it later for human handoff), and
**Voicemail**. This is standard FreePBX administration, not specific to
this integration.

### 1.3 Connect the VoIP.ms trunk

**Connectivity → Trunks → Add Trunk → PJSIP Trunk.** From your VoIP.ms
account's sub-account credentials, fill in:
- SIP Server: the VoIP.ms server for your region (e.g. `atlanta2.voip.ms`,
  `toronto.voip.ms` - pick the closest POP).
- Username / Secret: your VoIP.ms sub-account credentials.
- Register this trunk.

Buy a Canadian DID in the VoIP.ms portal (or port the client's existing
number - see the porting notes from the Twilio guide, the process is
similar for any Canadian carrier). Point that DID's routing at your
FreePBX trunk's IP in the VoIP.ms portal.

### 1.4 Create the ARI user

**Settings → Asterisk REST Interface → Add ARI User.** Username `pizzabot`
(or your choice), set a strong password - this is a credential the Python
service will authenticate with, so treat it like an API key. Note the
username/password for the `.env` file in Part 2.

### 1.5 The dialplan hook

This is the one piece of custom Asterisk config this integration needs. In
**Admin → Config Edit** (enable it under Advanced Settings if hidden), edit
`extensions_custom.conf` and add:

```ini
; Sends a call into our ARI app (src/telephony_asterisk/ari_client.py),
; which captures caller ID + dialed DID, then continues here once ready.
[ai-order]
exten => s,1,NoOp(AI order line - entering Stasis)
 same => n,Stasis(pizzabot)
 same => n,Hangup()

; The ARI app continues the dialplan to THIS context once it has captured
; the caller info and stashed a UUID on the channel as ${AUDIOSOCKET_UUID}.
; This must match TelephonyConfig's dialplan_context/dialplan_extension
; (defaults: ai-order-media / s).
[ai-order-media]
exten => s,1,NoOp(Bridging to AudioSocket media server)
 same => n,AudioSocket(${AUDIOSOCKET_UUID},${AUDIOSOCKET_HOST}:${AUDIOSOCKET_PORT})
 same => n,Hangup()
```

Set `AUDIOSOCKET_HOST`/`AUDIOSOCKET_PORT` as **global variables**
(`Admin → Config Edit → globals_custom.conf`, or hardcode the host:port
directly in the dialplan line above) pointing at wherever
`telephony_asterisk/server.py` runs - `127.0.0.1:9092` if it's on the same
box as Asterisk (recommended: keeps the audio path local, lowest latency).

### 1.6 Route calls to the AI line

**Connectivity → Inbound Routes.** For the DID that should reach the bot,
set **Destination → Custom Destination**, pointing at `ai-order,s,1` (the
context/extension/priority from 1.5). If the restaurant wants one number to
reach *either* a human or the bot, put a FreePBX **IVR** in front instead
("Press 1 to order, press 2 for the front counter") with option 1 pointing
at the same Custom Destination.

For **multi-location** support (the app already has this, see the main
README), either give each store its own DID with its own Custom
Destination, or route all locations through the same one and let the app's
existing spoken "which location?" flow handle it (set the `ARI_DID_VARIABLE`
env var to whatever channel variable your inbound route sets with the
dialed DID, if you want auto-routing by number instead).

### 1.7 Human handoff (optional, Phase 3)

To let the bot transfer a caller to a person, set `HANDOFF_ENDPOINT` in the
Python service's environment to your ring group, e.g.
`Local/600@from-internal` (600 = the ring group's extension from 1.2). The
`AriClient.redirect()` method in `ari_client.py` implements the primitive;
wiring a specific trigger phrase ("talk to a person") into
`conversation.py`'s dialogue is a small follow-up piece - flag it if you
want it built out before going live, since it hasn't been exercised against
a live PBX yet.

---

## Part 2 - Running the Python telephony service

### 2.1 Install dependencies

```bash
pip install -r requirements.txt -r requirements-asterisk.txt
```

### 2.2 Environment variables

Add to `.env` (see `config.example.env` for the base app's variables; these
are additional):

```bash
# AudioSocket media server - must match the dialplan's AUDIOSOCKET_HOST/PORT
AUDIOSOCKET_HOST=0.0.0.0
AUDIOSOCKET_PORT=9092

# ARI - must match the user created in step 1.4
ARI_BASE_URL=http://127.0.0.1:8088/ari
ARI_WS_URL=ws://127.0.0.1:8088/ari/events
ARI_USERNAME=pizzabot
ARI_PASSWORD=<the strong password from step 1.4>
ARI_APP_NAME=pizzabot
AUDIOSOCKET_DIALPLAN_CONTEXT=ai-order-media
AUDIOSOCKET_DIALPLAN_EXTENSION=s

# Speech providers (recommended defaults)
STT_PROVIDER=deepgram
DEEPGRAM_API_KEY=<from https://console.deepgram.com>
TTS_PROVIDER=polly
POLLY_REGION=us-east-1
POLLY_VOICE_ID=Joanna
# AWS credentials for Polly: standard AWS env vars/credentials file, e.g.
# AWS_ACCESS_KEY_ID=... / AWS_SECRET_ACCESS_KEY=...

# Optional: human handoff (see 1.7)
HANDOFF_ENDPOINT=Local/600@from-internal
```

If Asterisk's ARI is on a different host than this service, change
`ARI_BASE_URL`/`ARI_WS_URL` accordingly and make sure port 8088 (ARI) is
reachable only from trusted hosts (see Security below).

### 2.3 Run it

```bash
python src/telephony_asterisk/server.py
```

Run this **alongside** `src/app.py` (the dashboard), not instead of it -
they're independent processes sharing one database. In production, two
systemd units:

```ini
# /etc/systemd/system/pizza-dashboard.service
[Unit]
Description=Mr. Singh Pizza kitchen dashboard
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

```ini
# /etc/systemd/system/pizza-telephony.service
[Unit]
Description=Mr. Singh Pizza Asterisk telephony transport
After=network.target asterisk.service
[Service]
WorkingDirectory=/opt/pizza-bot
EnvironmentFile=/opt/pizza-bot/.env
ExecStart=/opt/pizza-bot/venv/bin/python src/telephony_asterisk/server.py
Restart=always
User=pizza
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now pizza-dashboard pizza-telephony
sudo journalctl -u pizza-telephony -f
```

---

## Part 3 - Testing

Do these in order - each isolates one layer, so if something's wrong you
know which piece to look at.

1. **PBX alone:** register two softphones (e.g. Zoiper, Linphone) as staff
   extensions, call between them, confirm audio both ways, leave/retrieve
   voicemail, do a blind transfer.
2. **Trunk alone:** call the DID from a real mobile phone; confirm it rings
   into FreePBX (route it to a staff extension temporarily if the AI route
   isn't wired yet).
3. **ARI wiring:** with `pizza-telephony` running and `journalctl -u
   pizza-telephony -f` open, call the DID (now routed per 1.6). You should
   see `Stasis call ... caller=... dialed=... -> audiosocket uuid=...` in
   the log, confirming ARI captured the call and handed it to AudioSocket.
4. **Full order:** stay on the call - you should hear the bot's greeting,
   then be able to place a full order by voice. Confirm the order lands on
   `/staff` (the same dashboard as the Twilio path) with the right location.
5. **Load/latency:** note how long from when you stop talking to when the
   bot starts replying. If it feels sluggish, see Tuning below.

The `tests/test_asterisk_*.py` suite (`pytest`) exercises the AudioSocket
protocol, VAD turn-taking, the call agent, ARI orchestration, and a full
voice order over a real local TCP connection - all without a live Asterisk.
Run it after any code changes here; it won't catch FreePBX-side
misconfiguration (steps 1-3 above are what catches that).

---

## Going fully offline (optional)

To avoid Deepgram/Polly entirely (matching the "full on-prem control" goal
completely - no third-party audio processing at all):

- **STT:** run Whisper locally (e.g. via `faster-whisper`) and add a small
  `WhisperSTT` class implementing the same `SpeechToText` interface in
  `stt.py` - a natural follow-up once the cloud path is validated.
- **TTS:** set `TTS_PROVIDER=piper` and `PIPER_MODEL_PATH` to a downloaded
  Piper voice (`.onnx` file from https://github.com/rhasspy/piper) -
  already implemented in `tts.py`.

Both need meaningfully more server hardware (Whisper benefits a lot from a
GPU) and will have somewhat lower accuracy/naturalness than the cloud
providers, which is the tradeoff for zero per-call cost and zero third-party
audio exposure.

---

## Security checklist

Running your own PBX makes you responsible for SIP security - this isn't
optional hardening, it's baseline (an exposed SIP trunk gets toll-fraud
attacks within hours of being reachable):

- **Firewall the SIP/RTP ports** (5060, 10000-20000 by default) to only
  your SIP trunk provider's IP ranges (VoIP.ms publishes theirs) plus any
  remote extensions you actually use.
- **fail2ban** with FreePBX's Asterisk jail enabled (on by default in
  recent FreePBX) to block repeated failed registration attempts.
- **Never expose ARI (port 8088) or the AudioSocket port (9092) to the
  internet** - both should only be reachable from `127.0.0.1` or your
  private network, since they have no purpose being internet-facing.
- **Set call/spend limits** on the VoIP.ms trunk (max concurrent calls,
  daily spend cap) as a backstop against toll fraud.
- **Strong ARI and extension passwords** - the ARI password in particular
  is effectively an API key with call-control power.
- **TLS/SRTP** for any remote (off-LAN) extensions - not needed for the
  AI line itself, which stays local.

## Relationship to the Twilio path

You don't have to pick one forever. `src/app.py`'s Twilio `/voice` routes
and `src/telephony_asterisk/` can both exist in the repo; run whichever
transport is live for a given deployment. A sensible migration: keep Twilio
live while standing up FreePBX and testing the Asterisk path in parallel
(different DID), then cut the production number over once you're confident,
rather than a hard switch.
