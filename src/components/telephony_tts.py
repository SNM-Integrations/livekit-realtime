"""
Telephony-Optimized TTS Wrapper with Stateful Resampling
==========================================================

Pre-resamples TTS output to 8kHz before sending to LiveKit/SIP using stateful
resampling to eliminate real-time artifacts and chunk boundary glitches.

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
- Use STATEFUL resampling (soxr.ResampleStream) for smooth chunk boundaries
- Maintains filter state across chunks - no discontinuities or artifacts
- LiveKit receives 8kHz frames directly
- No real-time processing = no glitching
- ~10ms overhead per chunk (negligible)

Technical Details:
- Uses soxr.ResampleStream with VHQ (Very High Quality) mode
- Each chunk "remembers" previous chunk state for seamless transitions
- Proper flushing at end ensures no samples lost
- Fallback to scipy for systems without soxr (with quality warning)

Usage:
    from components.telephony_tts import TelephonyOptimizedTTS
    from livekit.plugins import openai

    base_tts = openai.TTS(voice="alloy", model="gpt-4o-mini-tts")
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
        buffer_complete_sentence: bool = False,
    ):
        """
        Args:
            base_tts: The underlying TTS engine (OpenAI, ElevenLabs, etc.)
            target_sample_rate: Target sample rate for telephony (default 8000 Hz)
            use_soxr: Use soxr for resampling if available (fastest, highest quality)
            buffer_complete_sentence: If True, buffers complete TTS response before playing
                                     for natural prosody. Adds ~300-500ms latency but eliminates
                                     patchy, disconnected audio quality.
        """
        super().__init__(
            capabilities=base_tts.capabilities,
            sample_rate=target_sample_rate,  # Output 8kHz
            num_channels=1,
        )

        self._base_tts = base_tts
        self._target_rate = target_sample_rate
        self._use_soxr = use_soxr
        self._buffer_complete = buffer_complete_sentence

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
            f"target_rate={target_sample_rate}Hz, resampler={self._resampler} (stateful)"
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
            buffer_complete=self._buffer_complete,
        )


class TelephonyResampledStream(tts.ChunkedStream):
    """
    Wraps a TTS ChunkedStream and resamples audio chunks to 8kHz using stateful resampling.

    Uses soxr.ResampleStream for streaming-compatible resampling with state preservation
    across chunk boundaries, eliminating audio glitches and pops.

    Key features:
    - Stateful resampling: Each chunk "remembers" the previous chunk's state
    - Smooth transitions: No discontinuities at chunk boundaries
    - Minimal latency: ~10ms overhead per chunk, maintains streaming
    """

    def __init__(
        self,
        base_stream: tts.ChunkedStream,
        target_rate: int,
        resampler: str,
        tts_instance: tts.TTS,
        buffer_complete: bool = False,
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
        self._buffer_complete = buffer_complete

        # Stateful resampler (initialized on first chunk when source_rate is known)
        self._resampler_stream = None

    def _initialize_resampler_stream(self, source_rate: int) -> None:
        """
        Initialize the stateful ResampleStream on first chunk.

        This is called when we receive the first audio chunk and know the source sample rate.
        The ResampleStream maintains internal state across all subsequent chunks, ensuring
        smooth transitions at chunk boundaries.

        Args:
            source_rate: Source sample rate from TTS (e.g., 24000 Hz)
        """
        if self._resampler == "soxr":
            import soxr

            # Create stateful ResampleStream
            # dtype='float32' because soxr expects normalized float audio
            # channels=1 for mono audio
            self._resampler_stream = soxr.ResampleStream(
                in_rate=source_rate,
                out_rate=self._target_rate,
                num_channels=1,
                dtype='float32',
                quality='VHQ'  # Very High Quality - best for telephony
            )
            logger.info(
                f"Initialized stateful ResampleStream: {source_rate}Hz → {self._target_rate}Hz "
                f"(ratio: {source_rate/self._target_rate:.2f}:1, quality: VHQ)"
            )
        else:
            # scipy doesn't support stateful streaming, log warning
            logger.warning(
                "scipy resampling doesn't support stateful streaming. "
                "Consider installing soxr for better quality: pip install soxr"
            )

    def _resample_audio_chunk(self, audio_data: bytes, source_rate: int, is_last: bool = False) -> bytes:
        """
        Resample a single audio chunk using stateful resampling.

        This method maintains state across chunks, ensuring smooth transitions without
        discontinuities or artifacts at chunk boundaries.

        Args:
            audio_data: Raw PCM16 audio bytes
            source_rate: Source sample rate (e.g., 24000 Hz)
            is_last: True if this is the final chunk (flushes remaining samples)

        Returns:
            Resampled PCM16 audio bytes at target_rate
        """
        # Handle empty chunks
        if len(audio_data) == 0 and not is_last:
            return b''

        # Convert bytes to numpy array (int16)
        audio_array = np.frombuffer(audio_data, dtype=np.int16) if len(audio_data) > 0 else np.array([], dtype=np.int16)

        # If already at target rate, no resampling needed
        if source_rate == self._target_rate:
            return audio_data

        # Initialize resampler stream on first chunk
        if self._resampler_stream is None and self._resampler == "soxr":
            self._initialize_resampler_stream(source_rate)

        # Resample based on available library
        if self._resampler == "soxr" and self._resampler_stream is not None:
            # STATEFUL RESAMPLING using ResampleStream
            import soxr

            # Convert int16 to float32 normalized to [-1, 1]
            audio_float = audio_array.astype(np.float32) / 32768.0

            # Resample chunk with state preservation
            # is_last=True flushes remaining samples from internal buffer
            resampled_float = self._resampler_stream.resample_chunk(
                audio_float,
                last=is_last
            )

            # Convert back to int16, clipping to prevent overflow
            resampled_array = np.clip(resampled_float * 32768.0, -32768, 32767).astype(np.int16)

        elif self._resampler == "scipy" or (self._resampler == "soxr" and self._resampler_stream is None):
            # Fallback to non-stateful scipy resampling
            # Note: This may have artifacts at chunk boundaries
            import scipy.signal

            if len(audio_array) == 0:
                return b''

            # Calculate number of output samples
            num_samples = int(len(audio_array) * self._target_rate / source_rate)

            # Resample using polyphase filtering
            resampled_float = scipy.signal.resample(audio_array, num_samples)

            # Convert to int16
            resampled_array = np.clip(resampled_float, -32768, 32767).astype(np.int16)

        else:
            raise ValueError(f"Unknown resampler: {self._resampler}")

        # Convert back to bytes
        return resampled_array.tobytes()

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        """
        Core streaming/buffering logic with resampling.

        Two modes:
        1. Streaming (buffer_complete=False): Resamples and emits chunks immediately
           - Uses stateful resampling for smooth chunk boundaries
           - Low latency (~10ms overhead)
           - May have prosody discontinuities between chunks

        2. Buffered (buffer_complete=True): Collects complete synthesis, then emits
           - Buffers all chunks, resamples complete audio, then plays
           - Natural prosody across full sentence (no pitch/tone jumps)
           - Adds ~300-500ms latency
           - Eliminates "patchy" disconnected audio quality
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

            if self._buffer_complete:
                # BUFFERED MODE: Collect all chunks, resample once, emit complete audio
                await self._run_buffered(output_emitter, utils)
            else:
                # STREAMING MODE: Resample and emit chunks immediately (original behavior)
                await self._run_streaming(output_emitter, utils)

        except Exception as e:
            logger.error(f"Resampling error: {e}", exc_info=True)
            raise

    async def _run_streaming(self, output_emitter: tts.AudioEmitter, utils) -> None:
        """Streaming mode: Resample and emit chunks immediately (stateful resampling)"""
        # Process audio chunks from base TTS
        audio_received = False
        total_input_bytes = 0
        total_output_bytes = 0
        chunk_count = 0

        # Process all chunks from base TTS
        async for event in self._base_stream:
            if isinstance(event, tts.SynthesizedAudio):
                frame = event.frame

                # Store source rate from first frame
                if self._source_rate is None:
                    self._source_rate = frame.sample_rate
                    logger.info(
                        f"Starting stateful resampling (streaming): {self._source_rate}Hz → {self._target_rate}Hz "
                        f"(ratio: {self._source_rate/self._target_rate:.2f}:1)"
                    )

                # Resample this chunk (is_last=False - more chunks may follow)
                input_data = bytes(frame.data)
                resampled_data = self._resample_audio_chunk(
                    input_data,
                    self._source_rate,
                    is_last=False  # Not the last chunk yet
                )

                total_input_bytes += len(input_data)
                total_output_bytes += len(resampled_data)
                chunk_count += 1
                audio_received = True

                # Push resampled audio to LiveKit immediately (streaming)
                if len(resampled_data) > 0:
                    output_emitter.push(resampled_data)

                logger.debug(
                    f"Chunk {chunk_count}: {len(input_data)} → {len(resampled_data)} bytes"
                )

        # After all chunks processed, flush remaining samples from resampler
        if audio_received and self._resampler_stream is not None:
            logger.debug("Flushing resampler buffer...")

            # Call resample_chunk with empty array and last=True to flush
            final_chunk = self._resample_audio_chunk(
                b'',
                self._source_rate,
                is_last=True  # Flush remaining samples
            )

            if len(final_chunk) > 0:
                total_output_bytes += len(final_chunk)
                output_emitter.push(final_chunk)
                logger.debug(f"Flushed {len(final_chunk)} bytes from resampler buffer")

        if not audio_received:
            logger.warning("No audio received from base TTS")
            raise Exception("No audio output from base TTS")

        # Finalize output
        output_emitter.flush()

        # Calculate compression ratio
        compression_ratio = (total_output_bytes / total_input_bytes * 100) if total_input_bytes > 0 else 0

        logger.info(
            f"Stateful resampling complete (streaming): {total_input_bytes} → {total_output_bytes} bytes "
            f"({compression_ratio:.1f}% of original), {chunk_count} chunks processed"
        )

    async def _run_buffered(self, output_emitter: tts.AudioEmitter, utils) -> None:
        """Buffered mode: Collect all chunks, resample once, emit complete audio for natural prosody"""
        import time

        buffer_start = time.time()
        audio_chunks = []
        chunk_count = 0

        # Collect ALL chunks from TTS before playing anything
        async for event in self._base_stream:
            if isinstance(event, tts.SynthesizedAudio):
                frame = event.frame

                # Store source rate from first frame
                if self._source_rate is None:
                    self._source_rate = frame.sample_rate
                    logger.info(
                        f"Starting buffered resampling: {self._source_rate}Hz → {self._target_rate}Hz "
                        f"(ratio: {self._source_rate/self._target_rate:.2f}:1)"
                    )

                # Collect chunk (don't emit yet)
                audio_chunks.append(bytes(frame.data))
                chunk_count += 1

        if len(audio_chunks) == 0:
            logger.warning("No audio received from base TTS")
            raise Exception("No audio output from base TTS")

        # Concatenate all chunks into complete audio
        complete_audio = b''.join(audio_chunks)
        total_input_bytes = len(complete_audio)

        buffer_time = (time.time() - buffer_start) * 1000  # ms
        logger.info(
            f"Buffered {chunk_count} chunks ({total_input_bytes} bytes) in {buffer_time:.0f}ms"
        )

        # Resample the COMPLETE audio (single operation, perfect prosody)
        resampled_audio = self._resample_audio_chunk(
            complete_audio,
            self._source_rate,
            is_last=True  # Complete audio, flush everything
        )

        total_output_bytes = len(resampled_audio)
        compression_ratio = (total_output_bytes / total_input_bytes * 100) if total_input_bytes > 0 else 0

        # Emit the complete resampled audio
        output_emitter.push(resampled_audio)
        output_emitter.flush()

        logger.info(
            f"Buffered resampling complete: {total_input_bytes} → {total_output_bytes} bytes "
            f"({compression_ratio:.1f}% of original), {chunk_count} chunks buffered, "
            f"latency: {buffer_time:.0f}ms"
        )


