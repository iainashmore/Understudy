"""Native (UIAutomation) matching.

The half of the native driver that can be tested without Windows. The tree here
is modelled on what a CAD application actually exposes: menu items carrying
Win32 mnemonics and accelerators, a toolbar of custom-drawn controls with no
name and no automation id, a specification tree, a viewport pane with no
children at all, and the same button text appearing in two different panes.
"""

from __future__ import annotations

import pytest

from flowrunner.flow import Strategy, parse_flow
from flowrunner.native_match import (
    ElementDescriptor,
    NoMatch,
    candidates_for,
    matches,
    normalise_name,
    rank_by_stability,
    resolve,
    unique_strategies,
)


def strategy(**fields) -> Strategy:
    return Strategy(backend="native", fields=fields)


def target(*strategies, label: str = "thing"):
    return parse_flow({
        "version": 1, "name": "t", "prompts": [{"id": "a", "prompt": "x"}],
        "targets": {label: {"native": list(strategies)}},
        "steps": [{"action": "click", "target": label}],
    }).target_for(label)


def element(**overrides) -> ElementDescriptor:
    return ElementDescriptor(**overrides)


WINDOW = ("Window",)
TREE_PATH = ("Window", "Pane")


@pytest.fixture
def tree() -> list[ElementDescriptor]:
    """A plausible CAD window."""
    elements = [
        element(control_type="Window", name="CATIA V5 - [Part1.CATPart]",
                class_name="CATIAMainFrame"),
        element(control_type="MenuBar", name="", ancestors=WINDOW, depth=1),
    ]
    for label in ("&File", "&Edit", "&View", "&Insert", "&Tools", "&Window", "&Help"):
        elements.append(element(control_type="MenuItem", name=label,
                                ancestors=("Window", "MenuBar"), depth=2))

    # A custom-drawn toolbar: nothing an accessibility tree can identify.
    for _ in range(8):
        elements.append(element(control_type="Button", name="", automation_id="",
                                class_name="CATIAToolButton",
                                ancestors=("Window", "ToolBar"), depth=2))

    elements.append(element(control_type="Pane", name="Specification Tree",
                            ancestors=WINDOW, depth=1))
    for node in ("Product1", "xy plane", "Part1", "PartBody", "Pad.1", "Pocket.1"):
        elements.append(element(control_type="TreeItem", name=node,
                                ancestors=("Window", "Pane"), depth=2))

    # The viewport: one control, no children. This is the limitation the whole
    # visual-anchor path exists for.
    elements.append(element(control_type="Pane", name="3D Viewport",
                            class_name="CATIAViewer", ancestors=WINDOW, depth=1))

    # The same button text in two different panes.
    for pane in ("Filters", "Selection"):
        elements.append(element(control_type="Pane", name=pane, ancestors=WINDOW, depth=1))
        elements.append(element(control_type="Button", name="Apply",
                                ancestors=("Window", "Pane", pane), depth=2))

    # A modal dialog.
    elements += [
        element(control_type="Window", name="Properties", ancestors=WINDOW, depth=1),
        element(control_type="Edit", automation_id="NameField", name="Name",
                ancestors=("Window", "Window"), depth=2),
        element(control_type="Button", name="OK", ancestors=("Window", "Window"), depth=2),
        element(control_type="Button", name="Cancel", ancestors=("Window", "Window"),
                depth=2, enabled=False),
    ]
    return elements


class TestNameNormalisation:
    """Win32 text carries baggage that has nothing to do with identity."""

    @pytest.mark.parametrize("raw, expected", [
        ("&File", "file"),
        ("S&ave As", "save as"),
        ("Save\tCtrl+S", "save"),
        ("Properties...", "properties"),
        ("Properties…", "properties"),
        ("  Send   Message  ", "send message"),
        ("SEND", "send"),
        ("Search && Replace", "search & replace"),
        ("", ""),
        (None, ""),
    ])
    def test_names_fold_to_what_a_person_would_call_it(self, raw, expected):
        assert normalise_name(raw) == expected

    def test_a_flow_can_say_the_obvious_thing(self, tree):
        """`name: File` should find `&File`, and `name: Properties` should find
        `Properties...`. Making the author copy the mnemonics would be a trap."""
        assert len(candidates_for(tree, strategy(name="File", control_type="MenuItem"))) == 1
        assert candidates_for(tree, strategy(name="Properties", control_type="Window"))


