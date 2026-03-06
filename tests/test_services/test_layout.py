"""Tests for the per-user layout service."""

import json

import pytest

from src.services.layout import (
    SidebarGroup,
    UserLayout,
    get_grouped_sidebar,
    get_ordered_instances,
    merge_with_available,
    new_group_id,
    parse_layout,
    serialize_layout,
)


# ---------------------------------------------------------------------------
# parse_layout
# ---------------------------------------------------------------------------


class TestParseLayout:
    def test_none_returns_empty(self):
        layout = parse_layout(None)
        assert layout.sidebar_groups == []
        assert layout.card_order == []
        assert layout.hidden_instance_ids == []

    def test_empty_string_returns_empty(self):
        layout = parse_layout("")
        assert layout.sidebar_groups == []

    def test_invalid_json_returns_empty(self):
        layout = parse_layout("{bad json!!!")
        assert layout.sidebar_groups == []

    def test_valid_json_parsed(self):
        raw = json.dumps({
            "sidebar_groups": [
                {"id": "g1", "name": "Media", "collapsed": True, "instance_ids": [1, 2]},
                {"id": "g2", "name": "Infra", "collapsed": False, "instance_ids": [3]},
            ],
            "card_order": [3, 1, 2],
            "hidden_instance_ids": [4],
        })
        layout = parse_layout(raw)
        assert len(layout.sidebar_groups) == 2
        assert layout.sidebar_groups[0].id == "g1"
        assert layout.sidebar_groups[0].name == "Media"
        assert layout.sidebar_groups[0].collapsed is True
        assert layout.sidebar_groups[0].instance_ids == [1, 2]
        assert layout.sidebar_groups[1].id == "g2"
        assert layout.card_order == [3, 1, 2]
        assert layout.hidden_instance_ids == [4]

    def test_missing_fields_default(self):
        raw = json.dumps({"sidebar_groups": [{"id": "g1", "name": "X"}]})
        layout = parse_layout(raw)
        assert layout.sidebar_groups[0].collapsed is False
        assert layout.sidebar_groups[0].instance_ids == []
        assert layout.card_order == []
        assert layout.hidden_instance_ids == []

    def test_group_without_id_gets_generated(self):
        raw = json.dumps({"sidebar_groups": [{"name": "Auto"}]})
        layout = parse_layout(raw)
        assert len(layout.sidebar_groups[0].id) > 0

    def test_group_without_name_gets_default(self):
        raw = json.dumps({"sidebar_groups": [{"id": "x"}]})
        layout = parse_layout(raw)
        assert layout.sidebar_groups[0].name == "Unnamed"


# ---------------------------------------------------------------------------
# serialize_layout
# ---------------------------------------------------------------------------


class TestSerializeLayout:
    def test_roundtrip(self):
        original = UserLayout(
            sidebar_groups=[
                SidebarGroup(id="g1", name="Media", collapsed=True, instance_ids=[1, 2]),
            ],
            card_order=[2, 1, 3],
            hidden_instance_ids=[4],
        )
        raw = serialize_layout(original)
        restored = parse_layout(raw)
        assert len(restored.sidebar_groups) == 1
        assert restored.sidebar_groups[0].id == "g1"
        assert restored.sidebar_groups[0].name == "Media"
        assert restored.sidebar_groups[0].collapsed is True
        assert restored.sidebar_groups[0].instance_ids == [1, 2]
        assert restored.card_order == [2, 1, 3]
        assert restored.hidden_instance_ids == [4]

    def test_empty_layout_serializes(self):
        raw = serialize_layout(UserLayout())
        data = json.loads(raw)
        assert data["sidebar_groups"] == []
        assert data["card_order"] == []
        assert data["hidden_instance_ids"] == []


# ---------------------------------------------------------------------------
# merge_with_available
# ---------------------------------------------------------------------------


class TestMergeWithAvailable:
    def test_prunes_deleted_instances(self):
        layout = UserLayout(
            sidebar_groups=[
                SidebarGroup(id="g1", name="X", instance_ids=[1, 2, 3]),
            ],
            card_order=[1, 2, 3],
            hidden_instance_ids=[3],
        )
        merged = merge_with_available(layout, [1, 2])
        assert merged.sidebar_groups[0].instance_ids == [1, 2]
        assert merged.card_order == [1, 2]
        assert merged.hidden_instance_ids == []

    def test_appends_new_instances(self):
        layout = UserLayout(card_order=[1, 2])
        merged = merge_with_available(layout, [1, 2, 3, 4])
        assert 3 in merged.card_order
        assert 4 in merged.card_order
        # Original order preserved
        assert merged.card_order.index(1) < merged.card_order.index(3)

    def test_no_duplicates_in_card_order(self):
        layout = UserLayout(
            sidebar_groups=[SidebarGroup(id="g1", name="X", instance_ids=[1])],
            card_order=[1, 2],
        )
        merged = merge_with_available(layout, [1, 2, 3])
        assert merged.card_order.count(1) == 1
        assert merged.card_order.count(3) == 1

    def test_hidden_not_re_added(self):
        layout = UserLayout(
            card_order=[1],
            hidden_instance_ids=[2],
        )
        merged = merge_with_available(layout, [1, 2])
        # Instance 2 is hidden — it's tracked, shouldn't be appended again
        assert merged.card_order.count(2) == 0
        assert 2 in merged.hidden_instance_ids

    def test_empty_layout_gets_all_ids(self):
        merged = merge_with_available(UserLayout(), [5, 3, 1])
        assert sorted(merged.card_order) == [1, 3, 5]


