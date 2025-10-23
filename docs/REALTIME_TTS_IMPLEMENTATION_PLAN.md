# OpenAI Realtime API TTS-Only Component Implementation Plan

## Executive Summary

This document provides a comprehensive technical implementation plan for building a custom TTS (Text-to-Speech) component using OpenAI's Realtime API in TTS-only mode (text input → audio output with Marin/Cedar voice) that integrates with LiveKit AgentSession for SIP phone calls.

**Key Finding**: The OpenAI Realtime API is primarily designed for speech-to-speech conversations and does NOT have a dedicated "TTS-only mode". However, we can simulate TTS-only behavior by:
1. Using `conversation.item.create` events to inject text messages
2. Configuring session with text input modalities
3. Extracting audio output from `response.audio.delta` events

**Critical Consideration**: Based on research, there are known issues with manually creating conversation items and audio output reliability. This approach may require workarounds and extensive testing.

---

## Section 1: Technical Specifications

### 1.1 OpenAI Realtime API Configuration

#### WebSocket Connection
- **Endpoint**: `wss://api.openai.com/v1/realtime`
- **Model Parameter**: `?model=gpt-4o-realtime-preview-2024-10-01` or `gpt-realtime`
- **Authentication**:
  - Header: `Authorization: Bearer YOUR_API_KEY`
  - Header: `OpenAI-Beta: realtime=v1`

#### Session Configuration for TTS-Only

```json
{
  "event_id": "event_001",
  "type": "session.update",
  "session": {
    "modalities": ["text", "audio"],
    "voice": "marin",
    "input_audio_format": "pcm16",
    "output_audio_format": "pcm16",
    "input_audio_transcription": null,
    "turn_detection": null,
    "temperature": 0.9,
    "max_output_tokens": null
  }
}
```

**Key Configuration Points**:
- `modalities`: Set to `["text", "audio"]` to accept text input and produce audio output
- `voice`: Options include `"marin"`, `"cedar"`, `"alloy"`, `"ash"`, `"ballad"`, `"coral"`, `"echo"`, `"sage"`, `"shimmer"`, `"verse"`
  - **Recommended**: `"marin"` or `"cedar"` (newest, highest quality voices exclusive to Realtime API)
- `turn_detection`: Set to `null` to disable server-side VAD (we control when responses are generated)
- `output_audio_format`: `"pcm16"` for 24kHz PCM16 audio output

#### Audio Format Specifications

**OpenAI Realtime API Output**:
- **Format**: PCM16 (16-bit signed integer PCM)
- **Sample Rate**: 24,000 Hz (24kHz)
- **Channels**: 1 (mono)
- **Byte Order**: Little-endian
- **Encoding**: Base64-encoded in WebSocket messages

**Bitrate**: 384 kilobits/second (uncompressed 16-bit @ 24kHz)

**Alternative Format**: G.711 (µ-law/a-law) is supported for telephony but lower quality

### 1.2 Sending Text for TTS Conversion

#### Method 1: Using `conversation.item.create` (Manual Text Injection)

```json
{
  "type": "conversation.item.create",
  "item": {
    "type": "message",
    "role": "assistant",
    "content": [
      {
        "type": "input_text",
        "text": "Hello, this is the text to convert to speech."
      }
    ]
  }
}
```

Then trigger response generation:

```json
{
  "type": "response.create",
  "response": {
    "modalities": ["audio"],
    "instructions": "Convert the text to speech using the configured voice."
  }
}
```

**Known Issue**: Community reports indicate that manually creating conversation items may cause the API to stop generating audio responses and only respond with text. This requires testing and potential workarounds.

#### Method 2: Using `response.create` with Instructions (Recommended)

```json
{
  "type": "response.create",
  "response": {
    "modalities": ["audio"],
    "instructions": "Say this exact text: 'Hello, this is the text to convert to speech.'"
  }
}
```

This approach is more reliable as it lets the model generate the response naturally.

### 1.3 Receiving Audio Output

#### Event Flow

1. **response.audio_transcript.delta** - Streaming text of what's being spoken
2. **response.audio.delta** - Base64-encoded PCM16 audio chunks
3. **response.audio.done** - Signals audio generation is complete

#### Audio Delta Event Structure

