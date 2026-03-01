# Test Scenarios & Edge Cases

## Overview
This document catalogs the key scenarios, edge cases, and failure modes
that Great Eye must handle. These serve as acceptance criteria for
implementation and as the basis for integration tests.

## Provider Lifecycle Scenarios

### S1: Fresh Install — No Providers Configured
- Dashboard shows empty state with "Add your first provider" CTA
- Health indicators show nothing (not a wall of red dots)
- Admin sidebar link is prominent

### S2: Provider Goes Down During Normal Operation
- Health check transitions from UP → DOWN
- Summary card shows last known data with stale banner
- Health dot goes red
- Polling continues at normal interval (to detect recovery)
- No cascade: other providers unaffected

### S3: Provider Recovers After Being Down
- Health check transitions from DOWN → UP
- Stale banner removed
- Summary data refreshed immediately
- Dashboard reflects current state within one poll cycle

### S4: Provider Returns Partial Data
- Some API calls succeed, others fail (e.g., Sonarr /series works
  but /queue times out)
- Summary shows what's available, marks missing sections
- Does NOT show error for the entire card

### S5: Provider Config Changed
- Admin updates URL or API key
- Existing cache invalidated
- Immediate health check with new config
- If new config fails, show DOWN state (not stale old data)

### S6: Provider Disabled by Admin
- Card removed from dashboard
- Polling stops
- Config preserved in database
- Re-enabling resumes polling and re-adds card

## Authentication Scenarios

### S7: First-Time Setup
- No users in database
- All routes redirect to /setup
- Admin creates account + optional Plex link
- After setup, /setup returns 404 permanently
- Subsequent users created via admin UI

### S8: Plex OAuth — Approved User First Login
- User clicks "Sign in with Plex"
- Plex OAuth flow completes
- Username found in plex_approved_users
- User record created with default_role from approval
- Session created, redirect to dashboard

### S9: Plex OAuth — Unapproved User
- Plex OAuth completes
- Username NOT in plex_approved_users
- Login rejected: "Account not authorized. Contact admin."
- No user record created

### S10: Concurrent Sessions
- Same user logged in on two devices
- Both sessions are valid simultaneously
- Password change invalidates ALL sessions for that user
- Both devices redirect to login

### S11: Force Password Reset
- Admin sets force_reset on a user
- User's next request (any page) redirects to change password
- User cannot navigate away until password is changed
- After change, flag cleared, normal access restored

### S12: Session Expiry During SSE Connection
- User has SSE stream open
- Session expires (24h)
- SSE connection closed by server
- HTMX auto-reconnect triggers
- Reconnect fails with 401
- Page redirects to login (via HX-Redirect header)

## Dashboard Delivery Scenarios

### S13: SSE Mode — Normal Operation
- Dashboard loads with full render (all cards from cache)
- SSE connection opens
- Server pushes card updates when cache refreshes
- Only changed cards are re-rendered
- Connection stays open indefinitely

### S14: SSE Mode — Reconnection
- Network interruption drops SSE connection
- HTMX retries after 1 second
- On reconnect, server sends full state dump
- Dashboard is synchronized without page reload

### S15: Batch Mode — Normal Operation
- Dashboard loads with full render
- HTMX polls every 60 seconds
- Entire grid replaced on each poll
- Simpler, no persistent connection

### S16: Mode Switch
- User clicks "Switch to Polling" / "Switch to SSE"
- Preference saved to session
- Page reloads to activate new mode
- Preference persists for session duration

## Action Scenarios

### S17: Action Success
- User clicks action button (e.g., pause torrent)
- Confirmation dialog (if required)
- Server executes action on upstream API
- Toast notification: "Torrent paused"
- Cache invalidated, card refreshes on next poll/SSE push
- Action logged in provider_action_log

### S18: Action Failure — Upstream Error
- User triggers action
- Upstream API returns error (e.g., Sonarr 500)
- Toast notification: "Failed: Sonarr returned error"
- No cache invalidation
- Action logged with failure result

### S19: Action Failure — Permission Denied
- User without permission somehow triggers action (e.g., crafted request)
- Server checks permission before executing
- Returns 403
- Toast: "Permission denied"
- No action logged (never reached the provider)

### S20: Action on Down Provider
- Provider is DOWN
- Action buttons still visible (server doesn't know action will fail
  until it tries, and provider might recover between health checks)
- Action attempt fails with connection error
- Toast: "Cannot reach Sonarr"

### S21: Destructive Action with Files
- User deletes series/movie with delete_files=true
- Confirmation message explicitly mentions file deletion
- Action requires admin permission (not just action category)
- Success: toast notification, cache invalidated

