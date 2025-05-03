import os
import sys
import re
import queue
import asyncio
import concurrent.futures
from typing import AsyncGenerator
import time
import traceback

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from google.cloud import speech
import dotenv

from connection import register_connection, unregister_connection, get_connection_state, ConnectionState

# Load environment variables
dotenv.load_dotenv()
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
os.environ["Project_ID"] = os.getenv("Project_ID")

# Audio parameters
RATE = 16000
CHUNK = int(RATE / 10)  # 100ms

app = FastAPI()

# Allow all CORS origins for testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class WebSocketStream:
    """Generates audio chunks from a WebSocket connection."""

    def __init__(self, websocket: WebSocket, rate: int = RATE, chunk: int = CHUNK):
        self._rate = rate
        self._chunk = chunk
        self._websocket = websocket
        self._buff = asyncio.Queue()
        self.closed = False
        print("[WebSocketStream] Initialized.")

    async def __aenter__(self):
        self.closed = False
        print("[WebSocketStream] Entered context.")
        return self

    async def __aexit__(self, exc_type, exc_val, traceback_obj):
        print(f"[WebSocketStream] Exiting context. Type: {exc_type}, Val: {exc_val}")
        if exc_type:
             print("[WebSocketStream] Exception occurred in context:")
             traceback.print_exception(exc_type, exc_val, traceback_obj)
        self.closed = True
        await self._buff.put(None)
        print("[WebSocketStream] Exited context, sentinel placed in buffer.")

    async def fill_buffer(self, audio_data: bytes):
        """Add incoming audio to buffer."""
        if self.closed:
             print("[WebSocketStream.fill_buffer] Attempted to fill buffer while closed.")
             return
        if audio_data is None:
            print("[WebSocketStream.fill_buffer] Received None data, not adding to buffer.")
            return
            
        await self._buff.put(audio_data)

    async def generator(self) -> AsyncGenerator[bytes, None]:
        """Yields audio chunks from the asyncio Queue."""
        print("[WebSocketStream.generator] Starting generator...")
        chunk_count = 0
        try:
            while not self.closed:
                chunk = await self._buff.get()
                if chunk is None:
                    print(f"[WebSocketStream.generator] Received sentinel (chunk {chunk_count + 1}), ending generation.")
                    self._buff.task_done()
                    return
                
                chunk_count += 1
                data = [chunk]
                
                while True: 
                    try:
                        next_chunk = self._buff.get_nowait()
                        if next_chunk is None:
                            print(f"[WebSocketStream.generator] Received sentinel while batching (after chunk {chunk_count}), ending generation.")
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
             print("[WebSocketStream.generator] Generator task cancelled.")
             raise
        except Exception as e:
             print(f"[WebSocketStream.generator] Error during generation: {e}")
             traceback.print_exc()
        finally:
             print(f"[WebSocketStream.generator] Generator finished. Total chunks processed: {chunk_count}")
             self.closed = True

async def receive_audio(websocket: WebSocket, stream: WebSocketStream):
    """Receives audio data over WebSocket and fills buffer."""
    receive_count = 0
    print("[receive_audio] Task started. Waiting for audio data...")
    try:
        while True:
            print(f"[receive_audio] Attempting to receive packet #{receive_count + 1}...")
            audio_data = await websocket.receive_bytes()
            
            if audio_data:
                receive_count += 1
                print(f"[receive_audio] SUCCESS: Received packet #{receive_count} ({len(audio_data)} bytes).")
                
                print(f"[receive_audio] Putting packet #{receive_count} into stream buffer...")
                await stream.fill_buffer(audio_data)
                print(f"[receive_audio] Successfully put packet #{receive_count} into stream buffer.")
            else:
                print("[receive_audio] Received empty bytes packet. Ignoring.")

    except WebSocketDisconnect as e:
        print(f"[receive_audio] WebSocket disconnected. Code: {e.code}, Reason: {e.reason}. Packets received: {receive_count}")
        stream.closed = True
    except asyncio.CancelledError:
         print("[receive_audio] Task cancelled.")
         stream.closed = True
         raise
    except Exception as e:
        print(f"[receive_audio] An unexpected error occurred: {e}")
        traceback.print_exc()
        stream.closed = True
    finally:
        print(f"[receive_audio] Task finished. Total packets received: {receive_count}")
        if not stream.closed:
             print("[receive_audio] Forcing stream closure in finally block.")
             stream.closed = True
             await stream._buff.put(None)

async def listen_print_loop(responses, websocket: WebSocket):
    """Sends interim and final transcriptions to client."""
    num_chars_printed = 0
    for response in responses:
        if not response.results:
            continue

        result = response.results[0]
        if not result.alternatives:
            continue

        transcript = result.alternatives[0].transcript
        overwrite_chars = " " * (num_chars_printed - len(transcript))

        if not result.is_final:
            sys.stdout.write(transcript + overwrite_chars + "\r")
            sys.stdout.flush()
            num_chars_printed = len(transcript)
            await websocket.send_text(f"INTERIM:{transcript}")
        else:
            print(transcript + overwrite_chars)
            await websocket.send_text(f"FINAL:{transcript}")

            if re.search(r"\b(exit|quit)\b", transcript, re.I):
                print("Exiting on keyword.")
                break
            num_chars_printed = 0

