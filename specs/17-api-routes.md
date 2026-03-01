# API Routes

## Overview
All routes serve HTML (not JSON). The browser never receives raw data —
the server renders templates for every response. HTMX requests receive
HTML partials; full page loads receive the complete shell.

## Route Detection Pattern
Every route checks the `HX-Request` header to determine response type:

```python
def render(request, template, context, partial_template=None):
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(partial_template or template, context)
    return templates.TemplateResponse("base.html", {**context, "content_template": template})
```

## Auth Routes

| Method | Path                         | Auth | Description                          |
|--------|------------------------------|------|--------------------------------------|
| GET    | /auth/login                  | No   | Login page                           |
| POST   | /auth/login                  | No   | Authenticate (local)                 |
| GET    | /auth/plex                   | No   | Initiate Plex OAuth                  |
| GET    | /auth/plex/callback          | No   | Plex OAuth callback                  |
| POST   | /auth/logout                 | Yes  | Destroy session                      |
| GET    | /auth/reset-request          | No   | Password reset request form          |
| POST   | /auth/reset-request          | No   | Submit reset request                 |
| GET    | /auth/reset/{token}          | No   | Password reset form                  |
| POST   | /auth/reset/{token}          | No   | Submit new password                  |
| GET    | /auth/change-password        | Yes  | Change password form (force reset)   |
| POST   | /auth/change-password        | Yes  | Submit password change               |

## Setup Routes

| Method | Path                         | Auth | Description                          |
|--------|------------------------------|------|--------------------------------------|
| GET    | /setup                       | No*  | Setup wizard (only if no users)      |
| POST   | /setup/admin                 | No*  | Create admin account                 |
| POST   | /setup/providers             | No*  | Configure initial providers          |
| POST   | /setup/complete              | No*  | Finalize setup                       |

*Only accessible when `users` table is empty. Returns 404 otherwise.

## Dashboard Routes

| Method | Path                         | Auth | Permission | Description                  |
|--------|------------------------------|------|------------|------------------------------|
| GET    | /                            | Yes  | —          | Redirect to /dashboard       |
| GET    | /dashboard                   | Yes  | —          | Dashboard page (full render) |
| GET    | /dashboard/cards             | Yes  | —          | All cards (batch polling)    |
| GET    | /dashboard/stream            | Yes  | —          | SSE event stream             |

### SSE Stream Behavior
`GET /dashboard/stream` returns `text/event-stream`:
- On connect: send current state for all instances the user can see
- On cache update: send named events for changed instances
- Filter: only send instances the user has `{type}.view` permission for
- On disconnect: clean up the per-connection asyncio.Queue

### Batch Polling
`GET /dashboard/cards` returns all summary cards as a single HTML block.
Used when delivery_mode is "batch" (polled every 60s by HTMX).

## Provider Detail Routes

| Method | Path                                          | Auth | Permission     | Description            |
|--------|-----------------------------------------------|------|----------------|------------------------|
| GET    | /providers/{id}/detail                        | Yes  | {type}.view    | Detail page (default tab)|
| GET    | /providers/{id}/detail/{tab}                  | Yes  | {type}.view    | Detail tab content     |

Tab names are provider-specific (overview, queue, missing, calendar,
torrents, indexers, history, etc.). The provider registry maps tab
names to template paths.

## Provider Action Routes

| Method | Path                                          | Auth | Permission     | Description            |
|--------|-----------------------------------------------|------|----------------|------------------------|
| POST   | /providers/{id}/actions/{action}              | Yes  | per-action     | Execute provider action|
| POST   | /providers/{id}/refresh                       | Yes  | {type}.view    | Force cache refresh    |

### Action Request
```
POST /providers/3/actions/pause
Content-Type: application/x-www-form-urlencoded
HX-Request: true

hashes=abc123&hashes=def456
```

### Action Response
Returns a toast notification partial:
```html
<div class="toast toast--success">Torrent paused</div>
```

Or on error:
```html
<div class="toast toast--error">Failed: Connection refused</div>
```

## Preference Routes

| Method | Path                         | Auth | Permission | Description                  |
|--------|------------------------------|------|------------|------------------------------|
| POST   | /preferences/delivery-mode   | Yes  | —          | Toggle SSE/batch mode        |

Updates the session record and returns a page reload trigger:
```html
<script>window.location.reload();</script>
```

## Admin Routes

### Provider Management

