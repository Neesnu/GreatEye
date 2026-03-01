from datetime import datetime


def format_bytes(value: int | float | None) -> str:
    """Format a byte count into a human-readable string."""
    if value is None:
        return "—"
    value = float(value)
    if value < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} EB"


def format_speed(bytes_per_second: int | float | None) -> str:
    """Format a byte/second speed into a human-readable string."""
    if bytes_per_second is None:
        return "—"
    return f"{format_bytes(bytes_per_second)}/s"


def format_eta(seconds: int | float | None) -> str:
    """Format seconds remaining into a human-readable ETA string."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds == 0:
        return "done"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def format_timestamp(dt: datetime | None) -> str:
    """Format a datetime into a short display string (UTC)."""
    if dt is None:
        return "—"
    now = datetime.utcnow()
    delta = now - dt

    if delta.total_seconds() < 60:
        return "just now"
    if delta.total_seconds() < 3600:
        minutes = int(delta.total_seconds() // 60)
        return f"{minutes}m ago"
    if delta.total_seconds() < 86400:
        hours = int(delta.total_seconds() // 3600)
        return f"{hours}h ago"
    days = delta.days
    return f"{days}d ago"