async def transcribe_audio(stream: WebSocketStream, websocket: WebSocket):
    """Starts the Google Speech-to-Text streaming transcription."""
    language_code = "en-IN"
    print("[transcribe_audio] Task started.")

    client = speech.SpeechAsyncClient()
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code=language_code,
        enable_automatic_punctuation=True,
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        interim_results=True,
    )

    print("[transcribe_audio] Creating request generator using stream...")
    audio_generator = stream.generator()

    async def request_stream():
         print("[request_stream] Request stream starting...")
         yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
         print("[request_stream] Sent initial config request.")
         
         chunk_count = 0
         async for chunk in audio_generator:
             chunk_count += 1
             if chunk_count % 50 == 1 :
                print(f"[request_stream] Sending audio chunk #{chunk_count} ({len(chunk)} bytes) to Google...")
             yield speech.StreamingRecognizeRequest(audio_content=chunk)
             
         print(f"[request_stream] Finished sending audio chunks ({chunk_count} total).")

    try:
        print("[transcribe_audio] Calling Google API streaming_recognize...")
        responses = await client.streaming_recognize(
            requests=request_stream(),
            timeout=300
        )

        print("[transcribe_audio] Response stream received from Google. Processing results...")
        async for response in responses:
            if not response.results:
                continue

            result = response.results[0]
            if not result.alternatives:
                continue

            transcript = result.alternatives[0].transcript

            if not result.is_final:
                print(f"[transcribe_audio] Interim: {transcript}")
                await websocket.send_text(f"INTERIM:{transcript}")
            else:
                print(f"[transcribe_audio] Final: {transcript}")
                await websocket.send_text(f"FINAL:{transcript}")

    except asyncio.CancelledError:
         print("[transcribe_audio] Task cancelled.")
         raise
    except Exception as e:
        print(f"[transcribe_audio] Error during transcription: {e}")
        traceback.print_exc()
        try:
            await websocket.send_text(f"ERROR:Transcription failed: {e}")
        except Exception:
            pass
    finally:
        print("[transcribe_audio] Task finished.")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
        print("[websocket_endpoint] Connection accepted.")
    except Exception as e:
        print(f"[websocket_endpoint] Error accepting connection: {e}")
        return

    request_id = None
    receive_task = None
    transcribe_task = None

    try:
        request_id = await register_connection(websocket)
        print(f"[websocket_endpoint] Registered connection with ID: {request_id}")
        state: ConnectionState = await get_connection_state(request_id)

        async with WebSocketStream(websocket) as stream:
            print("[websocket_endpoint] WebSocketStream context entered.")
            
            receive_task = asyncio.create_task(receive_audio(websocket, stream), name=f"receive_audio_{request_id}")
            transcribe_task = asyncio.create_task(transcribe_audio(stream, websocket), name=f"transcribe_audio_{request_id}")
            
            async with state.lock:
                state.receiver_task = receive_task
            print("[websocket_endpoint] receive_audio and transcribe_audio tasks created.")

            done, pending = await asyncio.wait(
                [receive_task, transcribe_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            
            print(f"[websocket_endpoint] asyncio.wait finished. Done tasks: {len(done)}, Pending tasks: {len(pending)}")

            for task in done:
                if task.exception():
                    print(f"[websocket_endpoint] Task {task.get_name()} completed with exception: {task.exception()}")
                else:
                     print(f"[websocket_endpoint] Task {task.get_name()} completed successfully.")

            for task in pending:
                print(f"[websocket_endpoint] Cancelling pending task: {task.get_name()}")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    print(f"[websocket_endpoint] Pending task {task.get_name()} cancellation confirmed.")
                except Exception as e:
                     print(f"[websocket_endpoint] Exception during cancellation of pending task {task.get_name()}: {e}")

    except WebSocketDisconnect as e:
        print(f"[websocket_endpoint] WebSocket disconnected during setup or main loop. Code: {e.code}")
    except Exception as e:
        print(f"[websocket_endpoint] Unexpected error in main endpoint logic: {e}")
        traceback.print_exc()
    finally:
        print("[websocket_endpoint] Entering finally block...")
        
        if receive_task and not receive_task.done():
            print("[websocket_endpoint] Cancelling receive_task in finally block...")
            receive_task.cancel()
            try: await receive_task
            except asyncio.CancelledError: pass
            except Exception as e: print(f"Error awaiting cancelled receive_task: {e}")

        if transcribe_task and not transcribe_task.done():
            print("[websocket_endpoint] Cancelling transcribe_task in finally block...")
            transcribe_task.cancel()
            try: await transcribe_task
            except asyncio.CancelledError: pass
            except Exception as e: print(f"Error awaiting cancelled transcribe_task: {e}")

        if request_id:
            print(f"[websocket_endpoint] Unregistering connection ID: {request_id}")
            await unregister_connection(request_id)
        
        try:
            if websocket.client_state == websocket.client_state.CONNECTED:
                 print("[websocket_endpoint] WebSocket still connected, attempting close...")
                 await websocket.close()
                 print("[websocket_endpoint] WebSocket closed in finally block.")
            elif websocket.client_state == websocket.client_state.DISCONNECTED:
                 print("[websocket_endpoint] WebSocket already disconnected.")
            else:
                 print(f"[websocket_endpoint] WebSocket in unexpected state: {websocket.client_state}")

        except Exception as e:
            print(f"[websocket_endpoint] Error closing WebSocket in finally block: {e}")
            
        print("[websocket_endpoint] Cleanup complete. Connection closed.")
