"""Bounded caller rotation; refresh ownership is independent from inference slots."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager


class Capacity:
    def __init__(self, limit=2):
        self.limit, self.active = limit, 0
        self.callers, self.waiting = deque(), {}

    def dispatch(self):
        while self.active < self.limit and self.callers:
            caller = self.callers.popleft()
            queue = self.waiting[caller]
            future = queue.popleft()
            if queue:
                self.callers.append(caller)
            else:
                del self.waiting[caller]
            if not future.cancelled():
                self.active += 1
                future.set_result(None)

    @asynccontextmanager
    async def slot(self, caller):
        future = asyncio.get_running_loop().create_future()
        if caller not in self.waiting:
            self.waiting[caller] = deque()
            self.callers.append(caller)
        self.waiting[caller].append(future)
        self.dispatch()
        try:
            await asyncio.shield(future)
            yield
        finally:
            if future.done() and not future.cancelled():
                self.active -= 1
            else:
                future.cancel()
            self.dispatch()
