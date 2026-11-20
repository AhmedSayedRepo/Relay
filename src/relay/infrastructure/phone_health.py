"""Why a receipt phone went quiet, from the health report its app sends
with each heartbeat (app 0.8.1 and later).

Two kinds of answer:
- why the last silence happened, worked out when the phone checks in again
  after more than an hour (phone off, app closed by Android, no internet);
- what still puts the phone at risk now (battery saving holds the app
  back, the photo folder can no longer be read, battery nearly empty).

Codes, not sentences: the portal shows them in English or Arabic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

GAP = timedelta(hours=1)  # same as alerts.QUIET
# Android's reasons for closing the app, as the phone names them.
KILLED = {"low_memory", "battery_saving", "frozen", "system", "crash"}
STOPPED = {"force_stopped", "user_stopped"}


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def gap_reason(
    report: dict[str, Any], previous: datetime | None, now: datetime
) -> dict[str, Any] | None:
    """Why the phone was silent from ``previous`` to ``now``; None when it
    was not silent for long. ``reason`` is one of phone_off, force_stopped,
    app_killed (with ``detail``: Android's reason), no_internet."""
    if previous is None or now - previous < GAP:
        return None
    base = {"from": previous.isoformat(), "to": now.isoformat()}
    boot = _time(report.get("boot_at"))
    if boot is not None and previous < boot <= now:
        return base | {"reason": "phone_off", "at": boot.isoformat()}
    exits = report.get("exits")
    for item in exits if isinstance(exits, list) else []:
        at = _time(item.get("at")) if isinstance(item, dict) else None
        if at is None or not previous <= at <= now:
            continue
        reason = str(item.get("reason"))
        if reason in STOPPED:
            return base | {"reason": "force_stopped", "at": at.isoformat()}
        if reason in KILLED:
            return base | {
                "reason": "app_killed",
                "detail": reason,
                "at": at.isoformat(),
            }
    # The app kept running and the phone stayed on: it could not reach Relay.
    return base | {"reason": "no_internet"}


def risks(report: dict[str, Any] | None) -> list[str]:
    """What may make the phone go quiet again, most serious first."""
    if not isinstance(report, dict):
        return []
    found: list[str] = []
    if report.get("sms_allowed") is False:
        found.append("sms_denied")
    if report.get("folders_ok") is False:
        found.append("folder_lost")
    # An app set to Unrestricted is exempt from Android's standby buckets: a
    # "rare" bucket (an app not opened often) alone is not a restriction.
    unrestricted = report.get("battery_unrestricted") is True
    if (
        report.get("battery_unrestricted") is False
        or report.get("background_restricted") is True
        or (report.get("standby_bucket") == "restricted" and not unrestricted)
    ):
        found.append("battery_restricted")
    percent = report.get("battery_percent")
    if isinstance(percent, int) and percent <= 15 and report.get("charging") is False:
        found.append("battery_low")
    if report.get("power_save") is True:
        found.append("power_save")
    return found


def stored(
    report: dict[str, Any], previous_seen: datetime | None, now: datetime, old: Any
) -> dict[str, Any]:
    """What to keep on the device: the latest report, and the last silence
    explained (kept until a newer silence replaces it)."""
    kept = {key: value for key, value in report.items() if key != "gap"}
    gap = gap_reason(report, previous_seen, now)
    if gap is None and isinstance(old, dict) and isinstance(old.get("gap"), dict):
        gap = old["gap"]
    if gap is not None:
        kept["gap"] = gap
    return kept


def summary(health: Any) -> dict[str, Any] | None:
    """For the portal and emails: the last silence and current risks."""
    if not isinstance(health, dict):
        return None
    gap = health.get("gap")
    crash = health.get("last_crash")
    return {
        "gap": gap if isinstance(gap, dict) else None,
        "last_crash": crash if isinstance(crash, dict) else None,
        "risks": risks(health),
        "battery_percent": health.get("battery_percent"),
        "charging": health.get("charging"),
        "reported_at": health.get("reported_at"),
    }


WORDS = {
    "phone_off": "the phone was off or restarted",
    "force_stopped": "the app was force-stopped on the phone",
    "app_killed": "Android closed the app",
    "no_internet": "the phone had no internet (or was in deep sleep)",
    "sms_denied": "the app is no longer allowed to receive SMS",
    "folder_lost": "the app can no longer read the photo folder",
    "battery_restricted": "battery saving limits the app",
    "battery_low": "the battery is almost empty",
    "power_save": "the phone is in power saving mode",
}


def describe(info: Any) -> str:
    """One English phrase for emails from ``summary``: the last silence,
    then risks."""
    if not isinstance(info, dict):
        return "no health report yet (app older than 0.8.1)"
    parts = []
    if info["gap"]:
        parts.append(
            "last time " + WORDS.get(str(info["gap"].get("reason")), "unknown")
        )
    parts += [WORDS[code] for code in info.get("risks") or [] if code in WORDS]
    crash = info.get("last_crash")
    if isinstance(crash, dict) and crash.get("error"):
        parts.append(f"last crash {crash.get('at', '')}: {crash['error']}")
    return "; ".join(parts) or "no problem reported"