```json
{
  "type": "response.audio.delta",
  "delta": "<base64-encoded-pcm16-audio>",
  "item_id": "item_123",
  "output_index": 0,
  "content_index": 0
}
```

#### Decoding Audio

```python
import base64

# Receive event from WebSocket
audio_data = base64.b64decode(event["delta"])
# audio_data is now raw PCM16 bytes (24kHz, mono, int16)
```

---

## Section 2: LiveKit Integration Requirements

### 2.1 TTS Interface Requirements

Based on research of `livekit.agents.tts.TTS` base class:

```python
from livekit.agents import tts
from typing import Optional
from livekit.agents.types import APIConnectOptions

class RealtimeTTS(tts.TTS):
    def __init__(self, voice: str = "marin", api_key: str = None):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,  # Realtime API outputs 24kHz
            num_channels=1      # Mono audio
        )
        self._voice = voice
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")

    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[APIConnectOptions] = None
    ) -> tts.ChunkedStream:
        """
        Required method: Returns ChunkedStream for non-streaming synthesis
        """
        return RealtimeChunkedStream(
            tts=self,
            text=text,
            voice=self._voice,
            api_key=self._api_key,
            conn_options=conn_options
        )
```

### 2.2 ChunkedStream Implementation

```python
from livekit.agents import tts, utils
from livekit import rtc
import asyncio

class RealtimeChunkedStream(tts.ChunkedStream):
    def __init__(self, tts, text, voice, api_key, conn_options):
        super().__init__(tts, text, conn_options)
        self._text = text
        self._voice = voice
        self._api_key = api_key

    async def _run(self, output_emitter: tts.SynthesizedAudioEmitter):
        """
        Core synthesis logic - must be implemented
        """
        # 1. Initialize output emitter
        request_id = utils.shortuuid()
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=24000,
            num_channels=1,
            mime_type="audio/pcm"
        )

        # 2. Connect to Realtime API WebSocket
        # 3. Send session.update with voice configuration
        # 4. Send text for TTS conversion
        # 5. Receive audio.delta events
        # 6. Decode base64 and create AudioFrames
        # 7. Emit SynthesizedAudio events

        # (Detailed implementation in Section 4)
```

### 2.3 Audio Format Conversion

**Challenge**: OpenAI Realtime outputs 24kHz PCM16, but SIP expects 8kHz µ-law.

**Solution**: LiveKit automatically handles resampling for SIP!

According to documentation:
- LiveKit accepts audio at various sample rates (including 24kHz)
- Automatically resamples for SIP telephony (8kHz µ-law/a-law)
- No manual conversion needed in custom TTS plugin

**What we need to provide**:
```python
rtc.AudioFrame(
    data=pcm16_bytes,      # Raw PCM16 from Realtime API
    sample_rate=24000,      # Source sample rate
    num_channels=1,         # Mono
    samples_per_channel=len(pcm16_bytes) // 2  # int16 = 2 bytes per sample
)
```

LiveKit handles the rest automatically when publishing to SIP participant.

### 2.4 Integration with AgentSession

**Current Implementation** (from `src/agent.py`):
```python
session = AgentSession(
    vad=silero.VAD.load(),
    stt=deepgram.STT(...),
    llm=openai.LLM(...),
    tts=openai.TTS(voice=voice_name)  # <-- Replace this
)
```

**New Implementation**:
```python
from custom_tts.realtime_tts import RealtimeTTS

session = AgentSession(
    vad=silero.VAD.load(),
    stt=deepgram.STT(...),
    llm=openai.LLM(...),
    tts=RealtimeTTS(voice=voice_name)  # <-- Custom TTS
)
```

---

## Section 3: Implementation Steps (Detailed)

### Step 1: Create Custom TTS Class Structure

**File**: `src/custom_tts/realtime_tts.py`

