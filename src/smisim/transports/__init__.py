"""Ways for a real or software SMI master to reach the simulated lines."""

from .base import Link, Transport
from .pty import PtyTransport
from .serial import SerialTransport
from .tcp import TcpTransport

__all__ = ["Link", "PtyTransport", "SerialTransport", "TcpTransport", "Transport"]
