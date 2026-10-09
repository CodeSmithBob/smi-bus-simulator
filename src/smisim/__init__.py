"""SMI bus simulator: virtual SMI sunblind / roller-shutter drives for controller testing.

The names exported here form the stable in-process API (see docs/api.md). They work
without the web server and without asyncio::

    from smisim import Bus, BusSettings, MotorConfig

    bus = Bus(settings=BusSettings(name="Lab"))
    bus.add_motor(MotorConfig(address=3, kind="venetian"))
    reply, delay = bus.handle_frame(bytes.fromhex("53 02 AB"))  # DOWN to slave 3 -> b"\\xff"
    bus.update(1.0)  # one simulated second
"""

__version__ = "0.3.0"

from .bus import Bus, BusSettings, FrameResult  # noqa: E402
from .motor import DRIVE_PROFILES, FAULTS, BlindKind, Motor, MotorConfig  # noqa: E402

__all__ = [
    "DRIVE_PROFILES",
    "FAULTS",
    "BlindKind",
    "Bus",
    "BusSettings",
    "FrameResult",
    "Motor",
    "MotorConfig",
    "__version__",
]