```python
import os
import logging
from typing import Optional
from livekit.agents import tts
from livekit.agents.types import APIConnectOptions

logger = logging.getLogger("realtime-tts")

class RealtimeTTS(tts.TTS):
    """
    Custom TTS using OpenAI Realtime API for text-to-speech conversion.
    Outputs high-quality speech using Marin/Cedar voices.
    """

    def __init__(
        self,
        *,
        voice: str = "marin",
        api_key: str | None = None,
        model: str = "gpt-4o-realtime-preview-2024-10-01",
        temperature: float = 0.9,
    ):
        # Realtime API outputs 24kHz mono PCM16
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )

        self._voice = voice
        self._model = model
        self._temperature = temperature
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")

        if not self._api_key:
            raise ValueError("OpenAI API key required")

        logger.info(f"RealtimeTTS initialized with voice={voice}, model={model}")

    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[APIConnectOptions] = None
    ) -> tts.ChunkedStream:
        return RealtimeChunkedStream(
            tts=self,
            input_text=text,
            voice=self._voice,
            model=self._model,
            temperature=self._temperature,
            api_key=self._api_key,
            conn_options=conn_options,
        )
```

### Step 2: WebSocket Connection Management

```python
import asyncio
import websockets
import json

class RealtimeChunkedStream(tts.ChunkedStream):
    def __init__(self, tts, input_text, voice, model, temperature, api_key, conn_options):
        super().__init__(tts, input_text, conn_options)
        self._input_text = input_text
        self._voice = voice
        self._model = model
        self._temperature = temperature
        self._api_key = api_key

    async def _connect_websocket(self):
        """Establish WebSocket connection to Realtime API"""
        url = f"wss://api.openai.com/v1/realtime?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1"
        }

        logger.info(f"Connecting to Realtime API: {url}")

        try:
            ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
            )
            logger.info("WebSocket connected successfully")
            return ws
        except Exception as e:
            logger.error(f"Failed to connect to Realtime API: {e}")
            raise
```

### Step 3: Session Initialization

```python
async def _initialize_session(self, ws):
    """Configure Realtime API session for TTS-only mode"""
    session_config = {
        "type": "session.update",
        "session": {
            "modalities": ["text", "audio"],
            "voice": self._voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": None,  # Disable server-side VAD
            "temperature": self._temperature,
            "max_output_tokens": None,
        }
    }

    await ws.send(json.dumps(session_config))
    logger.info(f"Session configured: voice={self._voice}, temperature={self._temperature}")

    # Wait for session.updated confirmation
    response = await ws.recv()
    event = json.loads(response)
    if event.get("type") == "session.updated":
        logger.info("Session update confirmed")
    else:
        logger.warning(f"Unexpected response to session.update: {event.get('type')}")
```

### Step 4: Text-to-Audio Conversion Flow

```python
async def _request_tts(self, ws, text: str):
    """Request TTS conversion using response.create"""
    request = {
        "type": "response.create",
        "response": {
            "modalities": ["audio"],
            "instructions": f"Say this exact text: '{text}'"
        }
    }

    await ws.send(json.dumps(request))
    logger.info(f"TTS requested for text: {text[:50]}...")
```

### Step 5: Audio Streaming/Buffering

```python
import base64
from livekit import rtc
from livekit.agents import utils

async def _run(self, output_emitter: tts.SynthesizedAudioEmitter):
    """Core synthesis implementation"""

    # Initialize output emitter
    request_id = utils.shortuuid()
    segment_id = utils.shortuuid()

    output_emitter.initialize(
        request_id=request_id,
        sample_rate=24000,
        num_channels=1,
        mime_type="audio/pcm"
    )
    output_emitter.start_segment(segment_id=segment_id)

    ws = None
    audio_buffer = []

    try:
        # Connect to Realtime API
        ws = await self._connect_websocket()

        # Initialize session
        await self._initialize_session(ws)

        # Request TTS
        await self._request_tts(ws, self._input_text)

        # Receive and process audio chunks
        async for message in ws:
            event = json.loads(message)
            event_type = event.get("type")

            if event_type == "response.audio.delta":
                # Decode base64 audio chunk
                base64_audio = event.get("delta", "")
                audio_bytes = base64.b64decode(base64_audio)
                audio_buffer.append(audio_bytes)

                # Create AudioFrame
                samples_per_channel = len(audio_bytes) // 2  # int16 = 2 bytes
                frame = rtc.AudioFrame(
                    data=audio_bytes,
                    sample_rate=24000,
                    num_channels=1,
                    samples_per_channel=samples_per_channel
                )

                # Emit audio
                output_emitter.push_audio(frame)

            elif event_type == "response.audio.done":
                logger.info("Audio generation complete")
                break

            elif event_type == "error":
                error_msg = event.get("error", {})
                logger.error(f"Realtime API error: {error_msg}")
                raise Exception(f"Realtime API error: {error_msg}")

        # Mark as final
        output_emitter.mark_audio_segment_end()

    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        raise
    finally:
        if ws:
            await ws.close()
            logger.info("WebSocket closed")
```

