"""Minimal SMI master using the bundled client library.

Start the simulator first:   smisim run --motors 8
Then run:                    python examples/master_demo.py localhost 4000
"""

import asyncio
import sys

from smisim.client import SmiMaster, TcpLink
from smisim.protocol import Addressing, Command


async def main(host: str, port: int) -> None:
    async with SmiMaster(TcpLink(host, port)) as smi:
        smi.log = print

        # 1. Broadcast: every drive on the line goes up.
        await smi.command(Addressing.broadcast(), Command.UP)

        # 2. Group: drives 0, 2 and 4 go to 50 %.
        await smi.command(Addressing.to_slaves([0, 2, 4]), Command.GOTO, position=0x8000)

        # 3. Single drive: move address 1 down, then tilt its slats by 90 degrees.
        await smi.down(1)
        await asyncio.sleep(3)
        await smi.command(Addressing.to_slave(1), Command.UP, angle_deg=90)

        # 4. Poll every position once per second, five times.
        for _ in range(5):
            positions = [await smi.read_position(a) for a in range(8)]
            print("positions:", ["--" if p is None else f"{p / 655.35:4.0f}%" for p in positions])
            await asyncio.sleep(1)

        # 5. Diagnosis of the whole line.
        await smi.diag(Addressing.broadcast())


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
    asyncio.run(main(host, port))
