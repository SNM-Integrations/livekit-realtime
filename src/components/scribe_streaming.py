import asyncio
import base64
import json
import logging
from typing import Awaitable, Callable, Optional

import aiohttp
from livekit.rtc import RemoteAudioTrack
from livekit.rtc.audio_stream import AudioStream, AudioFrameEvent


LOGGER = logging.getLogger("scribe-streamer")


class ElevenLabsScribeStreamer:
    """
    Streams PCM audio to ElevenLabs Scribe v2 Realtime API and delivers transcripts back.
    """

    def __init__(
        self,
        api_key: str,
        language_code: str = "sv",
        commit_strategy: str = "vad",
        sample_rate: int = 16000,
        on_partial: Optional[Callable[[str], Awaitable[None]]] = None,
        on_commit: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> None:
        self.api_key = api_key
        self.language_code = language_code
        self.commit_strategy = commit_strategy
        self.sample_rate = sample_rate
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._on_partial = on_partial
        self._on_commit = on_commit

    async def start(self) -> None:
        """
        Connect to the ElevenLabs Realtime Scribe endpoint.
        """
        if self._ws:
            return

        url = "wss://api.elevenlabs.io/v1/speech-to-text/streaming"
        params = {
            "model_id": "scribe_v2_realtime",
            "language_code": self.language_code,
            "audio_format": f"pcm_{self.sample_rate}",
            "commit_strategy": self.commit_strategy,
        }
        headers = {
            "xi-api-key": self.api_key,
        }

        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(url, params=params, headers=headers)
        LOGGER.info("Connected to ElevenLabs Scribe streaming API")
        self._receiver_task = asyncio.create_task(self._receiver_loop())

    async def _receiver_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue

                    event = payload.get("event")
                    if event == "partial_transcript" and self._on_partial:
                        text = payload.get("text")
                        if text:
                            await self._on_partial(text)
                    elif event == "committed_transcript" and self._on_commit:
                        await self._on_commit(payload)
                    elif event in {"auth_error", "quota_exceeded", "transcriber_error", "input_error", "error"}:
                        LOGGER.error("Scribe stream error: %s", payload)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    LOGGER.error("Scribe WS error: %s", msg.data)
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - best effort logging
            LOGGER.exception("Scribe receiver failed: %s", exc)
        finally:
            self._closed.set()

    async def send_audio_chunk(self, pcm16: bytes, sample_rate: int) -> None:
        """
        Send a chunk of PCM audio to the Scribe session.
        """
        if not self._ws:
            return

        audio_base64 = base64.b64encode(pcm16).decode("ascii")
        message = {
            "event": "input_audio_chunk",
            "audio_base_64": audio_base64,
            "sample_rate": sample_rate,
        }
        try:
            await self._ws.send_json(message)
        except Exception as exc:  # pragma: no cover
            LOGGER.error("Failed sending audio chunk to Scribe: %s", exc)

    async def close(self) -> None:
        if self._receiver_task:
            self._receiver_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None
        await self._closed.wait()


async def stream_track_to_scribe(
    track: RemoteAudioTrack,
    streamer: ElevenLabsScribeStreamer,
    frame_size_ms: int = 30,
) -> None:
    """
    Utility to pull audio frames from a LiveKit RemoteAudioTrack and route to Scribe.
    """
    audio_stream: Optional[AudioStream] = None
    try:
        await streamer.start()
        audio_stream = AudioStream(
            track=track,
            sample_rate=streamer.sample_rate,
            num_channels=1,
            frame_size_ms=frame_size_ms,
        )

        async for event in audio_stream:
            frame: AudioFrameEvent = event
            pcm_bytes = frame.frame.data.tobytes()
            await streamer.send_audio_chunk(pcm_bytes, frame.frame.sample_rate)
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # pragma: no cover
        LOGGER.exception("Failed streaming track to Scribe: %s", exc)
    finally:
        if audio_stream:
            await audio_stream.aclose()
        await streamer.close()
