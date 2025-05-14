import traceback
from google.cloud import speech
from fastapi import WebSocket
from app.repositories.stream_repository import WebSocketStream
from app.Config.config import Settings
import os

print(f"Google Credentials (WebSocket): {Settings.GOOGLE_APPLICATION_CREDENTIALS}")
print(f"Environment Variable (OS): {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")

async def receive_audio(websocket: WebSocket, stream: WebSocketStream):
    try:
        while True:
            audio_data = await websocket.receive_bytes()
            print("[receive_audio] Received audio data")
            await stream.fill_buffer(audio_data)
    except Exception as e:
        print(f"[receive_audio] Error: {e}")
        traceback.print_exc()
        await websocket.close()
    finally:
        print("[receive_audio] WebSocket connection closed.")

async def transcribe_audio(stream: WebSocketStream, websocket: WebSocket):
    client = speech.SpeechAsyncClient()
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code="en-IN",
        enable_automatic_punctuation=True,
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        interim_results=True,
    )

    audio_generator = stream.generator()
    try:
        async for chunk in audio_generator:
            print("[transcribe_audio] Transcribing chunk")
            request = speech.StreamingRecognizeRequest(audio_content=chunk)
            responses = await client.streaming_recognize(
                requests=[request], timeout=300
            )
            async for response in responses:
                for result in response.results:
                    transcript = result.alternatives[0].transcript
                    print(f"[transcribe_audio] Transcription: {transcript}")
                    await websocket.send_text(transcript)
    except Exception as e:
        print(f"[transcribe_audio] Error: {e}")
        traceback.print_exc()
        await websocket.close()
    finally:
        print("[transcribe_audio] WebSocket connection closed.")

