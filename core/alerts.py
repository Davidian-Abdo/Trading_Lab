"""
core/alerts.py

Two ways the system talks to YOU:
  alert(msg)      -> pushes a message to your phone via Telegram (if configured)
  heartbeat()     -> pings an external uptime monitor; if the pings STOP,
                      that external service alerts you. Silence is the real
                      danger with unattended bots, this catches it.

Both degrade gracefully to just logging if not configured, so the bot
never crashes because Telegram is down.

v2.1 latest applied fixes :

Review (medium): alert() logs the full message at WARNING and posts it to
Telegram. If a token/secret ever ends up in a message string by accident
it would land in stdout / docker logs. We now redact anything that looks
like a long key/token before logging or sending.
"""

import logging
import re
import requests

log = logging.getLogger("alerts")

# redact long hex/base64-ish runs and obvious key=... patterns
_REDACT = [
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"),
    re.compile(r"(?i)(token|secret|api[_-]?key|password)\s*[=:]\s*\S+"),
]


def _safe(msg: str) -> str:
    out = msg
    for pat in _REDACT:
        out = pat.sub("«redacted»", out)
    return out


def alert(msg: str, token: str = "", chat: str = "") -> None:
    safe = _safe(msg)
    log.warning("ALERT: %s", safe)
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": safe[:4000]},
            timeout=10,
        )
    except Exception as e:
        log.error("Telegram alert failed: %s", e)


def heartbeat(url: str = "") -> None:
    if not url:
        return
    try:
        requests.get(url, timeout=10)
    except Exception as e:
        log.error("Heartbeat ping failed: %s", e)