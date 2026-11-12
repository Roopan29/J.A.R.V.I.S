"""Computer control modules (Windows-first, safe degradation elsewhere)."""
from .platform_support import PlatformSupport, support  # noqa: F401

__all__ = ["PlatformSupport", "support"]
