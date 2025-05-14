import asyncio
import logging
from typing import AsyncGenerator
from fastapi import WebSocket

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class WebSocketStream:
    def __init__(self, websocket: WebSocket, chunk_size: int = 1600):
        self.websocket = websocket
        self.buffer = asyncio.Queue()
        self.closed = False
        self.chunk_size = chunk_size

    async def __aenter__(self):
        self.closed = False
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

    async def fill_buffer(self, audio_data: bytes):
        await self.buffer.put(audio_data)

    async def generator(self) -> AsyncGenerator[bytes, None]:
        while not self.closed:
            chunk = await self.buffer.get()
            if chunk is None:
                break
            yield chunk