class TestFieldMatching:
    def test_automation_id_is_compared_exactly(self, tree):
        """A developer-chosen identifier. Folding case would merge controls
        that were deliberately kept apart."""
        assert candidates_for(tree, strategy(automation_id="NameField"))
        assert not candidates_for(tree, strategy(automation_id="namefield"))

    def test_control_type_and_class_are_case_insensitive(self, tree):
        assert candidates_for(tree, strategy(control_type="treeitem"))
        assert candidates_for(tree, strategy(class_name="catiaviewer"))

    def test_a_name_matches_as_a_substring_by_default(self, tree):
        assert candidates_for(tree, strategy(control_type="Window", name="Part1.CATPart"))

    def test_exact_turns_that_off(self, tree):
        assert not candidates_for(
            tree, strategy(control_type="Window", name="Part1.CATPart", exact=True)
        )
        assert candidates_for(tree, strategy(control_type="Button", name="OK", exact=True))

    def test_every_field_must_match(self, tree):
        assert not candidates_for(
            tree, strategy(control_type="Button", name="Pad.1")
        ), "Pad.1 is a TreeItem, not a Button"

    def test_a_strategy_with_no_fields_matches_everything(self, tree):
        # Meaningless on its own, but it must not crash -- the schema stops it
        # reaching here in practice.
        assert len(candidates_for(tree, strategy())) == len(tree)


class TestPath:
    def test_path_disambiguates_the_same_control_in_two_panes(self, tree):
        """Two Apply buttons. The path is what tells them apart."""
        assert len(candidates_for(tree, strategy(control_type="Button", name="Apply"))) == 2
        narrowed = candidates_for(
            tree, strategy(control_type="Button", name="Apply",
                           path=["Window", "Pane", "Filters"])
        )
        assert len(narrowed) == 1

    def test_path_is_a_subsequence_not_a_full_chain(self, tree):
        """A real tree is full of anonymous wrappers. Requiring every level
        would break the first time a layout gained a container."""
        assert candidates_for(
            tree, strategy(control_type="Button", name="Apply", path=["Window", "Filters"])
        )

    def test_path_order_matters(self, tree):
        assert not candidates_for(
            tree, strategy(control_type="Button", name="Apply", path=["Filters", "Window"])
        )

    def test_a_path_that_is_not_a_list_matches_nothing(self, tree):
        assert not candidates_for(tree, strategy(control_type="Button", path="Window"))


