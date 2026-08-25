"""Driver implementations, one per backend."""

from understudy.drivers.base import Driver, DriverError, Resolution, TargetNotFound

BACKENDS = ("web", "native")


def build(backend: str, **options):
    """Native lands when there is a Windows machine to exercise it against;
    writing an untested Win32 hook layer would be worse than not shipping one."""
    if backend == "web":
        from understudy.drivers.web import WebDriver

        return WebDriver(**options)
    if backend == "native":
        # Registered, but never yet run against a real application: there was
        # no Windows machine to exercise it on. The matching logic underneath
        # it is tested; the pywinauto contact is not.
        from understudy.drivers.native import NativeDriver

        return NativeDriver(**options)
    raise KeyError(f"unknown backend {backend!r}; expected one of {BACKENDS}")


__all__ = ["BACKENDS", "Driver", "DriverError", "Resolution", "TargetNotFound", "build"]
