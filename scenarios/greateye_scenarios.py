"""
Great Eye Scenario Suite
========================
External behavioral tests for Great Eye.
These scenarios test what the app does, not how it does it.
The app must be running on http://127.0.0.1:8484 before running these.

Scenarios are grouped by build phase. Run with --phase to test a
specific phase, or run all phases at once. Later phases assume
earlier phases pass.

Usage:
    # Run all scenarios
    python scenarios/greateye_scenarios.py

    # Run specific phase
    python scenarios/greateye_scenarios.py --phase 0
    python scenarios/greateye_scenarios.py --phase 2

    # Run with a pre-existing session (skip auto-login)
    python scenarios/greateye_scenarios.py --session <cookie_value>

    # Run with custom base URL
    python scenarios/greateye_scenarios.py --url http://10.0.0.45:8484

Requirements:
    pip install requests
"""

import argparse
import json
import re
import sys
import time

import requests

# ============================================================
# Configuration
# ============================================================

DEFAULT_URL = "http://127.0.0.1:8484"
TEST_ADMIN_USER = "testadmin"
TEST_ADMIN_PASS = "testpassword123"

# ============================================================
# Test Infrastructure
# ============================================================

PASS = []
FAIL = []
SKIP = []
CURRENT_GROUP = ""


def group(name):
    """Set the current scenario group for output formatting."""
    global CURRENT_GROUP
    CURRENT_GROUP = name


def check(name, condition, skip_reason=None, detail=None):
    """Record a scenario result."""
    full_name = f"[{CURRENT_GROUP}] {name}" if CURRENT_GROUP else name
    if skip_reason:
        SKIP.append((full_name, skip_reason))
    elif condition:
        PASS.append(full_name)
    else:
        FAIL.append((full_name, detail or ""))


def get(path, session=None, allow_redirects=False, base_url=DEFAULT_URL):
    """HTTP GET with optional session cookie."""
    cookies = {"session_id": session} if session else {}
    return requests.get(
        base_url + path,
        cookies=cookies,
        allow_redirects=allow_redirects,
        timeout=10,
    )


def post(path, data=None, json_data=None, session=None,
         allow_redirects=False, base_url=DEFAULT_URL):
    """HTTP POST with optional session cookie."""
    cookies = {"session_id": session} if session else {}
    return requests.post(
        base_url + path,
        data=data,
        json=json_data,
        cookies=cookies,
        allow_redirects=allow_redirects,
        timeout=10,
    )


def htmx_get(path, session=None, base_url=DEFAULT_URL):
    """HTTP GET with HX-Request header (simulates HTMX partial request)."""
    cookies = {"session_id": session} if session else {}
    return requests.get(
        base_url + path,
        cookies=cookies,
        headers={"HX-Request": "true"},
        allow_redirects=False,
        timeout=10,
    )


def login(base_url=DEFAULT_URL, username=TEST_ADMIN_USER,
          password=TEST_ADMIN_PASS):
    """Attempt login and return session cookie value, or None."""
    try:
        r = requests.post(
            base_url + "/auth/login",
            data={"username": username, "password": password},
            allow_redirects=False,
            timeout=10,
        )
        cookie = r.cookies.get("session_id")
        if cookie:
            return cookie
        # Some implementations set cookie on the redirect target
        if r.status_code in (302, 303):
            r2 = requests.get(
                base_url + r.headers.get("location", "/"),
                cookies=r.cookies,
                allow_redirects=False,
                timeout=10,
            )
            return r2.cookies.get("session_id") or r.cookies.get("session_id")
    except Exception:
        pass
    return None


# ============================================================
# PHASE 0: Project Skeleton
# ============================================================