| Method | Path                                | Auth | Permission | Description                  |
|--------|-------------------------------------|------|------------|------------------------------|
| GET    | /admin/providers                    | Yes  | admin      | Provider instance list       |
| GET    | /admin/providers/new                | Yes  | admin      | New instance form            |
| POST   | /admin/providers                    | Yes  | admin      | Create instance              |
| GET    | /admin/providers/{id}/edit          | Yes  | admin      | Edit instance form           |
| PUT    | /admin/providers/{id}               | Yes  | admin      | Update instance              |
| DELETE | /admin/providers/{id}               | Yes  | admin      | Delete instance              |
| POST   | /admin/providers/{id}/test          | Yes  | admin      | Test connection              |
| POST   | /admin/providers/{id}/toggle        | Yes  | admin      | Enable/disable instance      |
| POST   | /admin/providers/sort               | Yes  | admin      | Update dashboard sort order  |

### Test Connection Response
Returns inline result:
```html
<div class="test-result test-result--success">
  ✓ Connected to Sonarr v4.0.5
</div>
```

or:
```html
<div class="test-result test-result--error">
  ✗ Invalid API key
</div>
```

### User Management

| Method | Path                                | Auth | Permission | Description                  |
|--------|-------------------------------------|------|------------|------------------------------|
| GET    | /admin/users                        | Yes  | admin      | User list                    |
| GET    | /admin/users/new                    | Yes  | admin      | New user form                |
| POST   | /admin/users                        | Yes  | admin      | Create user                  |
| GET    | /admin/users/{id}/edit              | Yes  | admin      | Edit user form               |
| PUT    | /admin/users/{id}                   | Yes  | admin      | Update user                  |
| DELETE | /admin/users/{id}                   | Yes  | admin      | Delete user                  |
| POST   | /admin/users/{id}/force-reset       | Yes  | admin      | Toggle force password reset  |

### Plex User Approval

| Method | Path                                | Auth | Permission | Description                  |
|--------|-------------------------------------|------|------------|------------------------------|
| GET    | /admin/plex-users                   | Yes  | admin      | Approved Plex users list     |
| POST   | /admin/plex-users                   | Yes  | admin      | Add approved Plex user       |
| DELETE | /admin/plex-users/{id}              | Yes  | admin      | Remove approved Plex user    |

### Role Management

| Method | Path                                | Auth | Permission | Description                  |
|--------|-------------------------------------|------|------------|------------------------------|
| GET    | /admin/roles                        | Yes  | admin      | Role list with permissions   |
| GET    | /admin/roles/{id}/edit              | Yes  | admin      | Edit role permissions        |
| PUT    | /admin/roles/{id}                   | Yes  | admin      | Update role permissions      |
| POST   | /admin/roles                        | Yes  | admin      | Create custom role           |
| DELETE | /admin/roles/{id}                   | Yes  | admin      | Delete custom role           |

### System Settings

| Method | Path                                | Auth | Permission | Description                  |
|--------|-------------------------------------|------|------------|------------------------------|
| GET    | /admin/settings                     | Yes  | admin      | System settings page         |
| PUT    | /admin/settings                     | Yes  | admin      | Update settings              |

### Password Reset Queue

| Method | Path                                | Auth | Permission | Description                  |
|--------|-------------------------------------|------|------------|------------------------------|
| GET    | /admin/resets                       | Yes  | admin      | Pending password resets      |
| DELETE | /admin/resets/{id}                  | Yes  | admin      | Cancel a pending reset       |

## Error Handling

### HTTP Status Codes
| Code | Usage                                        |
|------|----------------------------------------------|
| 200  | Success                                      |
| 302  | Redirect (login, setup, after actions)       |
| 400  | Bad request (invalid form data)              |
| 401  | Unauthorized (session expired/invalid)       |
| 403  | Forbidden (insufficient permissions)         |
| 404  | Not found (invalid instance, route)          |
| 500  | Server error (unhandled exception)           |

### HTMX Error Handling
For HTMX requests, errors return HTML that can be swapped into the page:
- 401: returns login redirect header (`HX-Redirect: /auth/login`)
- 403: returns permission denied toast
- 404: returns "not found" message in target
- 500: returns generic error toast

```python
@app.exception_handler(403)
async def forbidden_handler(request, exc):
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            '<div class="toast toast--error">Permission denied</div>',
            status_code=403,
            headers={"HX-Retarget": "#toast-container", "HX-Reswap": "beforeend"}
        )
    return templates.TemplateResponse("errors/403.html", {"request": request}, status_code=403)
```
