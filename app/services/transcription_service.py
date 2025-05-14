import re
import sys
import asyncio
import logging
import traceback
from fastapi import WebSocket

from google.cloud import speech

from app.services.websocket_service import WebSocketStream
from app.Config.config import get_speech_recognition_config

logger = logging.getLogger(__name__)

async def transcribe_audio(stream: WebSocketStream, websocket: WebSocket, language_code: str = "en-IN"):
    """Starts the Google Speech-to-Text streaming transcription."""
    logger.info("Transcription service started.")

    client = speech.SpeechAsyncClient()
    streaming_config = get_speech_recognition_config(language_code)

    logger.info("Creating request generator using stream...")
    audio_generator = stream.generator()

    async def request_stream():
        logger.info("Request stream starting...")
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
        logger.info("Sent initial config request.")
         
        chunk_count = 0
        async for chunk in audio_generator:
            chunk_count += 1
            if chunk_count % 50 == 1:
                logger.debug(f"Sending audio chunk #{chunk_count} ({len(chunk)} bytes) to Google...")
            yield speech.StreamingRecognizeRequest(audio_content=chunk)
             
        logger.info(f"Finished sending audio chunks ({chunk_count} total).")

    try:
        logger.info("Calling Google API streaming_recognize...")
        responses = await client.streaming_recognize(
            requests=request_stream(),
            timeout=300
        )

        logger.info("Response stream received from Google. Processing results...")
        async for response in responses:
            if not response.results:
                continue

            result = response.results[0]
            if not result.alternatives:
                continue

            transcript = result.alternatives[0].transcript

            if not result.is_final:
                logger.debug(f"Interim transcript: {transcript}")
                await websocket.send_text(f"INTERIM:{transcript}")
            else:
                logger.info(f"Final transcript: {transcript}")
                await websocket.send_text(f"FINAL:{transcript}")

                if re.search(r"\b(exit|quit)\b", transcript, re.I):
                    logger.info("Exiting on keyword.")
                    break

    except asyncio.CancelledError:
        logger.info("Transcription task cancelled.")
        raise
    except Exception as e:
        logger.error(f"Error during transcription: {e}")
        traceback.print_exc()
        try:
            await websocket.send_text(f"ERROR:Transcription failed: {e}")
        except Exception:
            pass
    finally:
        logger.info("Transcription task finished.")