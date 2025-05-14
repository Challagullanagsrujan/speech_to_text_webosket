import asyncio
import logging
import traceback
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.connections.connection import register_connection, unregister_connection, get_connection_state, ConnectionState  as connection_repository
from app.services.websocket_service import WebSocketStream, receive_audio
from app.services.transcription_service import transcribe_audio

logger = logging.getLogger(__name__)

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
        logger.info("WebSocket connection accepted.")
    except Exception as e:
        logger.error(f"Error accepting connection: {e}")
        return

    request_id = None
    receive_task = None
    transcribe_task = None

    try:
        # Register connection and get state
        request_id = await register_connection(websocket)
        logger.info(f"Registered connection with ID: {request_id}")
        state = await get_connection_state(request_id)

        async with WebSocketStream(websocket) as stream:
            logger.info("WebSocketStream context entered.")
            
            # Create audio receive and transcription tasks
            receive_task = asyncio.create_task(
                receive_audio(websocket, stream), 
                name=f"receive_audio_{request_id}"
            )
            
            transcribe_task = asyncio.create_task(
                transcribe_audio(stream, websocket), 
                name=f"transcribe_audio_{request_id}"
            )
            
            # Store the receiver task in connection state
            async with state.lock:
                state.receiver_task = receive_task
                
            logger.info("receive_audio and transcribe_audio tasks created.")

            # Wait for either task to complete
            done, pending = await asyncio.wait(
                [receive_task, transcribe_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            
            logger.info(f"asyncio.wait finished. Done tasks: {len(done)}, Pending tasks: {len(pending)}")

            # Log completed tasks
            for task in done:
                if task.exception():
                    logger.error(f"Task {task.get_name()} completed with exception: {task.exception()}")
                else:
                    logger.info(f"Task {task.get_name()} completed successfully.")

            # Cancel pending tasks
            for task in pending:
                logger.info(f"Cancelling pending task: {task.get_name()}")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"Pending task {task.get_name()} cancellation confirmed.")
                except Exception as e:
                    logger.error(f"Exception during cancellation of pending task {task.get_name()}: {e}")

    except WebSocketDisconnect as e:
        logger.info(f"WebSocket disconnected during setup or main loop. Code: {e.code}")
    except Exception as e:
        logger.error(f"Unexpected error in main endpoint logic: {e}")
        traceback.print_exc()
    finally:
        logger.info("Entering finally block...")
        
        # Clean up tasks if they still exist
        if receive_task and not receive_task.done():
            logger.info("Cancelling receive_task in finally block...")
            receive_task.cancel()
            try: 
                await receive_task
            except asyncio.CancelledError: 
                pass
            except Exception as e: 
                logger.error(f"Error awaiting cancelled receive_task: {e}")

        if transcribe_task and not transcribe_task.done():
            logger.info("Cancelling transcribe_task in finally block...")
            transcribe_task.cancel()
            try: 
                await transcribe_task
            except asyncio.CancelledError: 
                pass
            except Exception as e: 
                logger.error(f"Error awaiting cancelled transcribe_task: {e}")

        # Unregister connection
        if request_id:
            logger.info(f"Unregistering connection ID: {request_id}")
            await unregister_connection(request_id)
        
        # Close WebSocket if still open
        try:
            if websocket.client_state == websocket.client_state.CONNECTED:
                logger.info("WebSocket still connected, attempting close...")
                await websocket.close()
                logger.info("WebSocket closed in finally block.")
            elif websocket.client_state == websocket.client_state.DISCONNECTED:
                logger.info("WebSocket already disconnected.")
            else:
                logger.info(f"WebSocket in unexpected state: {websocket.client_state}")

        except Exception as e:
            logger.error(f"Error closing WebSocket in finally block: {e}")
            
        logger.info("Cleanup complete. Connection closed.")