# Per-User Layout Customization

## Overview
Each user can customize their sidebar grouping, dashboard card order, and
hidden providers. Layout preferences persist across sessions (stored on the
User model, not the Session).

## Data Model
A single `layout_json` TEXT column on the `users` table stores a JSON blob:

```json
{
  "sidebar_groups": [
    { "id": "uuid", "name": "Media", "collapsed": false, "instance_ids": [3, 1, 7] },
    { "id": "uuid", "name": "Infra", "collapsed": true, "instance_ids": [5, 9] }
  ],
  "card_order": [3, 1, 7, 5, 9, 2],
  "hidden_instance_ids": [4, 6]
}
```

- **sidebar_groups**: Ordered list of named groups with their provider instance IDs
- **card_order**: Dashboard card display order (instance IDs)
- **hidden_instance_ids**: Instances the user has chosen to hide

A NULL or empty value means "no customization" — flat sidebar, default card order.

## Merge Logic
On every render, the layout is merged with the current set of available
(permitted) provider instances:

1. **Prune**: Remove instance IDs that no longer exist from groups, card_order, hidden
2. **Append**: Newly added instances appear in "Ungrouped" and at the end of card_order
3. **Hidden preserved**: Hidden IDs remain hidden unless the instance is deleted

This ensures admin adding/removing providers doesn't break user layouts.

## Layout Service
`src/services/layout.py` provides:

| Function | Purpose |
|----------|---------|
| `parse_layout(raw)` | Parse JSON string to `UserLayout` dataclass |
| `serialize_layout(layout)` | Serialize to JSON string |
| `merge_with_available(layout, ids)` | Reconcile with current instances |
| `get_ordered_instances(layout, instances)` | Apply card order + hide filter |
| `get_grouped_sidebar(layout, sidebar_instances)` | Build grouped sidebar data |
| `new_group_id()` | Generate a UUID for a new group |

## API Endpoints
All under `/preferences/layout/*`, require authentication.

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| GET | `/preferences/layout` | — | Return current layout |
| PUT | `/preferences/layout/sidebar` | `SidebarUpdate` | Full sidebar update |
| PUT | `/preferences/layout/card-order` | `CardOrderUpdate` | Update card order |
| POST | `/preferences/layout/sidebar/groups` | `GroupCreate` | Create group |
| PUT | `/preferences/layout/sidebar/groups/{id}` | `GroupRename` | Rename group |
| DELETE | `/preferences/layout/sidebar/groups/{id}` | — | Delete group |
| PUT | `/preferences/layout/sidebar/collapse/{id}` | — | Toggle collapse |
| PUT | `/preferences/layout/hidden` | `HiddenUpdate` | Update hidden list |

## Frontend
- **SortableJS** (vendored at `static/js/sortable.min.js`) handles drag-and-drop
- **layout.js** initializes SortableJS on sidebar groups and dashboard grid
- Sidebar items can be dragged between groups (cross-group `group: "sidebar-providers"`)
- Dashboard cards draggable by their header (`.card__header` handle)
- Changes auto-saved via fetch() to the API endpoints (no submit button needed)
- Group management: inline "New Group" button, prompt for rename, confirm for delete
- `htmx:afterSettle` listener re-initializes SortableJS after batch polling replaces cards

## CSS
- Sidebar groups use collapsible sections with chevron indicator
- Drag feedback: `.sortable-ghost` (opacity), `.sortable-chosen` (accent ring), `.sortable-drag` (shadow)
- Card hover: `translateY(-2px)` lift with status-colored glow shadow
- Glassmorphism: `backdrop-filter: blur(12px)` on sidebar and header
- Mobile (<768px): drag-and-drop still works but groups collapse/expand normally

## Sidebar Template Structure
```html
<div class="sidebar-group" data-group-id="uuid">
  <button class="sidebar-group__header">
    <span class="sidebar-group__chevron">▾</span>
    <span class="sidebar-group__name">Media</span>
    <span class="sidebar-group__count">3</span>
  </button>
  <div class="sidebar-group__items" data-group-id="uuid">
    <!-- draggable provider links -->
  </div>
</div>
```

## Route Integration
The `add_sidebar_context()` helper in `src/routes/_helpers.py` is called by
dashboard, providers, and admin routes for full-page renders. It populates:
- `sidebar_groups` — list of group dicts with items
- `ungrouped_instances` — instances not in any group
- `sidebar_instances` — flat list (fallback)

## Tests
- `tests/test_services/test_layout.py` — 25 unit tests for layout service
- `tests/test_routes/test_preferences_layout.py` — 14 API integration tests
