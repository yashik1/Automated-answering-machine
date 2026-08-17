"""
Self-hosted telephony transport for the Mr. Singh Pizza answering machine.

This package is the FreePBX/Asterisk equivalent of ``src/app.py``'s Twilio
webhooks: it answers phone calls and drives the same conversation engine
(``src/conversation.py``), but over a self-hosted SIP trunk instead of
Twilio. See ``DEPLOYMENT_ASTERISK.md`` at the project root for the full
setup runbook (FreePBX dialplan, ARI user, environment variables).
"""