# ---------------------------------------------------------------------------
# get_ordered_instances
# ---------------------------------------------------------------------------


class TestGetOrderedInstances:
    def _inst(self, iid: int) -> dict:
        return {"instance_id": iid, "display_name": f"inst-{iid}"}

    def test_respects_card_order(self):
        layout = UserLayout(card_order=[3, 1, 2])
        instances = [self._inst(1), self._inst(2), self._inst(3)]
        ordered = get_ordered_instances(layout, instances)
        assert [i["instance_id"] for i in ordered] == [3, 1, 2]

    def test_hidden_excluded(self):
        layout = UserLayout(card_order=[1, 2, 3], hidden_instance_ids=[2])
        instances = [self._inst(1), self._inst(2), self._inst(3)]
        ordered = get_ordered_instances(layout, instances)
        assert [i["instance_id"] for i in ordered] == [1, 3]

    def test_unordered_go_to_end(self):
        layout = UserLayout(card_order=[2])
        instances = [self._inst(1), self._inst(2), self._inst(3)]
        ordered = get_ordered_instances(layout, instances)
        assert ordered[0]["instance_id"] == 2
        assert len(ordered) == 3

    def test_empty_order_returns_all(self):
        layout = UserLayout()
        instances = [self._inst(1), self._inst(2)]
        ordered = get_ordered_instances(layout, instances)
        assert len(ordered) == 2


# ---------------------------------------------------------------------------
# get_grouped_sidebar
# ---------------------------------------------------------------------------


class TestGetGroupedSidebar:
    def _sb(self, iid: int) -> dict:
        return {
            "instance_id": iid,
            "display_name": f"inst-{iid}",
            "type_id": "test",
            "health_status": "ok",
            "health_message": "",
        }

    def test_basic_grouping(self):
        layout = UserLayout(
            sidebar_groups=[
                SidebarGroup(id="g1", name="Media", instance_ids=[1, 2]),
            ],
        )
        sidebar = [self._sb(1), self._sb(2), self._sb(3)]
        result = get_grouped_sidebar(layout, sidebar)
        assert len(result["groups"]) == 1
        assert result["groups"][0]["name"] == "Media"
        assert [i["instance_id"] for i in result["groups"][0]["instances"]] == [1, 2]
        assert [i["instance_id"] for i in result["ungrouped"]] == [3]

    def test_hidden_excluded_from_both(self):
        layout = UserLayout(
            sidebar_groups=[
                SidebarGroup(id="g1", name="X", instance_ids=[1, 2]),
            ],
            hidden_instance_ids=[2],
        )
        sidebar = [self._sb(1), self._sb(2), self._sb(3)]
        result = get_grouped_sidebar(layout, sidebar)
        assert [i["instance_id"] for i in result["groups"][0]["instances"]] == [1]
        assert [i["instance_id"] for i in result["ungrouped"]] == [3]

    def test_empty_layout_all_ungrouped(self):
        layout = UserLayout()
        sidebar = [self._sb(1), self._sb(2)]
        result = get_grouped_sidebar(layout, sidebar)
        assert len(result["groups"]) == 0
        assert len(result["ungrouped"]) == 2

    def test_collapse_state_preserved(self):
        layout = UserLayout(
            sidebar_groups=[
                SidebarGroup(id="g1", name="X", collapsed=True, instance_ids=[1]),
            ],
        )
        result = get_grouped_sidebar(layout, [self._sb(1)])
        assert result["groups"][0]["collapsed"] is True

    def test_deleted_instance_not_in_group(self):
        layout = UserLayout(
            sidebar_groups=[
                SidebarGroup(id="g1", name="X", instance_ids=[1, 99]),
            ],
        )
        sidebar = [self._sb(1)]
        result = get_grouped_sidebar(layout, sidebar)
        assert [i["instance_id"] for i in result["groups"][0]["instances"]] == [1]


# ---------------------------------------------------------------------------
# new_group_id
# ---------------------------------------------------------------------------


class TestNewGroupId:
    def test_unique(self):
        ids = {new_group_id() for _ in range(100)}
        assert len(ids) == 100

    def test_is_string(self):
        assert isinstance(new_group_id(), str)
