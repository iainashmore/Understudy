"""Driver implementations, one per backend."""

from flowrunner.drivers.base import Driver, DriverError, Resolution, TargetNotFound

BACKENDS = ("web", "native")


def build(backend: str, **options):
    """Native lands when there is a Windows machine to exercise it against;
    writing an untested Win32 hook layer would be worse than not shipping one."""
    if backend == "web":
        from flowrunner.drivers.web import WebDriver

        return WebDriver(**options)
    if backend == "native":
        raise NotImplementedError(
            "the native (UIAutomation) driver is not built yet; available: web"
        )
    raise KeyError(f"unknown backend {backend!r}; expected one of {BACKENDS}")


__all__ = ["BACKENDS", "Driver", "DriverError", "Resolution", "TargetNotFound", "build"]
