from aiohttp.web import StreamResponse
import asyncio


class ResponseAdapter:
    def __init__(self, stream_response: StreamResponse) -> None:
        self.stream_response = stream_response
        self.broken = False

    def write(self, s: str):
        asyncio.get_running_loop().create_task(self._quiet_write(s))

    async def _quiet_write(self, s):
        if self.broken:
            return
        try:
            await self.stream_response.write(s.encode())
        except RuntimeError:
            self.broken = True
        except ConnectionResetError:
            self.broken = True

    async def is_broken(self):
        return self.broken

    def flush(self):
        pass
