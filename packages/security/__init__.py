"""Shared security primitives used at every execution boundary."""
from .paths import PathConfinementError, confine_path
from .stop import StopLatch, EmergencyStopActive

__all__ = ["PathConfinementError", "confine_path", "StopLatch", "EmergencyStopActive"]
