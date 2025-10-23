# OpenAI Realtime TTS - Quick Start Implementation Guide

## Prerequisites

- OpenAI API key with Realtime API access
- LiveKit Agents framework installed
- Python 3.10+
- websockets library

## Step 1: Install Dependencies

Add to `requirements.txt`:
```
websockets>=12.0
```

Install:
```bash
pip install websockets
```

## Step 2: Create Custom TTS Module

Create directory structure:
```
src/
└── custom_tts/
    ├── __init__.py
    └── realtime_tts.py
```

**File: `src/custom_tts/__init__.py`**
```python
from .realtime_tts import RealtimeTTS

__all__ = ["RealtimeTTS"]
```

**File: `src/custom_tts/realtime_tts.py`**

Copy the complete implementation from Section 4 of the full implementation plan, or use this minimal version:

```python
"""
Minimal OpenAI Realtime API TTS for LiveKit
"""
import os
import json
import base64
import asyncio
import logging
from typing import Optional
import websockets

from livekit.agents import tts, utils
from livekit import rtc

logger = logging.getLogger("realtime-tts")


class RealtimeTTS(tts.TTS):
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
            sample_rate=24000,
            num_channels=1,
        )
        self._voice = voice
        self._model = model
        self._temperature = temperature
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")

        if not self._api_key:
            raise ValueError("OpenAI API key required")

    def synthesize(self, text: str, *, conn_options=None) -> tts.ChunkedStream:
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
    def __init__(self, tts, input_text, voice, model, temperature, api_key, conn_options):
        super().__init__(tts, input_text, conn_options)
        self._input_text = input_text
        self._voice = voice
        self._model = model
        self._temperature = temperature
        self._api_key = api_key

    async def _run(self, output_emitter: tts.SynthesizedAudioEmitter):
        # Initialize output
        request_id = utils.shortuuid()
        segment_id = utils.shortuuid()

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=24000,
            num_channels=1,
            mime_type="audio/pcm"
        )
        output_emitter.start_segment(segment_id=segment_id)

        # Connect to WebSocket
        url = f"wss://api.openai.com/v1/realtime?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1"
        }

        async with websockets.connect(url, additional_headers=headers) as ws:
            # Configure session
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "voice": self._voice,
                    "output_audio_format": "pcm16",
                    "turn_detection": None,
                    "temperature": self._temperature,
                }
            }))

            # Wait for session confirmation
            await ws.recv()  # session.created
            await ws.recv()  # session.updated

            # Request TTS
            await ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["audio"],
                    "instructions": f"Say this exact text: '{self._input_text}'"
                }
            }))

            # Receive audio chunks
            async for message in ws:
                event = json.loads(message)

                if event.get("type") == "response.audio.delta":
                    # Decode audio
                    audio_bytes = base64.b64decode(event.get("delta", ""))
                    if not audio_bytes:
                        continue

                    # Create frame
                    samples = len(audio_bytes) // 2
                    frame = rtc.AudioFrame(
                        data=audio_bytes,
                        sample_rate=24000,
                        num_channels=1,
                        samples_per_channel=samples
                    )

                    # Emit
                    output_emitter.push_audio(frame)

                elif event.get("type") == "response.audio.done":
                    break

                elif event.get("type") == "error":
                    raise Exception(f"API error: {event.get('error')}")

        output_emitter.mark_audio_segment_end()
```

## Step 3: Modify Agent Configuration

**File: `src/agent.py`**

Add import at the top:
```python
from custom_tts.realtime_tts import RealtimeTTS
```

In the `entrypoint()` function, replace the TTS configuration:

**Before**:
```python
session = AgentSession(
    vad=silero.VAD.load(),
    stt=deepgram.STT(...),
    llm=openai.LLM(...),
    tts=openai.TTS(voice=voice_name),  # OLD
)
```

**After**:
```python
session = AgentSession(
    vad=silero.VAD.load(),
    stt=deepgram.STT(...),
    llm=openai.LLM(...),
    tts=RealtimeTTS(voice=voice_name),  # NEW
)
```

## Step 4: Test Locally (Optional)

**Note**: Per CLAUDE.md, avoid running local agents due to phantom billing. Deploy to cloud for testing.