def phase_0(base_url, session):
    group("Phase 0 — Skeleton")

    # App is reachable
    try:
        r = requests.get(base_url, allow_redirects=True, timeout=5)
        check("App is reachable", True)
    except Exception:
        check("App is reachable", False,
              detail="Could not connect to " + base_url)
        return False  # Fatal — can't continue

    # Static CSS loads
    r = get("/static/css/greateye.css", base_url=base_url)
    check("CSS file is served", r.status_code == 200)
    if r.status_code == 200:
        check(
            "CSS contains dark theme variables",
            "--bg-primary" in r.text and "--color-health-up" in r.text,
            detail="Expected CSS custom properties for dark theme",
        )

    # Static JS loads
    r = get("/static/js/htmx.min.js", base_url=base_url)
    check("HTMX JS is served", r.status_code == 200)

    # App returns HTML (not JSON, not plaintext)
    r = requests.get(base_url, allow_redirects=True, timeout=5)
    check(
        "App returns HTML content",
        "text/html" in r.headers.get("content-type", ""),
    )

    return True


# ============================================================
# PHASE 1: Data Model
# (No HTTP-testable behavior — tested by unit tests)
# ============================================================

def phase_1(base_url, session):
    group("Phase 1 — Data Model")
    check(
        "Data model validation",
        None,
        skip_reason="Phase 1 is internal (DB schema) — validated by unit tests",
    )
    return True


# ============================================================
# PHASE 2: Auth System
# ============================================================

def phase_2(base_url, session):
    group("Phase 2 — Auth: Unauthenticated")

    # Login page is publicly accessible
    r = get("/auth/login", base_url=base_url, allow_redirects=True)
    check("Login page returns 200", r.status_code == 200)
    check(
        "Login page contains password field",
        'type="password"' in r.text or "password" in r.text.lower(),
    )

    # Unauthenticated access redirects to login
    for path in ["/dashboard", "/admin/providers", "/admin/users"]:
        r = get(path, base_url=base_url, allow_redirects=False)
        check(
            f"Unauthenticated {path} redirects to login",
            r.status_code in (302, 303, 307)
            and "login" in r.headers.get("location", "").lower(),
        )

    # Invalid session redirects to login
    r = get("/dashboard", session="invalid_token_abc123",
            base_url=base_url, allow_redirects=False)
    check(
        "Invalid session redirects to login",
        r.status_code in (302, 303, 307)
        and "login" in r.headers.get("location", "").lower(),
    )

    # Garbage session doesn't cause 500
    r = get("/dashboard", session="garbage!!@@##$$",
            base_url=base_url, allow_redirects=False)
    check(
        "Garbage session does not cause server error",
        r.status_code != 500,
        detail=f"Got status {r.status_code}",
    )

    # Failed login returns error, not 500
    r = post("/auth/login",
             data={"username": "noone", "password": "wrongpass"},
             base_url=base_url)
    check(
        "Failed login does not return 500",
        r.status_code != 500,
    )

    # Failed login error message is generic (no username/password leak)
    if r.status_code == 200:
        text = r.text.lower()
        check(
            "Failed login error is generic (no info leakage)",
            "user not found" not in text
            and "incorrect password" not in text
            and "no such user" not in text,
            detail="Error message should not reveal which field was wrong",
        )

    group("Phase 2 — Auth: Authenticated")

    if not session:
        skip_msg = "No valid session — auto-login failed"
        check("Dashboard loads for authenticated user", None, skip_msg)
        check("Logout works", None, skip_msg)
        return True

    # Dashboard accessible with valid session
    r = get("/dashboard", session=session, base_url=base_url,
            allow_redirects=True)
    check(
        "Dashboard returns 200 for authenticated user",
        r.status_code == 200,
        detail=f"Got status {r.status_code}",
    )

    # Logout works
    r = post("/auth/logout", session=session, base_url=base_url)
    check(
        "Logout returns redirect",
        r.status_code in (302, 303, 307),
    )

    return True


# ============================================================
# PHASE 2b: Plex OAuth
# ============================================================

def phase_2b(base_url, session):
    group("Phase 2b — Plex OAuth")

    r = get("/auth/login", base_url=base_url, allow_redirects=True)
    # Plex sign-in button should appear if PLEX_CLIENT_ID is configured
    has_plex = "plex" in r.text.lower()
    if has_plex:
        check("Login page contains Plex sign-in option", True)

        # Plex OAuth initiation endpoint exists
        r = get("/auth/plex", base_url=base_url, allow_redirects=False)
        check(
            "Plex OAuth endpoint responds",
            r.status_code in (302, 303, 307, 200),
            detail=f"Got status {r.status_code}",
        )
    else:
        check(
            "Plex sign-in option",
            None,
            skip_reason="PLEX_CLIENT_ID not configured — Plex OAuth not available",
        )

    return True


