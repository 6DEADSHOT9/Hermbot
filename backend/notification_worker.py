"""Independent attention projection and push dispatch, with no model or bot credentials."""

import asyncio
import logging
from pathlib import Path
import signal

from . import attention, db, notifications

STOP = asyncio.Event()
LOGGER = logging.getLogger("hermbot.notifications")


def project_cycle():
    attention.drain()
    with db.connection() as conn:
        attention.reconcile(conn, limit=10)
    attention.maintenance()
    Path("/tmp/hermbot-notifications-heartbeat").touch()


def delivery_cycle():
    delivery = notifications.claim()
    if delivery:
        notifications.deliver(delivery)


async def pump(cycle):
    while not STOP.is_set():
        try:
            await asyncio.to_thread(cycle)
        except Exception:
            LOGGER.error("Notification cycle failed; retained work will retry (private details omitted)")
        try:
            await asyncio.wait_for(STOP.wait(), timeout=0.5)
        except TimeoutError:
            pass


async def main():
    db.initialize()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, STOP.set)
    try:
        await asyncio.gather(pump(project_cycle), pump(delivery_cycle))
    finally:
        db.pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
