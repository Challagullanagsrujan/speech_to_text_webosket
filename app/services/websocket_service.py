import asyncio
from typing import AsyncGenerator
import logging
import traceback
from fastapi import WebSocket, WebSocketDisconnect

from app.Config.config import get_audio_config

logger = logging.getLogger(__name__)

class WebSocketStream:
    """Generates audio chunks from a WebSocket connection."""

    def __init__(self, websocket: WebSocket):
        config = get_audio_config()
        self._rate = config["rate"]
        self._chunk = config["chunk"]
        self._websocket = websocket
        self._buff = asyncio.Queue()
        self.closed = False
        logger.info("WebSocketStream initialized.")

    async def __aenter__(self):
        self.closed = False
        logger.info("WebSocketStream entered context.")
        return self

    async def __aexit__(self, exc_type, exc_val, traceback_obj):
        logger.info(f"WebSocketStream exiting context. Type: {exc_type}, Val: {exc_val}")
        if exc_type:
            logger.error("WebSocketStream exception occurred in context:")
            traceback.print_exception(exc_type, exc_val, traceback_obj)
        self.closed = True
        await self._buff.put(None)
        logger.info("WebSocketStream exited context, sentinel placed in buffer.")

    async def fill_buffer(self, audio_data: bytes):
        """Add incoming audio to buffer."""
        if self.closed:
            logger.warning("Attempted to fill buffer while closed.")
            return
        if audio_data is None:
            logger.warning("Received None data, not adding to buffer.")
            return
            
        await self._buff.put(audio_data)

    async def generator(self) -> AsyncGenerator[bytes, None]:
        """Yields audio chunks from the asyncio Queue."""
        logger.info("WebSocketStream generator starting...")
        chunk_count = 0
        try:
            while not self.closed:
                chunk = await self._buff.get()
                if chunk is None:
                    logger.info(f"Received sentinel (chunk {chunk_count + 1}), ending generation.")
                    self._buff.task_done()
                    return
                
                chunk_count += 1
                data = [chunk]
                
                # Batch chunks if available
                while True: 
                    try:
                        next_chunk = self._buff.get_nowait()
                        if next_chunk is None:
                            logger.info(f"Received sentinel while batching (after chunk {chunk_count}), ending generation.")
                            self._buff.task_done()
                            if data:
                                yield b"".join(data)
                            return
                        
                        data.append(next_chunk)
                        self._buff.task_done()
                    except asyncio.QueueEmpty:
                        break

                yield b"".join(data)
                self._buff.task_done()

        except asyncio.CancelledError:
            logger.info("Generator task cancelled.")
            raise
        except Exception as e:
            logger.error(f"Error during generation: {e}")
            traceback.print_exc()
        finally:
            logger.info(f"Generator finished. Total chunks processed: {chunk_count}")
            self.closed = True

async def receive_audio(websocket: WebSocket, stream: WebSocketStream):
    """Receives audio data over WebSocket and fills buffer."""
    receive_count = 0
    logger.info("receive_audio task started. Waiting for audio data...")
    try:
        while True:
            logger.debug(f"Attempting to receive packet #{receive_count + 1}...")
            audio_data = await websocket.receive_bytes()
            
            if audio_data:
                receive_count += 1
                logger.debug(f"Received packet #{receive_count} ({len(audio_data)} bytes).")
                
                logger.debug(f"Putting packet #{receive_count} into stream buffer...")
                await stream.fill_buffer(audio_data)
                logger.debug(f"Successfully put packet #{receive_count} into stream buffer.")
            else:
                logger.warning("Received empty bytes packet. Ignoring.")

    except WebSocketDisconnect as e:
        logger.info(f"WebSocket disconnected. Code: {e.code}, Reason: {e.reason}. Packets received: {receive_count}")
        stream.closed = True
    except asyncio.CancelledError:
        logger.info("Task cancelled.")
        stream.closed = True
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        traceback.print_exc()
        stream.closed = True
    finally:
        logger.info(f"Task finished. Total packets received: {receive_count}")
        if not stream.closed:
            logger.info("Forcing stream closure in finally block.")
            stream.closed = True
            await stream._buff.put(None)