# ============================================================
# PHASE 3: Provider Framework
# (Mostly internal — but we can check the health endpoint)
# ============================================================

def phase_3(base_url, session):
    group("Phase 3 — Provider Framework")
    check(
        "Provider framework validation",
        None,
        skip_reason="Phase 3 is internal (framework) — validated by unit tests",
    )
    return True


# ============================================================
# PHASE 4: Dashboard & SSE
# ============================================================

def phase_4(base_url, session):
    group("Phase 4 — Dashboard")

    if not session:
        skip_msg = "No valid session"
        check("Dashboard grid renders", None, skip_msg)
        check("Dashboard SSE endpoint responds", None, skip_msg)
        check("Dashboard batch endpoint responds", None, skip_msg)
        check("HTMX partial returns only content", None, skip_msg)
        return True

    # Dashboard page renders with grid structure
    r = get("/dashboard", session=session, base_url=base_url,
            allow_redirects=True)
    check("Dashboard returns 200", r.status_code == 200)
    check(
        "Dashboard contains grid container",
        "dashboard-grid" in r.text or "dashboard" in r.text.lower(),
    )

    # Full page includes base shell
    check(
        "Full page includes HTML shell",
        "<html" in r.text and "</html>" in r.text,
    )

    # HTMX partial request returns content without full shell
    r = htmx_get("/dashboard", session=session, base_url=base_url)
    if r.status_code == 200:
        check(
            "HTMX partial does not include full HTML shell",
            "<!DOCTYPE" not in r.text,
            detail="HTMX responses should return partials, not full pages",
        )
    else:
        check("HTMX partial request returns 200", False,
              detail=f"Got {r.status_code}")

    # SSE endpoint responds with event stream
    try:
        r = requests.get(
            base_url + "/dashboard/stream",
            cookies={"session_id": session},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=5,
        )
        content_type = r.headers.get("content-type", "")
        check(
            "SSE endpoint returns event-stream content type",
            "text/event-stream" in content_type,
            detail=f"Got content-type: {content_type}",
        )
        r.close()
    except requests.exceptions.Timeout:
        # Timeout on streaming is OK — means it's streaming
        check("SSE endpoint returns event-stream content type", True)
    except Exception as e:
        check("SSE endpoint responds", False, detail=str(e))

    # Batch polling endpoint
    r = get("/dashboard/cards", session=session, base_url=base_url,
            allow_redirects=True)
    check(
        "Batch cards endpoint returns 200",
        r.status_code == 200,
        detail=f"Got {r.status_code}",
    )

    # Delivery mode toggle
    r = post("/preferences/delivery-mode",
             data={"mode": "batch"},
             session=session, base_url=base_url)
    check(
        "Delivery mode toggle responds",
        r.status_code in (200, 302, 303),
        detail=f"Got {r.status_code}",
    )

    return True


# ============================================================
# PHASE 5-8: Provider Tests (Template)
# Each provider tests: card renders, detail loads, actions work
# ============================================================

def _test_provider_instance(base_url, session, instance_id,
                            provider_type, provider_name):
    """Generic provider instance test pattern."""
    prefix = f"Provider:{provider_name}"

    # Detail page loads
    r = get(f"/providers/{instance_id}/detail", session=session,
            base_url=base_url, allow_redirects=True)
    check(
        f"{prefix} — detail page returns 200",
        r.status_code == 200,
        detail=f"Got {r.status_code}",
    )

    if r.status_code == 200:
        # Health indicator present
        check(
            f"{prefix} — detail contains health indicator",
            "health" in r.text.lower()
            or "health-dot" in r.text
            or "health--" in r.text,
        )

    # HTMX detail tab loads
    r = htmx_get(f"/providers/{instance_id}/detail/overview",
                 session=session, base_url=base_url)
    check(
        f"{prefix} — overview tab returns 200",
        r.status_code == 200,
        detail=f"Got {r.status_code}",
    )

    # Force refresh
    r = post(f"/providers/{instance_id}/refresh",
             session=session, base_url=base_url)
    check(
        f"{prefix} — refresh returns success",
        r.status_code in (200, 202),
        detail=f"Got {r.status_code}",
    )