If you must test locally:
```python
# Quick test script (test_tts.py)
import asyncio
from custom_tts.realtime_tts import RealtimeTTS

async def test():
    tts = RealtimeTTS(voice="marin")
    stream = tts.synthesize("Hello, this is a test of the Realtime TTS system.")

    frame_count = 0
    async for event in stream:
        frame_count += 1

    print(f"Success! Received {frame_count} audio frames")

asyncio.run(test())
```

## Step 5: Deploy to LiveKit Cloud

```bash
lk agent deploy
```

## Step 6: Test with Real SIP Call

1. Make a test call to your Telnyx number
2. Listen for voice quality
3. Check logs:
   ```bash
   lk agent logs <AGENT_ID>
   ```

## Step 7: Monitor Performance

**Check logs for**:
- WebSocket connection success
- Session initialization
- Audio frame emission count
- Any errors or timeouts

**Key metrics**:
- Latency (text → first audio)
- Audio quality (naturalness, clarity)
- Reliability (success rate)
- Cost per call

## Voice Options

Available voices (from best to good):
- `"marin"` - NEW, highest quality, most natural (recommended)
- `"cedar"` - NEW, highest quality, warm tone (recommended)
- `"ballad"` - Expressive
- `"coral"` - Professional
- `"sage"` - Calm
- `"shimmer"` - Clear
- `"alloy"` - Standard
- `"echo"` - Deep
- `"nova"` - Professional

## Troubleshooting

### Issue: "OpenAI API key required"
**Solution**: Set `OPENAI_API_KEY` in `.env` file

### Issue: WebSocket connection timeout
**Solution**: Check API key, check network connectivity, verify model name

### Issue: No audio received
**Solution**:
1. Check if using `response.create` (not `conversation.item.create`)
2. Verify modalities include "audio"
3. Check logs for error events

### Issue: Audio distortion or quality issues
**Solution**:
1. Verify 24kHz sample rate in AudioFrame creation
2. Check bytes calculation: `len(audio_bytes) // 2` for int16
3. Ensure mono (num_channels=1)

### Issue: High latency
**Solution**:
1. Check WebSocket connection reuse
2. Monitor network latency
3. Consider fallback to standard TTS

### Issue: Costs higher than expected
**Solution**:
1. Monitor audio output duration (billed per token, ~$0.24/min)
2. Consider A/B testing to justify cost
3. Implement usage caps

## Cost Monitoring

Add to monitoring:
```python
import time

start_time = time.time()
# ... synthesis ...
end_time = time.time()

duration = end_time - start_time
cost_estimate = (duration / 60) * 0.24  # $0.24/min

logger.info(f"TTS duration: {duration:.2f}s, est. cost: ${cost_estimate:.4f}")
```

## Rollback Plan

If issues occur, rollback to standard TTS:

**Quick rollback**:
```python
# In agent.py, change:
tts=RealtimeTTS(voice=voice_name)

# Back to:
tts=openai.TTS(voice="nova", model="tts-1-hd")
```

Redeploy:
```bash
lk agent deploy
```

## Best Practices

1. **Always use `response.create`** (not `conversation.item.create`)
2. **Set turn_detection to None** (disable server VAD)
3. **Include retry logic** (WebSocket can be unstable)
4. **Monitor costs closely** (16x more expensive than standard TTS)
5. **Test thoroughly** before full rollout
6. **Have fallback ready** (standard TTS)
7. **Log everything** (for debugging)

## Performance Benchmarks

Target metrics:
- Latency: < 400ms (text → first audio chunk)
- Success rate: > 99%
- Audio quality: Subjective improvement over standard TTS
- Cost: Monitor vs budget

## Next Steps

1. Deploy and test with small % of calls
2. Collect user feedback
3. Compare metrics vs standard TTS
4. Decide on full rollout based on data

## Support

- Full Implementation Plan: `docs/REALTIME_TTS_IMPLEMENTATION_PLAN.md`
- Executive Summary: `docs/REALTIME_TTS_EXECUTIVE_SUMMARY.md`
- OpenAI Realtime Docs: https://platform.openai.com/docs/guides/realtime
- LiveKit TTS Docs: https://docs.livekit.io/agents/models/tts/

---

**Quick Start Version**: 1.0
**Last Updated**: 2025-10-23
