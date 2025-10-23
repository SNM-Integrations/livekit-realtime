"""
Deepgram Nova-3 Speech-to-Text Component
Streaming STT for Swedish language with LiveKit integration
"""

import os
import asyncio
import logging
from typing import Optional, Callable, Awaitable
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

logger = logging.getLogger(__name__)


class DeepgramSTT:
    """
    Deepgram Nova-3 streaming STT for Swedish (sv-SE).
    Processes audio chunks and emits transcription events.
    """

    def __init__(
        self,
        api_key: str,
        language: str = "sv-SE",
        model: str = "nova-3",
        on_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
    ):
        """
        Args:
            api_key: Deepgram API key
            language: Language code (default: sv-SE for Swedish)
            model: Deepgram model (default: nova-3)
            on_transcript: Async callback for transcripts (text, is_final)
        """
        self.api_key = api_key
        self.language = language
        self.model = model
        self.on_transcript = on_transcript

        # Deepgram client and connection
        self.client: Optional[DeepgramClient] = None
        self.connection = None
        self._connected = False

        logger.info(f"🎤 DeepgramSTT initialized: model={model}, language={language}")

    async def connect(self):
        """Establish WebSocket connection to Deepgram"""
        try:
            # Initialize client
            config = DeepgramClientOptions(
                options={"keepalive": "true"}
            )
            self.client = DeepgramClient(self.api_key, config)

            # Create live transcription connection
            self.connection = self.client.listen.asyncwebsocket.v("1")

            # Set up event handlers
            self.connection.on(LiveTranscriptionEvents.Transcript, self._on_message)
            self.connection.on(LiveTranscriptionEvents.Error, self._on_error)
            self.connection.on(LiveTranscriptionEvents.Close, self._on_close)

            # Configure options
            options = LiveOptions(
                model=self.model,
                language=self.language,
                smart_format=True,  # Punctuation and formatting
                interim_results=False,  # Only final transcripts (reduces noise)
                encoding="linear16",  # PCM format
                sample_rate=16000,  # 16kHz (standard for phone audio)
                channels=1,  # Mono
            )

            # Start connection
            await self.connection.start(options)
            self._connected = True

            logger.info(f"✅ Deepgram connected: {self.model} ({self.language})")

        except Exception as e:
            logger.error(f"❌ Deepgram connection failed: {e}")
            raise

    async def send_audio(self, audio_data: bytes):
        """
        Send audio chunk to Deepgram for transcription

        Args:
            audio_data: Raw PCM audio bytes (linear16, 16kHz, mono)
        """
        if not self._connected or not self.connection:
            logger.warning("⚠️ Deepgram not connected, skipping audio chunk")
            return

        try:
            await self.connection.send(audio_data)
        except Exception as e:
            logger.error(f"❌ Error sending audio to Deepgram: {e}")

    async def _on_message(self, *args, **kwargs):
        """Handle incoming transcription from Deepgram"""
        try:
            result = kwargs.get("result")
            if not result:
                return

            # Extract transcript
            transcript = result.channel.alternatives[0].transcript
            is_final = result.is_final

            if not transcript or transcript.strip() == "":
                return

            logger.info(f"📝 Deepgram transcript ({'final' if is_final else 'interim'}): {transcript}")

            # Call user callback if provided
            if self.on_transcript and is_final:
                await self.on_transcript(transcript, is_final)

        except Exception as e:
            logger.error(f"❌ Error processing Deepgram message: {e}")

    def _on_error(self, *args, **kwargs):
        """Handle Deepgram errors"""
        error = kwargs.get("error")
        logger.error(f"❌ Deepgram error: {error}")

    def _on_close(self, *args, **kwargs):
        """Handle connection close"""
        logger.info("🔌 Deepgram connection closed")
        self._connected = False

    async def close(self):
        """Close Deepgram connection"""
        if self.connection:
            try:
                await self.connection.finish()
                logger.info("✅ Deepgram connection finished")
            except Exception as e:
                logger.error(f"❌ Error closing Deepgram: {e}")
            finally:
                self._connected = False
                self.connection = None

    @property
    def is_connected(self) -> bool:
        """Check if Deepgram is connected"""
        return self._connected