### Step 6: Error Handling

```python
async def _run(self, output_emitter: tts.SynthesizedAudioEmitter):
    retry_count = 0
    max_retries = 3

    while retry_count < max_retries:
        try:
            # ... synthesis logic ...
            return  # Success

        except websockets.exceptions.WebSocketException as e:
            retry_count += 1
            logger.warning(f"WebSocket error (attempt {retry_count}/{max_retries}): {e}")
            if retry_count >= max_retries:
                raise
            await asyncio.sleep(1 * retry_count)  # Exponential backoff

        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            raise
```

### Step 7: Integration with Existing `agent.py`

**Modify** `src/agent.py`:

```python
# Add import at top
from custom_tts.realtime_tts import RealtimeTTS

# In entrypoint() function, replace TTS configuration:
session = AgentSession(
    vad=silero.VAD.load(),
    stt=deepgram.STT(
        model="nova-3",
        language=deepgram_language,
        smart_format=True,
        interim_results=False,
    ),
    llm=openai.LLM(
        model=model_config.get("primary_model", "gpt-4o-mini"),
        temperature=model_config.get("temperature", 0.9),
    ),
    tts=RealtimeTTS(  # <-- NEW: Custom Realtime TTS
        voice=voice_name,
        temperature=config.get("temperature", 0.9),
    ),
)
```

---

## Section 4: Code Examples

### Complete Custom TTS Implementation

**File**: `src/custom_tts/__init__.py`
```python
from .realtime_tts import RealtimeTTS

__all__ = ["RealtimeTTS"]
```

**File**: `src/custom_tts/realtime_tts.py`

