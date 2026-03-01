# Authentication & Authorization

## Overview
Great Eye supports two authentication methods: local username/password
and Plex OAuth. Users can use either or both (linked accounts). All
authorization is role-based with granular provider-level permissions.

## Authentication Methods

### Local Auth
Standard username + password authentication.

- Passwords hashed with bcrypt (cost factor 12)
- Minimum password length: 8 characters
- No complexity requirements (length is king)
- Passwords checked server-side, timing-safe comparison
- Failed login: generic "Invalid credentials" (don't reveal which field)

### Plex OAuth
Delegates authentication to Plex's OAuth flow. Only pre-approved
Plex users can log in.

**Flow:**
1. User clicks "Sign in with Plex" on login page
2. Great Eye opens Plex OAuth URL in new window/popup
3. User authenticates with Plex (plex.tv)
4. Plex redirects back with an auth token
5. Great Eye exchanges token for user info via Plex API
6. Great Eye checks username against `plex_approved_users` table
7. If approved: create session. If first login: create user record
   with default role from the approval entry.
8. If not approved: reject with "Account not authorized"

**Required Config:**
- `PLEX_CLIENT_ID` environment variable (registered app identifier)
- Admin must pre-approve Plex usernames in the admin UI

**Plex Token Storage:**
The Plex auth token is stored (Fernet-encrypted) for potential future
use (e.g., correlating Plex user with Plex sessions). Not required for
auth after initial login — session cookie handles subsequent requests.

### Dual Auth (Linked Accounts)
A user can have both auth methods linked:

- Local user clicks "Link Plex Account" → Plex OAuth flow → links
  plex_user_id to their existing local account
- Plex user sets a password → auth_method changes from "plex" to "both"
- Either method creates a valid session for the same user record

`auth_method` field values: `"local"`, `"plex"`, `"both"`

## Session Management

### Session Creation
On successful login:
1. Generate cryptographically random session ID (32 bytes, hex-encoded)
2. Store in `sessions` table: session_id, user_id, created_at, expires_at
3. Set HTTP-only, Secure (if HTTPS), SameSite=Lax cookie

### Session Cookie
```
Set-Cookie: session_id=<token>; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400
```

- `HttpOnly`: not accessible via JavaScript
- `SameSite=Lax`: CSRF protection for top-level navigations
- `Secure`: only set if running behind HTTPS (detected from request)
- `Max-Age`: 24 hours default, configurable

### Session Validation
Every request (except static files, login page, setup wizard):
1. Read `session_id` cookie
2. Look up in sessions table
3. Check `expires_at > now()`
4. Load user + role + permissions
5. If invalid/expired: redirect to `/login`

### Session Invalidation
Sessions are invalidated:
- On explicit logout (DELETE session from DB)
- On password change (all sessions for that user deleted)
- On admin force-logout (admin can delete specific user sessions)
- On expiry (cleanup job removes expired sessions periodically)

### Session Preferences
The `sessions` table stores per-session preferences:
- `delivery_mode`: "sse" or "batch" (dashboard data delivery)

## Authorization Model

### Roles
Three system roles seeded on first startup:

| Role   | Description                    | Deletable | Modifiable |
|--------|--------------------------------|-----------|------------|
| admin  | Full access to everything      | No        | Yes*       |
| user   | View + actions, no admin       | No        | Yes        |
| viewer | Read-only, no actions          | No        | Yes        |

*Admin permissions can be modified but the role cannot be deleted.

Custom roles can be created by admins.

### Permission Categories
Permissions are registered by providers on startup and fall into
three categories:

| Category | Description                | Default assignment         |
|----------|----------------------------|----------------------------|
| read     | View provider data         | admin, user, viewer        |
| action   | Execute provider actions   | admin, user                |
| admin    | Destructive/config actions | admin                      |

### Permission Resolution
```python
def has_permission(user, permission_key: str) -> bool:
    """Check if user's role grants the given permission."""
    role = user.role
    return permission_key in role.permissions
```

Permissions are loaded once per request (cached on the request context)
when the session is validated. No per-check database queries.

### Permission-Aware Rendering
Templates check permissions before rendering UI elements:

```jinja2
{% if has_permission("sonarr.search") %}
  <button>Search</button>
{% endif %}
```

API endpoints also check permissions server-side (defense in depth):

```python
@app.post("/providers/{instance_id}/actions/{action}")
async def execute_action(instance_id, action, user=Depends(get_current_user)):
    required_perm = registry.get_action_permission(instance_id, action)
    if not has_permission(user, required_perm):
        raise HTTPException(403, "Insufficient permissions")
    ...
```

## Login Page

### Layout
Centered card on dark background with Great Eye logo.

**Elements:**
- Username + password fields (local auth)
- "Sign in" button
- "Sign in with Plex" button (if PLEX_CLIENT_ID configured)
- "Forgot password?" link → shows reset request form
- Error messages displayed inline (not page redirect)

### HTMX Login
```html
<form hx-post="/auth/login"
      hx-target="#login-error"
      hx-swap="innerHTML"
      hx-on::after-request="if(event.detail.successful) window.location='/'">
  <input type="text" name="username" required>
  <input type="password" name="password" required>
  <button type="submit">Sign In</button>
  <div id="login-error"></div>
</form>
```

Success: redirect to dashboard.
Failure: server returns error message HTML into `#login-error`.

## Password Reset Flow

### User-Initiated
1. User clicks "Forgot password?" on login page
2. User enters their username
3. Server generates a reset token (random 32 bytes, hex)
4. Token hash (SHA-256) stored in `password_reset_tokens` table
5. Plaintext token is NOT emailed (v1) — surfaced in admin UI
6. Admin sees pending reset in admin panel, shares link out-of-band
7. User visits `/auth/reset/{token}`
8. Server verifies token hash, checks expiry (1 hour)
9. User sets new password
10. Token marked as used, all existing sessions invalidated

### Admin-Initiated
1. Admin toggles "Force Password Reset" on a user
2. User's `force_reset` flag set to true
3. On next login, user is redirected to password change form
4. User cannot access any other page until password is changed
5. After change, `force_reset` cleared, sessions refreshed

## Middleware Stack

### Request Processing Order
1. **Static files** — served directly, no auth
2. **Setup check** — if no users exist, redirect to `/setup`
3. **Session validation** — read cookie, validate, load user
4. **Force reset check** — if user.force_reset, redirect to `/auth/change-password`
5. **Route handler** — normal request processing

### Exempt Routes (no auth required)
- `GET /auth/login` — login page
- `POST /auth/login` — login submission
- `POST /auth/plex/callback` — Plex OAuth callback
- `GET /auth/reset/{token}` — password reset page
- `POST /auth/reset/{token}` — password reset submission
- `GET /setup` — first-time setup (only when no users)
- `POST /setup/*` — setup wizard steps
- `GET /static/*` — static files

## Security Considerations

### Rate Limiting
- Login attempts: 5 per minute per IP
- Password reset requests: 3 per hour per username
- API actions: 30 per minute per user
- Implemented via in-memory counter (not database, resets on restart)

### Timing Attacks
- Password comparison uses bcrypt (constant-time by design)
- Session lookup: use constant-time comparison for session ID
- Invalid username returns same response as invalid password

### Cookie Security
- Session cookies are HTTP-only (no JS access)
- SameSite=Lax prevents CSRF for state-changing requests
- Secure flag set when detected behind HTTPS
- No sensitive data in cookies beyond the session ID

### Secret Management
- `SECRET_KEY` env var: used to derive Fernet encryption key
- Never logged, never in HTML, never in error messages
- If SECRET_KEY changes: all encrypted data (API keys, Plex tokens)
  becomes unrecoverable. Admin must re-enter all provider configs.
- bcrypt passwords are NOT affected by SECRET_KEY changes