def _discover_instances(base_url, session):
    """Discover configured provider instances from the dashboard.
    Returns list of (instance_id, provider_type, display_name) tuples."""
    instances = []
    r = get("/dashboard", session=session, base_url=base_url,
            allow_redirects=True)
    if r.status_code != 200:
        return instances

    # Look for card elements with instance IDs
    # Pattern: id="card-{instance_id}" or data-instance="{instance_id}"
    card_pattern = re.compile(
        r'id="card-([^"]+)"[^>]*'
        r'(?:data-provider-type="([^"]*)")?'
    )
    for match in card_pattern.finditer(r.text):
        instance_id = match.group(1)
        provider_type = match.group(2) or "unknown"
        instances.append((instance_id, provider_type, instance_id))

    return instances


def phase_5_8_providers(base_url, session):
    group("Phase 5-8 — Providers")

    if not session:
        check("Provider tests", None,
              skip_reason="No valid session")
        return True

    # Discover what's configured
    instances = _discover_instances(base_url, session)

    if not instances:
        check(
            "At least one provider instance configured",
            False,
            detail="No provider cards found on dashboard. "
                   "Configure at least one provider to test.",
        )
        return True

    check(
        f"Found {len(instances)} provider instance(s) on dashboard",
        True,
    )

    # Test each discovered instance
    for instance_id, provider_type, display_name in instances:
        _test_provider_instance(
            base_url, session, instance_id, provider_type, display_name
        )

    # Dashboard cards are present for all instances
    r = get("/dashboard", session=session, base_url=base_url,
            allow_redirects=True)
    for instance_id, _, _ in instances:
        check(
            f"Dashboard card present for {instance_id}",
            f"card-{instance_id}" in r.text,
        )

    return True


# ============================================================
# PHASE 5: qBittorrent Specific
# ============================================================

def phase_5_qbit(base_url, session):
    group("Phase 5 — qBittorrent Specific")

    if not session:
        check("qBittorrent tests", None,
              skip_reason="No valid session")
        return True

    instances = _discover_instances(base_url, session)
    qbit_instances = [
        (iid, pt, dn) for iid, pt, dn in instances
        if "qbit" in pt.lower() or "qbittorrent" in pt.lower()
    ]

    if not qbit_instances:
        check("qBittorrent instance found", None,
              skip_reason="No qBittorrent instance configured")
        return True

    iid = qbit_instances[0][0]

    # Detail page has torrents tab
    r = get(f"/providers/{iid}/detail", session=session,
            base_url=base_url, allow_redirects=True)
    if r.status_code == 200:
        check(
            "qBit detail has torrents tab",
            "torrent" in r.text.lower(),
        )

    # Torrents tab loads
    r = htmx_get(f"/providers/{iid}/detail/torrents",
                 session=session, base_url=base_url)
    check(
        "qBit torrents tab returns 200",
        r.status_code == 200,
        detail=f"Got {r.status_code}",
    )

    return True


# ============================================================
# PHASE 6: Arr Provider Specific
# ============================================================

def phase_6_arr(base_url, session):
    group("Phase 6 — Arr Providers")

    if not session:
        check("Arr provider tests", None,
              skip_reason="No valid session")
        return True

    instances = _discover_instances(base_url, session)

    for provider_key, tab_name in [
        ("sonarr", "missing"),
        ("sonarr", "calendar"),
        ("radarr", "missing"),
        ("radarr", "calendar"),
    ]:
        matching = [
            (iid, pt, dn) for iid, pt, dn in instances
            if provider_key in pt.lower()
        ]
        if not matching:
            check(
                f"{provider_key} {tab_name} tab",
                None,
                skip_reason=f"No {provider_key} instance configured",
            )
            continue

        iid = matching[0][0]
        r = htmx_get(f"/providers/{iid}/detail/{tab_name}",
                     session=session, base_url=base_url)
        check(
            f"{provider_key} {tab_name} tab returns 200",
            r.status_code == 200,
            detail=f"Got {r.status_code}",
        )

    return True