```python
"""
OpenAI Realtime API TTS-only implementation for LiveKit Agents.
Provides high-quality text-to-speech using Marin/Cedar voices.
"""

import os
import json
import base64
import asyncio
import logging
from typing import Optional
import websockets

from livekit.agents import tts, utils
from livekit.agents.types import APIConnectOptions
from livekit import rtc

logger = logging.getLogger("realtime-tts")


class RealtimeTTS(tts.TTS):
    """
    Custom TTS using OpenAI Realtime API.

    Features:
    - High-quality Marin/Cedar voices (exclusive to Realtime API)
    - 24kHz PCM16 audio output
    - Automatic integration with LiveKit's audio pipeline
    - SIP-compatible (LiveKit handles resampling to 8kHz)

    Usage:
        tts = RealtimeTTS(voice="marin")
        session = AgentSession(tts=tts, ...)
    """

    def __init__(
        self,
        *,
        voice: str = "marin",
        api_key: str | None = None,
        model: str = "gpt-4o-realtime-preview-2024-10-01",
        temperature: float = 0.9,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,  # Realtime API outputs 24kHz
            num_channels=1,     # Mono
        )

        self._voice = voice
        self._model = model
        self._temperature = temperature
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")

        if not self._api_key:
            raise ValueError("OpenAI API key required (OPENAI_API_KEY env var or api_key parameter)")

        logger.info(f"RealtimeTTS initialized: voice={voice}, model={model}, temp={temperature}")

    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[APIConnectOptions] = None
    ) -> tts.ChunkedStream:
        """Synthesize text to speech using Realtime API"""
        return RealtimeChunkedStream(
            tts=self,
            input_text=text,
            voice=self._voice,
            model=self._model,
            temperature=self._temperature,
            api_key=self._api_key,
            conn_options=conn_options,
        )


class RealtimeChunkedStream(tts.ChunkedStream):
    """
    ChunkedStream implementation for Realtime API TTS.
    Handles WebSocket connection, session management, and audio streaming.
    """

    def __init__(self, tts, input_text, voice, model, temperature, api_key, conn_options):
        super().__init__(tts, input_text, conn_options)
        self._input_text = input_text
        self._voice = voice
        self._model = model
        self._temperature = temperature
        self._api_key = api_key

    async def _connect_websocket(self):
        """Establish WebSocket connection to Realtime API"""
        url = f"wss://api.openai.com/v1/realtime?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1"
        }

        logger.debug(f"Connecting to Realtime API WebSocket")

        try:
            ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
            logger.debug("WebSocket connected")
            return ws
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            raise

    async def _initialize_session(self, ws):
        """Configure session for TTS-only mode"""
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": self._voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": None,  # Disable server VAD
                "temperature": self._temperature,
            }
        }

        await ws.send(json.dumps(session_config))
        logger.debug(f"Session update sent: voice={self._voice}")

        # Wait for confirmation (with timeout)
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            event = json.loads(response)

            if event.get("type") == "session.created":
                # First message is session.created, wait for session.updated
                response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                event = json.loads(response)

            if event.get("type") != "session.updated":
                logger.warning(f"Unexpected session response: {event.get('type')}")
        except asyncio.TimeoutError:
            logger.warning("Session update confirmation timeout")

    async def _request_tts(self, ws, text: str):
        """Request TTS conversion"""
        # Use response.create with instructions for most reliable TTS
        request = {
            "type": "response.create",
            "response": {
                "modalities": ["audio"],
                "instructions": f"Say this exact text: '{text}'"
            }
        }

        await ws.send(json.dumps(request))
        logger.debug(f"TTS request sent: {text[:50]}...")

    async def _run(self, output_emitter: tts.SynthesizedAudioEmitter):
        """
        Core synthesis logic.
        Connects to Realtime API, sends text, receives audio chunks.
        """

        # Initialize output emitter with audio format
        request_id = utils.shortuuid()
        segment_id = utils.shortuuid()

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=24000,
            num_channels=1,
            mime_type="audio/pcm"
        )
        output_emitter.start_segment(segment_id=segment_id)

        ws = None
        retry_count = 0
        max_retries = 2

        while retry_count <= max_retries:
            try:
                # Connect to WebSocket
                ws = await self._connect_websocket()

                # Initialize session
                await self._initialize_session(ws)

                # Request TTS
                await self._request_tts(ws, self._input_text)

                # Process audio stream
                audio_received = False

                async for message in ws:
                    event = json.loads(message)
                    event_type = event.get("type")

                    if event_type == "response.audio.delta":
                        # Decode base64 audio chunk
                        base64_audio = event.get("delta", "")
                        if not base64_audio:
                            continue

                        audio_bytes = base64.b64decode(base64_audio)
                        audio_received = True

                        # Create AudioFrame (24kHz mono PCM16)
                        samples_per_channel = len(audio_bytes) // 2  # int16 = 2 bytes
                        frame = rtc.AudioFrame(
                            data=audio_bytes,
                            sample_rate=24000,
                            num_channels=1,
                            samples_per_channel=samples_per_channel
                        )

                        # Emit to LiveKit pipeline
                        output_emitter.push_audio(frame)

                    elif event_type == "response.audio.done":
                        logger.debug("Audio synthesis complete")
                        break

                    elif event_type == "response.done":
                        logger.debug("Response complete")
                        break

                    elif event_type == "error":
                        error = event.get("error", {})
                        error_msg = error.get("message", "Unknown error")
                        logger.error(f"Realtime API error: {error_msg}")
                        raise Exception(f"Realtime API error: {error_msg}")

                # Check if we received audio
                if not audio_received:
                    logger.warning("No audio received from Realtime API")
                    raise Exception("No audio output received")

                # Mark segment as complete
                output_emitter.mark_audio_segment_end()

                logger.info(f"TTS synthesis successful: {len(self._input_text)} chars")
                return  # Success - exit retry loop

            except Exception as e:
                retry_count += 1
                logger.error(f"TTS synthesis error (attempt {retry_count}/{max_retries + 1}): {e}")

                if retry_count > max_retries:
                    logger.error("Max retries exceeded, TTS synthesis failed")
                    raise

                # Exponential backoff
                await asyncio.sleep(0.5 * retry_count)

            finally:
                if ws:
                    await ws.close()
                    logger.debug("WebSocket closed")
```

### Integration Example

**File**: `src/agent.py` (modified sections)

