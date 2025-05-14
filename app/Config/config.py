import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Audio parameters
RATE = 16000
CHUNK = int(RATE / 10)  # 100ms

def Settings():
    """Load environment variables and set up necessary configurations."""
    # Load environment variables
    load_dotenv()

    # Configure Google Cloud credentials
    GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if GOOGLE_APPLICATION_CREDENTIALS is not None:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_APPLICATION_CREDENTIALS
    else:
        logger.warning("GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")

    # Configure Project ID
    PROJECT_ID = os.getenv("PROJECT_ID")
    if PROJECT_ID is not None:
        os.environ["PROJECT_ID"] = PROJECT_ID
    else:
        logger.warning("PROJECT_ID environment variable not set.")
    
    logger.info("Environment setup complete")

# Function to get configuration settings
def get_audio_config():
    """Get audio configuration parameters."""
    return {
        "rate": RATE,
        "chunk": CHUNK,
    }

def get_speech_recognition_config(language_code="en-IN"):
    """Get Google Speech-to-Text configuration."""
    from google.cloud import speech
    
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
    
    return streaming_config