# ============================================================
# PHASE 9: Admin UI
# ============================================================

def phase_9_admin(base_url, session):
    group("Phase 9 — Admin UI")

    if not session:
        check("Admin tests", None,
              skip_reason="No valid session")
        return True

    # Provider management page loads
    r = get("/admin/providers", session=session, base_url=base_url,
            allow_redirects=True)
    check(
        "Admin providers page returns 200",
        r.status_code == 200,
    )

    if r.status_code == 200:
        # Contains list of instances or "add" form
        check(
            "Admin providers page has add capability",
            "add" in r.text.lower() or "new" in r.text.lower(),
        )

        # API keys are masked
        check(
            "Admin page masks sensitive fields",
            'type="password"' in r.text
            or "***" in r.text
            or "masked" in r.text.lower(),
            detail="Sensitive fields (API keys) should be masked",
        )

    # User management page loads
    r = get("/admin/users", session=session, base_url=base_url,
            allow_redirects=True)
    check(
        "Admin users page returns 200",
        r.status_code == 200,
    )

    # Role management page loads
    r = get("/admin/roles", session=session, base_url=base_url,
            allow_redirects=True)
    check(
        "Admin roles page returns 200",
        r.status_code == 200,
    )

    if r.status_code == 200:
        # Contains the three system roles
        text = r.text.lower()
        check(
            "Roles page shows system roles",
            "admin" in text and "viewer" in text,
        )

    # System settings page loads
    r = get("/admin/settings", session=session, base_url=base_url,
            allow_redirects=True)
    check(
        "Admin settings page returns 200",
        r.status_code == 200,
    )

    # Test connection endpoint exists (for any configured provider)
    instances = _discover_instances(base_url, session)
    if instances:
        iid = instances[0][0]
        r = post(f"/admin/providers/{iid}/test",
                 session=session, base_url=base_url)
        check(
            "Test connection endpoint responds",
            r.status_code in (200, 422),
            detail=f"Got {r.status_code}",
        )

    return True


# ============================================================
# PHASE 10: Metrics & Self-Health
# ============================================================

def phase_10_health(base_url, session):
    group("Phase 10 — Self-Health")

    # Health endpoint (no auth required)
    r = get("/health", base_url=base_url)
    check("Health endpoint returns 200 or 503", r.status_code in (200, 503))

    if r.status_code in (200, 503):
        try:
            data = r.json()
            check("Health returns JSON", True)
            check(
                "Health contains status field",
                "status" in data,
            )
            check(
                "Health contains version field",
                "version" in data,
            )
            check(
                "Health contains providers summary",
                "providers" in data,
            )
            check(
                "Health does NOT expose provider URLs",
                "url" not in json.dumps(data).lower()
                or "http" not in json.dumps(data).lower(),
                detail="Health endpoint should not expose provider config",
            )
        except (json.JSONDecodeError, ValueError):
            check("Health returns valid JSON", False,
                  detail="Response was not valid JSON")

    return True


# ============================================================
# SECURITY CHECKS (run anytime)
# ============================================================

