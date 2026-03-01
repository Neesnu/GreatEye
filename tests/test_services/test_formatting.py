"""Tests for display formatting utilities."""
from datetime import datetime, timedelta

from src.utils.formatting import format_bytes, format_eta, format_speed, format_timestamp


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(500) == "500 B"

    def test_kilobytes(self):
        assert format_bytes(1536) == "1.5 KB"

    def test_megabytes(self):
        assert format_bytes(5242880) == "5.0 MB"

    def test_gigabytes(self):
        assert format_bytes(1073741824) == "1.0 GB"

    def test_terabytes(self):
        assert format_bytes(1099511627776) == "1.0 TB"

    def test_none(self):
        assert format_bytes(None) == "—"

    def test_negative(self):
        assert format_bytes(-1) == "—"

    def test_zero(self):
        assert format_bytes(0) == "0 B"


class TestFormatSpeed:
    def test_kilobytes_per_second(self):
        result = format_speed(1536)
        assert "1.5 KB/s" == result

    def test_none(self):
        assert format_speed(None) == "—"


class TestFormatEta:
    def test_seconds(self):
        assert format_eta(45) == "45s"

    def test_minutes(self):
        assert format_eta(125) == "2m 5s"

    def test_exact_minutes(self):
        assert format_eta(120) == "2m"

    def test_hours(self):
        assert format_eta(3665) == "1h 1m"

    def test_exact_hours(self):
        assert format_eta(3600) == "1h"

    def test_done(self):
        assert format_eta(0) == "done"

    def test_none(self):
        assert format_eta(None) == "—"

    def test_negative(self):
        assert format_eta(-1) == "—"


class TestFormatTimestamp:
    def test_just_now(self):
        assert format_timestamp(datetime.utcnow()) == "just now"

    def test_minutes_ago(self):
        dt = datetime.utcnow() - timedelta(minutes=5)
        assert format_timestamp(dt) == "5m ago"

    def test_hours_ago(self):
        dt = datetime.utcnow() - timedelta(hours=3)
        assert format_timestamp(dt) == "3h ago"

    def test_days_ago(self):
        dt = datetime.utcnow() - timedelta(days=2)
        assert format_timestamp(dt) == "2d ago"

    def test_none(self):
        assert format_timestamp(None) == "—"