```python
# Add import
from custom_tts.realtime_tts import RealtimeTTS

# In entrypoint() function:
async def entrypoint(ctx: JobContext):
    # ... existing code ...

    # Get configuration
    voice_name = config.get("voice", "marin")  # Use marin by default

    logger.info(f"🎯 Custom Pipeline with Realtime TTS")
    logger.info(f"   STT: Deepgram Nova-3")
    logger.info(f"   LLM: GPT-4o-mini")
    logger.info(f"   TTS: OpenAI Realtime API (voice: {voice_name})")

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(
            model="nova-3",
            language=deepgram_language,
            smart_format=True,
            interim_results=False,
        ),
        llm=openai.LLM(
            model=model_config.get("primary_model", "gpt-4o-mini"),
            temperature=model_config.get("temperature", 0.9),
        ),
        tts=RealtimeTTS(  # <-- CHANGED: Use Realtime TTS
            voice=voice_name,
            temperature=config.get("temperature", 0.9),
        ),
    )

    # ... rest of code unchanged ...
```

---

## Section 5: Testing Strategy

### 5.1 Unit Testing (Isolation)

**Test File**: `tests/test_realtime_tts.py`

```python
import pytest
import asyncio
from custom_tts.realtime_tts import RealtimeTTS

@pytest.mark.asyncio
async def test_tts_initialization():
    """Test TTS class initialization"""
    tts = RealtimeTTS(voice="marin")
    assert tts._voice == "marin"
    assert tts.sample_rate == 24000
    assert tts.num_channels == 1

@pytest.mark.asyncio
async def test_synthesize_simple_text():
    """Test basic text synthesis"""
    tts = RealtimeTTS(voice="marin")
    stream = tts.synthesize("Hello world")

    # Collect audio frames
    frames = []
    async for event in stream:
        frames.append(event.frame)

    assert len(frames) > 0
    assert frames[0].sample_rate == 24000

@pytest.mark.asyncio
async def test_websocket_connection():
    """Test WebSocket connection establishment"""
    from custom_tts.realtime_tts import RealtimeChunkedStream

    # Mock stream
    stream = RealtimeChunkedStream(
        tts=None,
        input_text="test",
        voice="marin",
        model="gpt-4o-realtime-preview-2024-10-01",
        temperature=0.9,
        api_key=os.environ.get("OPENAI_API_KEY"),
        conn_options=None
    )

    ws = await stream._connect_websocket()
    assert ws is not None
    await ws.close()

@pytest.mark.asyncio
async def test_error_handling():
    """Test error handling with invalid API key"""
    with pytest.raises(ValueError):
        tts = RealtimeTTS(voice="marin", api_key="")
```

### 5.2 Integration Testing (with LiveKit)

```python
@pytest.mark.asyncio
async def test_livekit_integration():
    """Test integration with LiveKit AgentSession"""
    from livekit.agents.voice import AgentSession
    from livekit.plugins import silero, openai, deepgram
    from custom_tts.realtime_tts import RealtimeTTS

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=RealtimeTTS(voice="marin"),
    )

    # Generate test speech
    await session.generate_reply(
        instructions="Say: Testing realtime TTS integration"
    )
```

### 5.3 Live SIP Call Testing

**Manual Test Procedure**:

1. **Deploy to LiveKit Cloud**:
   ```bash
   lk agent deploy
   ```

2. **Make test call** to configured Telnyx number

3. **Test scenarios**:
   - Short greeting (< 10 words)
   - Medium response (50-100 words)
   - Long response (200+ words)
   - Special characters and punctuation
   - Numbers and dates

4. **Monitor logs**:
   ```bash
   lk agent logs <AGENT_ID>
   ```

5. **Check metrics**:
   - Latency: Time from LLM response → audio start
   - Audio quality: Clarity, naturalness, no distortion
   - Reliability: Success rate, error frequency
   - SIP termination: Proper cleanup (no phantom billing)

### 5.4 Logging for Debugging

Add comprehensive logging to `RealtimeChunkedStream`:

```python
logger.debug(f"WebSocket state: {ws.state}")
logger.debug(f"Event received: {event_type}")
logger.debug(f"Audio chunk size: {len(audio_bytes)} bytes")
logger.debug(f"Total frames emitted: {frame_count}")
logger.info(f"Synthesis latency: {end_time - start_time:.2f}s")
```

### 5.5 Expected Latency Benchmarks