def security_checks(base_url, session):
    group("Security")

    # Unknown routes return 404, not 500
    r = get("/nonexistent-route-xyz", base_url=base_url,
            allow_redirects=False)
    check(
        "Unknown routes do not cause 500",
        r.status_code != 500,
        detail=f"Got {r.status_code}",
    )

    # Login page doesn't expose secrets
    r = get("/auth/login", base_url=base_url, allow_redirects=True)
    if r.status_code == 200:
        text = r.text.lower()
        check(
            "Login page does not expose API keys",
            "api_key" not in text and "apikey" not in text,
        )
        check(
            "Login page does not expose SECRET_KEY",
            "secret_key" not in text,
        )

    # Admin route not accessible with fake session
    r = get("/admin/providers", session="fake_session_token",
            base_url=base_url, allow_redirects=False)
    check(
        "Admin not accessible with fake session",
        r.status_code in (302, 303, 307, 401, 403)
        and r.status_code != 200,
    )

    # Session cookie attributes (check on login response)
    r = requests.post(
        base_url + "/auth/login",
        data={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASS},
        allow_redirects=False,
        timeout=10,
    )
    set_cookie = r.headers.get("set-cookie", "")
    if "session_id" in set_cookie:
        check(
            "Session cookie has HttpOnly flag",
            "httponly" in set_cookie.lower(),
        )
        check(
            "Session cookie has SameSite attribute",
            "samesite" in set_cookie.lower(),
        )
    else:
        check("Session cookie set on login", False,
              detail="No session_id cookie in Set-Cookie header")

    if session:
        # Docker provider should not expose env vars
        instances = _discover_instances(base_url, session)
        docker_instances = [
            (iid, pt, dn) for iid, pt, dn in instances
            if "docker" in pt.lower()
        ]
        if docker_instances:
            iid = docker_instances[0][0]
            r = get(f"/providers/{iid}/detail", session=session,
                    base_url=base_url, allow_redirects=True)
            if r.status_code == 200:
                # Check that typical env var patterns don't appear
                check(
                    "Docker detail does not expose environment variables",
                    "API_KEY=" not in r.text
                    and "PASSWORD=" not in r.text
                    and "SECRET=" not in r.text,
                    detail="Container environment variables should be stripped",
                )

    return True


# ============================================================
# HTMX PATTERNS (run anytime after Phase 4)
# ============================================================

def htmx_pattern_checks(base_url, session):
    group("HTMX Patterns")

    if not session:
        check("HTMX pattern tests", None,
              skip_reason="No valid session")
        return True

    # Full page load includes HTMX script
    r = get("/dashboard", session=session, base_url=base_url,
            allow_redirects=True)
    if r.status_code == 200:
        check(
            "Page includes HTMX script tag",
            "htmx" in r.text.lower(),
        )
        check(
            "Page includes SSE extension",
            "sse" in r.text.lower(),
        )

    # HTMX request gets HX-Redirect on auth failure (not HTML redirect)
    r = requests.get(
        base_url + "/dashboard",
        headers={"HX-Request": "true"},
        cookies={"session_id": "invalid"},
        allow_redirects=False,
        timeout=10,
    )
    # Should get HX-Redirect header or a 401/redirect
    hx_redirect = r.headers.get("HX-Redirect", "")
    check(
        "HTMX auth failure returns HX-Redirect or appropriate status",
        "login" in hx_redirect.lower()
        or r.status_code in (401, 302, 303),
        detail=f"Got status {r.status_code}, HX-Redirect: '{hx_redirect}'",
    )

    # Toast pattern: action response returns toast HTML
    instances = _discover_instances(base_url, session)
    if instances:
        iid = instances[0][0]
        r = post(
            f"/providers/{iid}/refresh",
            session=session,
            base_url=base_url,
        )
        if r.status_code == 200:
            check(
                "Action response contains toast markup",
                "toast" in r.text.lower(),
            )

    return True


# ============================================================
# SETUP WIZARD (only when no users exist)
# ============================================================

def setup_wizard_check(base_url):
    """Check if setup wizard is active (no users in DB)."""
    group("Setup Wizard")

    try:
        r = requests.get(base_url + "/setup", allow_redirects=False,
                         timeout=5)
        if r.status_code == 200:
            check("Setup wizard is accessible", True)
            check(
                "Setup wizard has admin creation form",
                "password" in r.text.lower(),
            )
            return True  # Setup mode — no other tests can run
        elif r.status_code == 404:
            check(
                "Setup wizard returns 404 (users exist)",
                True,
            )
            return False  # Normal mode
        else:
            check(
                "Setup wizard returns expected status",
                False,
                detail=f"Got {r.status_code} (expected 200 or 404)",
            )
            return False
    except Exception as e:
        check("Setup wizard check", False, detail=str(e))
        return False


# ============================================================
# Main Runner
# ============================================================

