"""
Telephony-Optimized TTS Wrapper
================================

Pre-resamples TTS output to 8kHz before sending to LiveKit/SIP to eliminate
real-time resampling artifacts and glitching during full-duplex calls.

Why this works:
- SIP telephony uses 8kHz μ-law encoding
- Standard TTS outputs 22-24kHz
- LiveKit SFU resampling 24kHz→8kHz in real-time causes:
  * CPU contention during duplex audio
  * Buffer underruns
  * Timing jitter
  * Audio glitches/pops

Solution:
- Pre-resample TTS output to 8kHz BEFORE real-time transmission
- Use high-quality offline resampling (soxr - fastest & best)
- LiveKit receives 8kHz frames directly
- No real-time processing = no glitching

Usage:
    from components.telephony_tts import TelephonyOptimizedTTS
    from livekit.plugins import openai

    base_tts = openai.TTS(voice="alloy", model="tts-1")
    tts = TelephonyOptimizedTTS(base_tts)

    session = AgentSession(tts=tts, ...)
"""

import logging
import numpy as np
from typing import Optional
from livekit.agents import tts
from livekit.agents.types import APIConnectOptions

logger = logging.getLogger("telephony-tts")


class TelephonyOptimizedTTS(tts.TTS):
    """
    Wrapper that pre-resamples any TTS output to 8kHz for SIP telephony.

    Eliminates glitching by avoiding real-time resampling on LiveKit SFU.
    """

    def __init__(
        self,
        base_tts: tts.TTS,
        target_sample_rate: int = 8000,
        use_soxr: bool = True,
    ):
        """
        Args:
            base_tts: The underlying TTS engine (OpenAI, ElevenLabs, etc.)
            target_sample_rate: Target sample rate for telephony (default 8000 Hz)
            use_soxr: Use soxr for resampling if available (fastest, highest quality)
        """
        super().__init__(
            capabilities=base_tts.capabilities,
            sample_rate=target_sample_rate,  # Output 8kHz
            num_channels=1,
        )

        self._base_tts = base_tts
        self._target_rate = target_sample_rate
        self._use_soxr = use_soxr

        # Check if soxr is available
        self._resampler = None
        if use_soxr:
            try:
                import soxr
                self._resampler = "soxr"
                logger.info("Using soxr for high-quality resampling")
            except ImportError:
                logger.warning("soxr not available, falling back to scipy")

        if not self._resampler:
            try:
                import scipy.signal
                self._resampler = "scipy"
                logger.info("Using scipy for resampling")
            except ImportError:
                logger.error("No resampling library available! Install soxr or scipy")
                raise ImportError("Install soxr (pip install soxr) or scipy for resampling")

        logger.info(
            f"TelephonyOptimizedTTS initialized: "
            f"target_rate={target_sample_rate}Hz, resampler={self._resampler}"
        )

    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[APIConnectOptions] = None
    ) -> tts.ChunkedStream:
        """Synthesize text and pre-resample to telephony rate"""
        return TelephonyResampledStream(
            base_stream=self._base_tts.synthesize(text, conn_options=conn_options),
            target_rate=self._target_rate,
            resampler=self._resampler,
            tts_instance=self,
        )