**Target Latency** (text → first audio chunk):
- **Ideal**: 200-400ms
- **Acceptable**: 400-600ms
- **Warning**: >600ms

**Breakdown**:
1. WebSocket connection: ~50-100ms (cached/reused)
2. Session initialization: ~50-100ms
3. TTS generation (first chunk): ~100-200ms
4. Network transmission: ~50-100ms

**Total Pipeline** (user speech → agent response):
- STT (Deepgram): ~150ms
- LLM (GPT-4o-mini): ~400ms
- TTS (Realtime): ~300ms
- **Total**: ~850ms (acceptable for phone calls)

---

## Section 6: Risk Assessment & Mitigation

### Risk 1: Manual Conversation Item Audio Issues

**Risk**: Community reports indicate that manually creating conversation items with `conversation.item.create` can cause the Realtime API to stop generating audio output.

**Likelihood**: High (multiple community reports)

**Impact**: Critical (TTS would not work)

**Mitigation**:
1. **Primary Approach**: Use `response.create` with `instructions` instead of manual items
   ```json
   {"type": "response.create", "response": {"modalities": ["audio"], "instructions": "Say: [text]"}}
   ```
2. **Testing**: Thoroughly test audio output reliability
3. **Fallback**: If issues persist, consider using standard OpenAI TTS API instead of Realtime

**Status**: Medium concern - workaround available

---

### Risk 2: WebSocket Connection Stability

**Risk**: WebSocket connections can drop, timeout, or become unstable during synthesis.

**Likelihood**: Medium

**Impact**: High (synthesis fails mid-sentence)

**Mitigation**:
1. Implement retry logic (max 3 attempts)
2. Exponential backoff between retries
3. Timeout handling for all WebSocket operations
4. Connection pooling/reuse if possible
5. Graceful degradation to fallback TTS

**Status**: Low concern - standard WebSocket handling

---

### Risk 3: Latency vs Quality Tradeoff

**Risk**: Realtime API TTS might be slower than standard OpenAI TTS API due to WebSocket overhead.

**Likelihood**: Medium

**Impact**: Medium (user experience degradation)

**Comparison**:
- **Realtime API TTS**: ~300-400ms (WebSocket + generation)
- **Standard TTS API**: ~200-300ms (HTTP + generation)

**Mitigation**:
1. Benchmark both approaches
2. Optimize WebSocket connection reuse
3. Consider connection pooling
4. If latency unacceptable, fall back to standard TTS

**Status**: Medium concern - needs benchmarking

---

### Risk 4: Audio Format Compatibility

**Risk**: PCM16 24kHz from Realtime API might not integrate smoothly with LiveKit's pipeline.

**Likelihood**: Low

**Impact**: High (audio distortion/quality issues)

**Mitigation**:
1. LiveKit documentation confirms automatic resampling
2. Test thoroughly with actual SIP calls
3. Verify no audio artifacts during resampling
4. Monitor audio quality metrics

**Status**: Low concern - LiveKit handles this

---

### Risk 5: Cost Implications

**Risk**: Realtime API is more expensive than standard TTS API.

**Cost Comparison** (per 1M tokens):
- **Realtime API audio output**: $200/1M tokens (~$0.24/min)
- **Standard TTS API**: $15/1M chars (~$0.015/min)

**Impact**: Critical (16x cost increase!)

**Mitigation**:
1. **Only use if quality justifies cost**
2. Monitor usage carefully
3. Set billing alerts
4. Consider standard TTS for non-critical responses
5. Benchmark if Marin/Cedar voice quality is worth the premium

**Status**: HIGH CONCERN - cost evaluation critical

---

### Risk 6: API Rate Limits

**Risk**: Realtime API has different rate limits than standard APIs.

**Likelihood**: Medium (during high call volume)

**Impact**: High (calls fail to connect)

**Mitigation**:
1. Check Realtime API rate limits
2. Implement queuing if needed
3. Monitor 429 errors
4. Fallback to standard TTS on rate limit

**Status**: Medium concern - needs monitoring

---

### Risk 7: Voice Availability

**Risk**: Marin/Cedar voices are exclusive to Realtime API and may not always be available.

**Likelihood**: Low

**Impact**: Medium (need to use different voice)