def run_all(base_url, session, target_phase=None):
    """Run scenario groups, optionally limited to a specific phase."""

    phases = {
        0: ("Phase 0: Skeleton", phase_0),
        1: ("Phase 1: Data Model", phase_1),
        2: ("Phase 2: Auth", phase_2),
        "2b": ("Phase 2b: Plex OAuth", phase_2b),
        3: ("Phase 3: Provider Framework", phase_3),
        4: ("Phase 4: Dashboard & SSE", phase_4),
        5: ("Phase 5-8: Providers", phase_5_8_providers),
        "5q": ("Phase 5: qBittorrent", phase_5_qbit),
        6: ("Phase 6: Arr Providers", phase_6_arr),
        9: ("Phase 9: Admin UI", phase_9_admin),
        10: ("Phase 10: Health", phase_10_health),
        "sec": ("Security Checks", security_checks),
        "htmx": ("HTMX Patterns", htmx_pattern_checks),
    }

    if target_phase is not None:
        # Convert string phase like "2b" or int phase
        key = target_phase
        try:
            key = int(target_phase)
        except (ValueError, TypeError):
            pass

        if key in phases:
            name, fn = phases[key]
            print(f"\nRunning: {name}")
            fn(base_url, session)
        else:
            print(f"Unknown phase: {target_phase}")
            print(f"Available: {list(phases.keys())}")
            sys.exit(1)
        return

    # Run everything in order
    if not phase_0(base_url, session):
        return  # App not reachable

    # Check if we're in setup mode
    in_setup = setup_wizard_check(base_url)
    if in_setup:
        print("\n⚠ App is in setup mode (no users). Complete setup first.")
        return

    phase_1(base_url, session)
    phase_2(base_url, session)
    phase_2b(base_url, session)
    phase_3(base_url, session)
    phase_4(base_url, session)
    phase_5_8_providers(base_url, session)
    phase_5_qbit(base_url, session)
    phase_6_arr(base_url, session)
    phase_9_admin(base_url, session)
    phase_10_health(base_url, session)
    security_checks(base_url, session)
    htmx_pattern_checks(base_url, session)


def print_results():
    """Print formatted results."""
    total = len(PASS) + len(FAIL)

    print(f"\n{'=' * 60}")
    print(f"GREAT EYE SCENARIO RESULTS")
    print(f"{'=' * 60}")

    if PASS:
        print(f"\n✓ PASSED: {len(PASS)}/{total}")
        for p in PASS:
            print(f"    ✓ {p}")

    if FAIL:
        print(f"\n✗ FAILED: {len(FAIL)}/{total}")
        for name, detail in FAIL:
            print(f"    ✗ {name}")
            if detail:
                print(f"      → {detail}")

    if SKIP:
        print(f"\n○ SKIPPED: {len(SKIP)}")
        for name, reason in SKIP:
            print(f"    ○ {name}")
            print(f"      → {reason}")

    print(f"\n{'=' * 60}")

    if total > 0:
        pct = (len(PASS) / total) * 100
        print(f"Score: {len(PASS)}/{total} ({pct:.0f}%)")
    else:
        print("No scenarios executed.")

    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Great Eye external scenario tests"
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"Base URL of running app (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--session", default=None,
        help="Pre-existing session cookie (skips auto-login)",
    )
    parser.add_argument(
        "--phase", default=None,
        help="Run only a specific phase (0, 1, 2, 2b, 3, 4, 5, 6, 9, 10, sec, htmx)",
    )
    parser.add_argument(
        "--user", default=TEST_ADMIN_USER,
        help=f"Admin username for auto-login (default: {TEST_ADMIN_USER})",
    )
    parser.add_argument(
        "--password", default=TEST_ADMIN_PASS,
        help="Admin password for auto-login",
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    print(f"Target: {base_url}")

    # Reachability check
    try:
        requests.get(base_url, timeout=5, allow_redirects=True)
    except Exception:
        print(f"FATAL: Cannot reach {base_url}")
        print("Start the app first, then re-run scenarios.")
        sys.exit(1)

    # Session: use provided, or try auto-login
    session = args.session
    if not session:
        print(f"Attempting auto-login as '{args.user}'...")
        session = login(base_url, args.user, args.password)
        if session:
            print(f"  ✓ Logged in (session: {session[:12]}...)")
        else:
            print(f"  ✗ Auto-login failed — authenticated tests will be skipped")
            print(f"    Use --session <cookie> to provide a session manually")

    run_all(base_url, session, args.phase)
    print_results()

    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
