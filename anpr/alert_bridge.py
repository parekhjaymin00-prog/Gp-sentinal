"""Alert bridge: reuse the EXISTING email + dashboard alert pipeline.

On a watchlist hit this module

  1. logs one row into the existing ``incidents`` table via
     ``database.db.log_incident`` (shows up on the dashboard's GET /alerts), and
  2. calls the existing ``detectors.alert_system.send_email_alert`` which owns
     the per-type cooldown, snapshot logic, and Gmail delivery.

No cooldown logic is duplicated here: ``send_email_alert`` enforces it.
"""

from __future__ import annotations

import time

from anpr.config import DATABASE_PATH, WATCHLIST_ALERT_COOLDOWN_SECONDS, WATCHLIST_ALERT_TYPE
from anpr.watchlist import WatchlistMatch
from database.db import log_incident

# Register the cooldown override for the new alert type inside the EXISTING
# engine's override table (idempotent import-time registration).
from detectors import alert_system as _alert_system  # noqa: E402

_alert_system.ALERT_COOLDOWN_OVERRIDES.setdefault(
    WATCHLIST_ALERT_TYPE, WATCHLIST_ALERT_COOLDOWN_SECONDS
)


def emit_watchlist_alert(
    match: WatchlistMatch,
    camera_id,
    timestamp_pts_ms: float | None,
    frame=None,
    db_path=DATABASE_PATH,
) -> bool:
    """Log + email one watchlist match. Returns True when an email was sent.

    ``timestamp_pts_ms`` is the CAP_PROP_POS_MSEC media timestamp of the frame
    the match came from (RTSP PTS). The incidents row carries it inside
    ``details``; the wall clock is only used for the column default.
    """
    pts_text = (
        f"{timestamp_pts_ms:.0f}ms" if timestamp_pts_ms is not None else "unknown-pts"
    )
    details = (
        f"plate={match.entry.plate_number} "
        f"ocr='{match.ocr_text}' edit_distance={match.distance} "
        f"camera_id={camera_id} pts={pts_text} reason='{match.entry.reason}'"
    )

    # 1) Dashboard entry through the existing database helper.
    log_incident(WATCHLIST_ALERT_TYPE, match.confidence, details, db_path=db_path)
    print(f"[ANPR ALERT LOGGED] {details}")

    # 2) Email through the existing engine (cooldown enforced inside).
    sent = _alert_system.send_email_alert(
        detection_type=WATCHLIST_ALERT_TYPE,
        confidence=match.confidence,
        timestamp=time.time(),
        frame=frame,
        details=details,
    )
    if sent:
        print("[ANPR ALERT EMAIL] accepted by existing alert engine")
    else:
        print(
            "[ANPR ALERT EMAIL] not sent (cooldown active, unconfigured, or failed)",
        )
    return sent