**Mitigation**:
1. Test voice availability during initialization
2. Have fallback voice configured ("alloy", "nova")
3. Graceful degradation to standard TTS with similar voice

**Status**: Low concern

---

## Fallback Strategy

If Realtime TTS proves unreliable or too expensive:

### Option 1: Standard OpenAI TTS API

```python
from livekit.plugins import openai

# Simple fallback
tts = openai.TTS(
    voice="nova",  # Closest to Marin quality
    model="tts-1-hd",  # High-definition quality
    speed=1.0,
)
```

**Pros**: Reliable, cheaper, simpler
**Cons**: No Marin/Cedar voices, potentially lower quality

### Option 2: Hybrid Approach

```python
class HybridTTS(tts.TTS):
    def __init__(self):
        self.realtime_tts = RealtimeTTS(voice="marin")
        self.standard_tts = openai.TTS(voice="nova", model="tts-1-hd")
        self.use_realtime = True  # Feature flag

    def synthesize(self, text, **kwargs):
        if self.use_realtime:
            try:
                return self.realtime_tts.synthesize(text, **kwargs)
            except Exception as e:
                logger.warning(f"Realtime TTS failed, falling back: {e}")
                self.use_realtime = False  # Disable for session

        return self.standard_tts.synthesize(text, **kwargs)
```

**Pros**: Best of both worlds, automatic fallback
**Cons**: More complex, harder to debug

---

## Section 7: Recommendation

### Should You Implement This?

**PROS**:
- Highest quality TTS voices (Marin/Cedar exclusive to Realtime API)
- More natural, expressive speech
- Better prosody and emotion
- Consistent with "premium" brand positioning

**CONS**:
- 16x more expensive than standard TTS ($0.24/min vs $0.015/min)
- More complex implementation
- Higher risk of audio output reliability issues
- Unknown latency impact (needs benchmarking)

### Recommendation

**Phase 1: Benchmark Standard TTS**
1. First, optimize with standard OpenAI TTS (nova voice, tts-1-hd)
2. Measure quality, latency, reliability
3. Collect user feedback

**Phase 2: A/B Test Realtime TTS**
1. Implement Realtime TTS with fallback
2. Test with 5-10% of calls
3. Compare metrics:
   - User satisfaction
   - Call completion rate
   - Audio quality ratings
   - Cost per successful call

**Phase 3: Decision**
- If quality improvement justifies 16x cost → Roll out
- If marginal difference → Stay with standard TTS
- If issues persist → Maintain fallback only

### Alternative: Wait for Standard API Marin/Cedar

OpenAI may eventually add Marin/Cedar voices to standard TTS API. Monitor announcements before investing in Realtime TTS implementation.

---

## Appendix A: Complete File Structure

```
src/
├── agent.py                          # Main agent (modify TTS initialization)
└── custom_tts/
    ├── __init__.py                   # Package exports
    └── realtime_tts.py               # Realtime TTS implementation

tests/
└── test_realtime_tts.py              # Unit and integration tests

docs/
└── REALTIME_TTS_IMPLEMENTATION_PLAN.md  # This document
```

## Appendix B: Required Dependencies

Add to `requirements.txt`:

```
livekit-agents[openai]>=0.10.0
websockets>=12.0
python-dotenv>=1.0.0
```

## Appendix C: Environment Variables

Add to `.env`:

```bash
OPENAI_API_KEY=sk-...           # Required for Realtime API
LIVEKIT_URL=wss://...           # Existing
LIVEKIT_API_KEY=...             # Existing
LIVEKIT_API_SECRET=...          # Existing
```

---

## Conclusion

This implementation plan provides a complete technical roadmap for building a custom TTS component using OpenAI's Realtime API. However, due to:

1. **Cost concerns** (16x more expensive)
2. **Reliability concerns** (community-reported audio output issues)
3. **Complexity** (WebSocket management, error handling)

**It is recommended to thoroughly evaluate whether the quality improvement justifies the costs and risks before proceeding with full implementation.**

Consider starting with standard OpenAI TTS (nova voice, tts-1-hd model) and only implementing Realtime TTS if A/B testing shows significant user experience improvements.

---

**Document Version**: 1.0
**Last Updated**: 2025-10-23
**Author**: Claude Code Research Agent
**Status**: Complete - Ready for Review
