import asyncio
import uuid
from typing import Dict, Optional, Any, List
from fastapi import WebSocket
import logging

log = logging.getLogger(__name__)

class ConnectionState:
    """Holds all state associated with a single WebSocket connection."""
    def __init__(self, websocket: WebSocket):
        self.websocket: WebSocket = websocket
        self.answer_holder: List[Any] = []
        self.receiver_task: Optional[asyncio.Task] = None
        self.lock = asyncio.Lock() # Protects answer_holder and receiver_task

# Global store: {request_id: ConnectionState}
# WARNING: MUST ensure cleanup in endpoints to avoid memory leaks!
_active_connections: Dict[str, ConnectionState] = {}
_global_lock = asyncio.Lock() # Lock to protect the global dictionary itself

def generate_request_id() -> str:
    """Generates a unique ID."""
    return str(uuid.uuid4())

async def register_connection(websocket: WebSocket) -> str:
    """Creates state, stores it globally, and returns the request ID."""
    request_id = generate_request_id()
    state = ConnectionState(websocket)
    async with _global_lock:
        _active_connections[request_id] = state
    log.info(f"Registered connection state for request_id: {request_id}")
    return request_id

async def unregister_connection(request_id: str):
    """Removes connection state from the global store."""
    async with _global_lock:
        state = _active_connections.pop(request_id, None)
        if state:
            log.info(f"Unregistered connection state for request_id: {request_id}")
            # Use lock to safely access receiver_task for cancellation
            async with state.lock:
                task_to_cancel = state.receiver_task
                state.receiver_task = None # Clear handle
            # Cancel outside the lock
            if task_to_cancel and not task_to_cancel.done():
                log.debug(f"Cancelling receiver task during unregistration for {request_id}")
                task_to_cancel.cancel()
                try:
                    await asyncio.wait_for(task_to_cancel, timeout=0.1)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
        else:
            log.warning(f"Attempted to unregister non-existent request_id: {request_id}")

async def get_connection_state(request_id: str) -> Optional[ConnectionState]:
    """Retrieves the state object for a given request ID."""
    async with _global_lock:
        return _active_connections.get(request_id)