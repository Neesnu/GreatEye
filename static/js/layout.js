/**
 * LayoutManager — sidebar group management + drag-and-drop for sidebar and dashboard cards.
 * Depends on SortableJS being loaded first.
 */
(function () {
    "use strict";

    var LayoutManager = {
        _sidebarSortables: [],
        _dashboardSortable: null,

        // ---------------------------------------------------------------
        // Init
        // ---------------------------------------------------------------
        init: function () {
            this.initSidebar();
            this.initDashboard();

            // Re-init dashboard after HTMX swaps (batch polling replaces cards)
            document.body.addEventListener("htmx:afterSettle", function (e) {
                if (e.detail.target && e.detail.target.id === "dashboard-grid") {
                    LayoutManager.initDashboard();
                }
            });
        },

        // ---------------------------------------------------------------
        // Sidebar drag-and-drop
        // ---------------------------------------------------------------
        initSidebar: function () {
            if (typeof Sortable === "undefined") return;

            // Destroy previous instances
            this._sidebarSortables.forEach(function (s) { s.destroy(); });
            this._sidebarSortables = [];

            // Make items within each group sortable (cross-group drag)
            var itemContainers = document.querySelectorAll(".sidebar-group__items");
            var self = this;
            itemContainers.forEach(function (el) {
                var s = Sortable.create(el, {
                    group: "sidebar-providers",
                    animation: 150,
                    ghostClass: "sortable-ghost",
                    chosenClass: "sortable-chosen",
                    dragClass: "sortable-drag",
                    handle: ".sidebar__link",
                    onEnd: function () { self.saveSidebar(); }
                });
                self._sidebarSortables.push(s);
            });

            // Make groups container sortable (reorder groups by dragging headers)
            var groupsContainer = document.getElementById("sidebar-groups-container");
            if (groupsContainer) {
                var gs = Sortable.create(groupsContainer, {
                    animation: 150,
                    handle: ".sidebar-group__header",
                    ghostClass: "sortable-ghost",
                    dragClass: "sortable-drag",
                    onEnd: function () { self.saveSidebar(); }
                });
                this._sidebarSortables.push(gs);
            }
        },

        saveSidebar: function () {
            var groups = [];
            var groupEls = document.querySelectorAll("#sidebar-groups-container > .sidebar-group");
            groupEls.forEach(function (groupEl) {
                var groupId = groupEl.dataset.groupId;
                if (groupId === "ungrouped") return; // handled separately
                var nameEl = groupEl.querySelector(".sidebar-group__name");
                var name = nameEl ? nameEl.textContent.trim() : "Unnamed";
                var collapsed = groupEl.classList.contains("sidebar-group--collapsed");
                var instanceIds = [];
                groupEl.querySelectorAll(".sidebar-group__items .sidebar__link[data-instance-id]").forEach(function (link) {
                    instanceIds.push(parseInt(link.dataset.instanceId, 10));
                });
                groups.push({ id: groupId, name: name, collapsed: collapsed, instance_ids: instanceIds });
            });

            // Collect ungrouped IDs
            var ungroupedIds = [];
            var ungroupedEl = document.querySelector('[data-group-id="ungrouped"] .sidebar-group__items, [data-group-id="ungrouped"].sidebar-group__items');
            if (!ungroupedEl) {
                ungroupedEl = document.querySelector('.sidebar-group[data-group-id="ungrouped"] .sidebar-group__items');
            }
            if (ungroupedEl) {
                ungroupedEl.querySelectorAll(".sidebar__link[data-instance-id]").forEach(function (link) {
                    ungroupedIds.push(parseInt(link.dataset.instanceId, 10));
                });
            }

            fetch("/preferences/layout/sidebar", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ groups: groups, ungrouped_ids: ungroupedIds })
            });
        },

        // ---------------------------------------------------------------
        // Group actions
        // ---------------------------------------------------------------
        toggleGroup: function (btn) {
            var group = btn.closest(".sidebar-group");
            if (!group) return;
            var collapsed = group.classList.toggle("sidebar-group--collapsed");
            btn.setAttribute("aria-expanded", collapsed ? "false" : "true");

            var groupId = group.dataset.groupId;
            if (groupId && groupId !== "ungrouped") {
                fetch("/preferences/layout/sidebar/collapse/" + groupId, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" }
                });
            }
        },

        showAddGroup: function () {
            var container = document.getElementById("sidebar-add-group");
            if (!container) return;
            container.innerHTML =
                '<form class="sidebar-add-group__form" onsubmit="event.preventDefault(); window.LayoutManager.createGroup(this)">' +
                '<input class="sidebar-add-group__input" name="name" placeholder="Group name" maxlength="50" autofocus>' +
                '<button type="submit" class="btn btn--sm btn--ghost" title="Create">&#10003;</button>' +
                '<button type="button" class="btn btn--sm btn--ghost" title="Cancel" onclick="window.LayoutManager.hideAddGroup()">&#10005;</button>' +
                '</form>';
            container.querySelector("input").focus();
        },

        hideAddGroup: function () {
            var container = document.getElementById("sidebar-add-group");
            if (!container) return;
            container.innerHTML =
                '<button class="sidebar-add-group__btn" onclick="window.LayoutManager && window.LayoutManager.showAddGroup()">+ New Group</button>';
        },

        createGroup: function (form) {
            var name = form.querySelector("input[name=name]").value.trim();
            if (!name) return;
            fetch("/preferences/layout/sidebar/groups", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name })
            }).then(function (res) {
                if (res.ok) { location.reload(); }
            });
        },

        renameGroup: function (groupId, currentName) {
            var newName = prompt("Rename group:", currentName);
            if (!newName || newName.trim() === currentName) return;
            fetch("/preferences/layout/sidebar/groups/" + groupId, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: newName.trim() })
            }).then(function (res) {
                if (res.ok) { location.reload(); }
            });
        },

        deleteGroup: function (groupId) {
            if (!confirm("Delete this group? Items will move to ungrouped.")) return;
            fetch("/preferences/layout/sidebar/groups/" + groupId, {
                method: "DELETE"
            }).then(function (res) {
                if (res.ok) { location.reload(); }
            });
        },

        // ---------------------------------------------------------------
        // Dashboard card drag-and-drop
        // ---------------------------------------------------------------
        initDashboard: function () {
            if (typeof Sortable === "undefined") return;

            var grid = document.getElementById("dashboard-grid");
            if (!grid) return;

            // Destroy previous
            if (this._dashboardSortable) {
                this._dashboardSortable.destroy();
                this._dashboardSortable = null;
            }

            var self = this;
            this._dashboardSortable = Sortable.create(grid, {
                animation: 150,
                ghostClass: "sortable-ghost",
                chosenClass: "sortable-chosen",
                dragClass: "sortable-drag",
                handle: ".card__header",
                onEnd: function () { self.saveCardOrder(); }
            });

            // Add draggable class for cursor styling
            grid.querySelectorAll(".card").forEach(function (card) {
                card.classList.add("card--draggable");
            });
        },

        saveCardOrder: function () {
            var grid = document.getElementById("dashboard-grid");
            if (!grid) return;
            var order = [];
            grid.querySelectorAll("[data-instance-id]").forEach(function (el) {
                order.push(parseInt(el.dataset.instanceId, 10));
            });
            fetch("/preferences/layout/card-order", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ card_order: order })
            });
        }
    };

    // Expose globally
    window.LayoutManager = LayoutManager;

    // Auto-init on DOM ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { LayoutManager.init(); });
    } else {
        LayoutManager.init();
    }
})();