# Helper function for easy integration
def create_telephony_tts(
    voice: str = "alloy",
    model: str = "gpt-4o-mini-tts",
    speed: float = 1.0,
    instructions: str = None,
    buffer_complete_sentence: bool = False
) -> TelephonyOptimizedTTS:
    """
    Convenience function to create a telephony-optimized OpenAI TTS.

    Args:
        voice: OpenAI voice for gpt-4o-mini-tts (alloy, ash, ballad, coral, echo,
               fable, onyx, nova, sage, shimmer, verse)
               NOTE: marin/cedar are ONLY available in Realtime API, not gpt-4o-mini-tts
        model: TTS model (gpt-4o-mini-tts, tts-1, or tts-1-hd)
        speed: Audio playback speed (0.25-4.0, default 1.0 for natural playback)
               NOTE: This speeds up audio playback, not speaking style. Use instructions for pacing.
        instructions: Voice style instructions (only works with gpt-4o-mini-tts)
                     Controls tone, emotion, PACING, accent via natural language
                     Example: "Speak with natural, flowing pace - not rushed but efficient"
        buffer_complete_sentence: If True, buffers complete TTS synthesis before playing
                                 for natural prosody (eliminates "patchy" audio).
                                 Adds ~300-500ms latency but much better quality.

    Returns:
        TelephonyOptimizedTTS instance ready for SIP calls

    Example:
        tts = create_telephony_tts(
            voice="shimmer",
            model="gpt-4o-mini-tts",
            speed=1.0,
            instructions="Speak like a friendly, professional Swedish assistant",
            buffer_complete_sentence=True  # For natural, non-patchy audio
        )
        session = AgentSession(tts=tts, ...)
    """
    from livekit.plugins import openai

    # Build TTS parameters
    tts_params = {
        "voice": voice,
        "model": model,
        "speed": speed
    }

    # Only add instructions if provided and using gpt-4o-mini-tts
    # (instructions don't work with tts-1/tts-1-hd)
    if instructions and model == "gpt-4o-mini-tts":
        tts_params["instructions"] = instructions

    base_tts = openai.TTS(**tts_params)
    return TelephonyOptimizedTTS(
        base_tts,
        target_sample_rate=8000,
        buffer_complete_sentence=buffer_complete_sentence
    )
