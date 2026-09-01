"""Driver implementations.

One backend: a Windows application, driven through its window. Understudy
drove web pages too until it was pointed at what it was built for -- a CAD
assistant with no accessibility tree and an embedded panel with no reachable
DOM -- and neither rung was there when it mattered. What is left is the rung
that was: pictures and OCR.
"""

from understudy.drivers.base import Driver, DriverError, Resolution, TargetNotFound

BACKENDS = ("native",)


def build(backend: str = "native", **options):
    if backend != "native":
        raise KeyError(f"unknown backend {backend!r}; expected one of {BACKENDS}")
    from understudy.drivers.native import NativeDriver

    return NativeDriver(**options)


__all__ = ["BACKENDS", "Driver", "DriverError", "Resolution", "TargetNotFound", "build"]
