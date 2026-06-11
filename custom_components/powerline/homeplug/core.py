"""Composed HomeplugAV facade and module-level entry points."""
import asyncio
import socket

from .const import ETHERTYPE_HPAV
from .frames import _find_interface
from ._base import _HomeplugBase
from .discovery import DiscoveryMixin
from .state import StateMixin
from .pib import QcaPibMixin
from .control import ControlMixin
from .diagnostics import DiagnosticsMixin


class HomeplugAV(
    _HomeplugBase,
    DiscoveryMixin,
    StateMixin,
    QcaPibMixin,
    ControlMixin,
    DiagnosticsMixin,
):
    """Dual-protocol HomePlug AV adapter control, composed from mixins."""


async def async_discover(interface: str | None = None,
                         timeout: float = 5.0) -> list[dict]:
    hp = HomeplugAV(interface)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, hp.discover, timeout)

async def async_diagnose(interface: str | None = None,
                         timeout: float = 10.0) -> str:
    hp = HomeplugAV(interface)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, hp.diagnose, timeout)

def find_interface() -> str | None:
    return _find_interface()

def is_available() -> bool:
    try:
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                          socket.htons(ETHERTYPE_HPAV))
        s.close()
        return True
    except (PermissionError, OSError):
        return False


__all__ = ["HomeplugAV", "find_interface", "is_available",
           "async_discover", "async_diagnose"]