## Provider-Specific Edge Cases

### S22: qBittorrent — Session Cookie Expiry
- qBit session cookie expires during polling
- Provider detects 403, re-authenticates
- Retries failed request with new cookie
- If re-auth fails, mark DOWN

### S23: qBittorrent — v4 vs v5 State Names
- Provider checks qBit version on first health check
- Uses "pausedDL"/"pausedUP" for v4, "stoppedDL"/"stoppedUP" for v5
- Uses /torrents/pause for v4, /torrents/stop for v5
- Version cached for instance lifetime

### S24: Sonarr — Large Library
- 500+ series library
- /api/v3/series returns all series in one call
- Provider computes aggregates server-side
- Cache aggressively — series data changes slowly
- Summary card shows counts, not the full list

### S25: Sonarr — Command Fire-and-Forget
- User triggers episode search
- Sonarr accepts command (HTTP 201)
- Provider returns "Search started" immediately
- Actual search runs asynchronously in Sonarr
- Cache refresh after short delay picks up changes

### S26: Prowlarr — Indexer Status Derivation
- Prowlarr doesn't have a single status field per indexer
- Provider derives status from stats and /health endpoint
- Multiple indexers with mixed states: show aggregate health

### S27: Seerr/Overseerr — App Detection
- Provider checks /api/v1/status response
- Detects "Seerr" vs "Overseerr" from response
- Both work identically — log which was detected
- If Overseerr: validate_config includes migration note

### S28: Pi-hole — Blocking Disabled
- Pi-hole is reachable but blocking is off
- Health: DEGRADED (not DOWN — service is running)
- Card shows prominent "Blocking Disabled" warning
- "Enable Blocking" action button available

### S29: Unbound — Cumulative vs Per-Interval Stats
- `statistics-cumulative: yes`: values always increase
- `statistics-cumulative: no`: values reset between reads
- Provider must handle both: compute deltas for cumulative,
  use raw values for per-interval
- Detection: if query count decreases between polls → per-interval

### S30: Docker — Self-Protection
- Provider identifies own container on startup
- Restart/stop actions filtered: own container never targeted
- If user attempts (crafted request), server rejects

### S31: Docker — Read-Only Socket
- Socket mounted with :ro
- Container list and stats work normally
- Restart/stop actions fail with permission error
- Provider detects this on startup, hides action buttons
- Health shows UP (monitoring works), action availability noted

## Multi-Instance Scenarios

### S32: Two Sonarr Instances (HD + 4K)
- Both instances appear as separate cards
- Each has independent config, health, polling
- Actions target the correct instance
- Dashboard shows both side by side

### S33: Two Pi-hole Instances (Primary + Secondary)
- Both monitored independently
- One could be DOWN while other is UP
- Dashboard shows per-instance health
- User sees full DNS infrastructure status

## Failure & Recovery Scenarios

### S34: Database Corruption
- SQLite database becomes unreadable
- Application fails to start
- Error logged with clear message
- Recovery: restore from backup (/config mount)

### S35: SECRET_KEY Change
- All Fernet-encrypted data becomes unrecoverable
- Provider API keys, Plex tokens cannot be decrypted
- All providers go DOWN with "Decryption failed" error
- Admin must re-enter all provider configs
- User passwords (bcrypt) are NOT affected

### S36: Upstream API Changes
- Sonarr/Radarr/etc. updates with breaking API change
- Provider calls fail with unexpected response format
- Health: DOWN with "Unexpected API response"
- Other providers unaffected
- Fix: update provider code for new API version

### S37: High Concurrent Users
- Multiple users on dashboard simultaneously
- SSE mode: each user has own Queue, event bus fans out
- Cache is shared — one fetch serves all users
- Actions are per-user (logged with user_id)

### S38: Slow Network to Provider
- Provider API responds slowly (>5s)
- Health check times out → DOWN
- Summary fetch times out → stale data shown
- Other providers not blocked (asyncio handles concurrently)
- Provider-specific timeout, not global

## Data Integrity Scenarios

### S39: Metrics Retention Cleanup
- Daily job deletes metrics older than 30 days
- Job runs at a consistent time (e.g., 3 AM)
- Large delete batched to avoid long locks
- SQLite WAL mode prevents read blocking during cleanup

### S40: Cache Race Condition
- Polling updates cache while SSE is reading it
- SQLite WAL mode: readers don't block writers
- Cache reads are point-in-time consistent
- Worst case: SSE sends slightly stale data, next event corrects