class TestResolution:
    def test_the_first_strategy_that_identifies_one_element_wins(self, tree):
        found = resolve(tree, target(
            {"automation_id": "NameField"},
            {"control_type": "Edit", "name": "Name"},
        ))
        assert found.index == 0
        assert found.element.automation_id == "NameField"

    def test_resolution_falls_through_and_says_which_rung_it_landed_on(self, tree):
        found = resolve(tree, target(
            {"automation_id": "NoSuchId"},
            {"control_type": "TreeItem", "name": "PartBody"},
        ))
        assert found.index == 1
        assert found.element.name == "PartBody"

    def test_ambiguity_is_not_resolution(self, tree):
        """Two Apply buttons, both usable. Clicking the first would be a wrong
        click that looks like a right one."""
        with pytest.raises(NoMatch, match="2 matches, ambiguous"):
            resolve(tree, target({"control_type": "Button", "name": "Apply"}))

    def test_ambiguity_falls_through_to_the_next_strategy(self, tree):
        found = resolve(tree, target(
            {"control_type": "Button", "name": "Apply"},
            {"control_type": "Button", "name": "Apply", "path": ["Selection"]},
        ))
        assert found.index == 1

    def test_nth_makes_a_deliberate_choice_explicit(self, tree):
        found = resolve(tree, target(
            {"control_type": "Button", "class_name": "CATIAToolButton", "nth": 5}
        ))
        assert "nth=5 of 8" in found.note

    def test_an_out_of_range_nth_is_reported_not_wrapped(self, tree):
        with pytest.raises(NoMatch, match="nth=99 but only 8"):
            resolve(tree, target(
                {"control_type": "Button", "class_name": "CATIAToolButton", "nth": 99}
            ))

    def test_an_unusable_duplicate_does_not_count_as_ambiguity(self, tree):
        """Cancel is disabled here; a lone usable match settles it."""
        extra = tree + [ElementDescriptor(control_type="Button", name="Cancel",
                                          ancestors=("Window",), enabled=True)]
        found = resolve(extra, target({"control_type": "Button", "name": "Cancel"}))
        assert found.element.enabled is True
        assert "one usable" in found.note

    def test_nothing_matching_lists_everything_tried(self, tree):
        with pytest.raises(NoMatch) as caught:
            resolve(tree, target(
                {"automation_id": "Absent"},
                {"control_type": "Button", "name": "Nonexistent"},
            ))
        message = str(caught.value)
        assert "id='Absent'" in message and "no match" in message

    def test_image_and_agent_rungs_are_left_to_the_driver(self, tree):
        """They need a screenshot and a model; the tree matcher says so rather
        than silently failing."""
        with pytest.raises(NoMatch, match="not a tree strategy"):
            resolve(tree, target({"agent": "the measure tool"}))

    def test_a_tree_rung_still_wins_over_a_later_agent_rung(self, tree):
        found = resolve(tree, target(
            {"control_type": "TreeItem", "name": "Pad.1"},
            {"agent": "the Pad.1 node"},
        ))
        assert found.index == 0


class TestTheOpaqueViewport:
    def test_the_viewport_is_a_single_control_with_nothing_inside(self, tree):
        """The reason visual anchoring exists. UIA can find the viewport; it can
        tell you nothing about what is drawn in it."""
        viewport = candidates_for(tree, strategy(class_name="CATIAViewer"))
        assert len(viewport) == 1
        children = [e for e in tree if e.ancestors and e.ancestors[-1] == "CATIAViewer"]
        assert children == []

    def test_the_toolbar_offers_nothing_to_identify_a_button_by(self, tree):
        buttons = candidates_for(tree, strategy(class_name="CATIAToolButton"))
        assert len(buttons) == 8
        assert all(not b.name and not b.automation_id for b in buttons)


class TestStrategySuggestions:
    """What a recorder would emit: several strategies per element, ranked."""

    def test_suggestions_are_ranked_with_the_stable_ones_first(self):
        described = rank_by_stability([ElementDescriptor(
            control_type="Edit", automation_id="NameField", name="Name",
            class_name="Edit", ancestors=("Window", "Window"),
        )])[0]["strategies"]
        assert described[0] == {"automation_id": "NameField"}
        assert {"control_type": "Edit", "name": "Name"} in described

    def test_only_strategies_that_actually_identify_the_element_are_kept(self, tree):
        """A recorder emitting a strategy that matches six controls has
        recorded a bug."""
        apply_button = next(
            e for e in tree if e.name == "Apply" and "Filters" in e.ancestors
        )
        kept = unique_strategies(tree, apply_button)

        assert kept, "the path-scoped strategy should survive"
        assert {"name": "Apply"} not in kept, "matches both panes"
        for fields in kept:
            assert len(candidates_for(tree, strategy(**fields))) == 1

    def test_an_unidentifiable_control_yields_nothing(self, tree):
        """A custom toolbar button has no name, no id, and seven identical
        siblings. Nothing in the tree identifies it -- which is the signal to
        fall back to a visual anchor."""
        button = next(e for e in tree if e.class_name == "CATIAToolButton")
        assert unique_strategies(tree, button) == []


def test_matches_is_the_same_predicate_resolution_uses(tree):
    """Guard against the two drifting apart."""
    spec = strategy(control_type="TreeItem", name="PartBody")
    found = resolve(tree, target(dict(spec.fields)))
    assert matches(found.element, spec)