class TelephonyResampledStream(tts.ChunkedStream):
    """
    Wraps a TTS ChunkedStream and resamples audio chunks to 8kHz.

    Uses streaming-compatible resampling to minimize latency.
    """

    def __init__(
        self,
        base_stream: tts.ChunkedStream,
        target_rate: int,
        resampler: str,
        tts_instance: tts.TTS,
    ):
        super().__init__(
            tts=tts_instance,
            input_text=base_stream._input_text,
            conn_options=base_stream._conn_options,
        )
        self._base_stream = base_stream
        self._target_rate = target_rate
        self._resampler = resampler
        self._source_rate = None

    def _resample_audio(self, audio_data: bytes, source_rate: int) -> bytes:
        """
        Resample audio data from source_rate to target_rate.

        Args:
            audio_data: Raw PCM16 audio bytes
            source_rate: Source sample rate (e.g., 24000 Hz)

        Returns:
            Resampled PCM16 audio bytes at target_rate
        """
        # Convert bytes to numpy array (int16)
        audio_array = np.frombuffer(audio_data, dtype=np.int16)

        # If already at target rate, return as-is
        if source_rate == self._target_rate:
            return audio_data

        # Resample based on available library
        if self._resampler == "soxr":
            # Use soxr (fastest, highest quality)
            import soxr

            # soxr expects float32 in range [-1, 1]
            audio_float = audio_array.astype(np.float32) / 32768.0

            # Resample with high quality
            resampled_float = soxr.resample(
                audio_float,
                source_rate,
                self._target_rate,
                quality='HQ'  # High Quality mode
            )

            # Convert back to int16
            resampled_array = (resampled_float * 32768.0).astype(np.int16)

        elif self._resampler == "scipy":
            # Use scipy (slower but still good)
            import scipy.signal

            # Calculate number of output samples
            num_samples = int(len(audio_array) * self._target_rate / source_rate)

            # Resample using polyphase filtering
            resampled_array = scipy.signal.resample(audio_array, num_samples)

            # Convert to int16
            resampled_array = resampled_array.astype(np.int16)

        else:
            raise ValueError(f"Unknown resampler: {self._resampler}")

        # Convert back to bytes
        return resampled_array.tobytes()

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        """
        Core streaming logic.

        Receives high-sample-rate chunks from base TTS, resamples each chunk
        to 8kHz, and emits to LiveKit.
        """
        from livekit.agents import utils

        try:
            # Initialize the output emitter
            output_emitter.initialize(
                request_id=utils.shortuuid(),
                sample_rate=self._target_rate,  # 8000 Hz
                num_channels=1,
                mime_type="audio/pcm",
            )

            # Process audio chunks from base TTS
            audio_received = False
            total_input_bytes = 0
            total_output_bytes = 0

            async for event in self._base_stream:
                if isinstance(event, tts.SynthesizedAudio):
                    frame = event.frame

                    # Store source rate from first frame
                    if self._source_rate is None:
                        self._source_rate = frame.sample_rate
                        logger.info(
                            f"Resampling: {self._source_rate}Hz → {self._target_rate}Hz "
                            f"(ratio: {self._source_rate/self._target_rate:.2f}:1)"
                        )

                    # Resample this chunk
                    input_data = bytes(frame.data)
                    resampled_data = self._resample_audio(input_data, self._source_rate)

                    total_input_bytes += len(input_data)
                    total_output_bytes += len(resampled_data)
                    audio_received = True

                    # Push resampled audio to LiveKit
                    output_emitter.push(resampled_data)

                    logger.debug(
                        f"Resampled chunk: {len(input_data)} → {len(resampled_data)} bytes"
                    )

            if not audio_received:
                logger.warning("No audio received from base TTS")
                raise Exception("No audio output from base TTS")

            # Flush to finalize
            output_emitter.flush()

            logger.info(
                f"Resampling complete: {total_input_bytes} → {total_output_bytes} bytes "
                f"({total_output_bytes/total_input_bytes*100:.1f}% of original)"
            )

        except Exception as e:
            logger.error(f"Resampling error: {e}")
            raise


# Helper function for easy integration
def create_telephony_tts(voice: str = "alloy", model: str = "tts-1", speed: float = 1.3) -> TelephonyOptimizedTTS:
    """
    Convenience function to create a telephony-optimized OpenAI TTS.

    Args:
        voice: OpenAI voice (alloy, echo, fable, onyx, nova, shimmer)
        model: TTS model (tts-1 or tts-1-hd)
        speed: Speech speed (0.25-4.0, default 1.3 for natural conversation flow)

    Returns:
        TelephonyOptimizedTTS instance ready for SIP calls

    Example:
        tts = create_telephony_tts(voice="alloy", model="tts-1", speed=1.3)
        session = AgentSession(tts=tts, ...)
    """
    from livekit.plugins import openai

    base_tts = openai.TTS(voice=voice, model=model, speed=speed)
    return TelephonyOptimizedTTS(base_tts, target_sample_rate=8000)
