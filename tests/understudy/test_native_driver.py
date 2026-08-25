"""The native driver's testable surface.

The pywinauto contact cannot run here, so what is covered is: the protocol
shape, the tree walk (against fake wrappers, including ones that throw), the
anchor point arithmetic, and that every unavailable path fails with a message
that says what to do. The matching itself lives in `native_match` and has its
own tests.
"""

from __future__ import annotations

import pytest

from understudy.drivers import build
from understudy.drivers.base import Driver, DriverError
from understudy.drivers.native import NativeDriver, _Point, walk
from understudy.vision import Match


class FakeInfo:
    def __init__(self, **fields):
        self.__dict__.update({
            "control_type": "", "automation_id": "", "name": "",
            "class_name": "", **fields,
        })

    @property
    def rectangle(self):
        raise RuntimeError("no rectangle on this control")


class FakeWrapper:
    def __init__(self, info=None, children=(), throws=False):
        self.element_info = info or FakeInfo()
        self._children = list(children)
        self._throws = throws

    def children(self):
        if self._throws:
            raise RuntimeError("COM error 0x80040201")
        return self._children

    def is_enabled(self):
        return True

    def is_visible(self):
        return True


class Exploding:
    """A control whose properties throw. Routine in custom-drawn UIs."""

    @property
    def element_info(self):
        raise RuntimeError("no accessible information")

    def children(self):
        return []


def test_the_driver_satisfies_the_protocol():
    assert isinstance(NativeDriver(), Driver)
    assert NativeDriver().backend == "native"


def test_the_registry_hands_it_out():
    assert isinstance(build("native"), NativeDriver)


class TestTreeWalk:
    def test_a_tree_flattens_with_depths_and_paths(self):
        tree = FakeWrapper(
            FakeInfo(control_type="Window", name="CATIA V5"),
            children=[
                FakeWrapper(FakeInfo(control_type="MenuBar")),
                FakeWrapper(
                    FakeInfo(control_type="Pane", name="Specification Tree"),
                    children=[FakeWrapper(FakeInfo(control_type="TreeItem", name="Pad.1"))],
                ),
            ],
        )
        found = walk(tree)

        assert [e.depth for e in found] == [0, 1, 1, 2]
        pad = found[-1]
        assert pad.name == "Pad.1"
        assert "Specification Tree" in pad.ancestors, (
            "a named pane should be reachable by name in a path"
        )

    def test_an_element_that_throws_does_not_stop_the_walk(self):
        tree = FakeWrapper(
            FakeInfo(control_type="Window"),
            children=[Exploding(), FakeWrapper(FakeInfo(control_type="Button"))],
        )
        found = walk(tree)
        assert len(found) == 3
        assert found[-1].control_type == "Button"

    def test_a_container_that_refuses_to_enumerate_is_not_fatal(self):
        """The signature of an opaque viewport."""
        found = walk(FakeWrapper(FakeInfo(control_type="Pane"), throws=True))
        assert len(found) == 1

    def test_the_walk_is_bounded(self, monkeypatch):
        # CATIA's tree is large; an unbounded walk would hang the run.
        import understudy.drivers.native as native

        monkeypatch.setattr(native, "MAX_ELEMENTS", 5)
        deep = FakeWrapper(FakeInfo(control_type="A"))
        for _ in range(50):
            deep = FakeWrapper(FakeInfo(control_type="A"), children=[deep])
        assert len(walk(deep)) <= 5

    def test_a_missing_rectangle_is_not_an_error(self):
        assert walk(FakeWrapper(FakeInfo(control_type="Pane")))[0].bounds is None


class TestAnchorPoints:
    def test_the_click_point_is_the_centre_of_the_match(self):
        point = _Point(NativeDriver(), Match(100, 200, 40, 20, 0.99))
        assert point.point == (120, 210)

    def test_an_offset_moves_the_click_off_the_anchor(self):
        """Anchor on the static label, click the field below it."""
        point = _Point(NativeDriver(), Match(100, 200, 40, 20, 0.99),
                       offset={"dx": 0, "dy": 26})
        assert point.point == (120, 236)

    def test_reading_text_from_an_anchor_says_why_it_cannot(self):
        point = _Point(NativeDriver(), Match(0, 0, 10, 10, 0.99))
        with pytest.raises(DriverError, match="no text to read"):
            point.get_value()


class TestUnavailablePaths:
    def test_starting_without_pywinauto_says_what_to_install(self):
        # Only meaningful where pywinauto is genuinely absent. Where it is
        # installed the driver gets past the import and spends a minute
        # waiting for a CATIA window that is not there.
        try:
            import pywinauto  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip("pywinauto is installed, so this path cannot be reached")

        with pytest.raises(DriverError, match="pywinauto"):
            NativeDriver().start({"window_title_pattern": "*CATIA*"})

    def test_a_flow_with_no_native_app_config_is_a_clear_error(self, monkeypatch):
        import sys, types

        # Pretend pywinauto is importable so the next check is the one that fires.
        module = types.ModuleType("pywinauto")
        module.Application = object
        module.Desktop = object
        monkeypatch.setitem(sys.modules, "pywinauto", module)

        with pytest.raises(DriverError, match="window_title_pattern"):
            NativeDriver().start({})

    def test_level_two_reset_is_refused_with_a_reason(self):
        with pytest.raises(DriverError, match="level-1 reset steps"):
            NativeDriver().reset()

    def test_using_it_before_start_is_a_clear_error(self):
        with pytest.raises(DriverError, match="before start"):
            NativeDriver().refresh()
