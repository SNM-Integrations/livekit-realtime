"""
Phase 1C Test: Deepgram STT Component
Tests Deepgram Nova-3 transcription with LiveKit SIP audio
"""

import asyncio
import logging
import os
from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli
from components.deepgram_stt import DeepgramSTT
from dotenv import load_dotenv

# Load environment
load_dotenv()

logger = logging.getLogger("test-deepgram")
logger.setLevel(logging.INFO)


async def entrypoint(ctx: JobContext):
    """
    Test entrypoint for Deepgram STT only.
    Connects to room, streams SIP audio to Deepgram, logs transcriptions.
    """
    logger.info("🧪 PHASE 1C TEST: Deepgram STT Component")
    logger.info(f"📞 Room: {ctx.room.name}")

    # Initialize Deepgram STT
    deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
    if not deepgram_api_key:
        logger.error("❌ DEEPGRAM_API_KEY not found in environment")
        return

    # Create Deepgram STT with callback
    async def on_transcript(text: str, is_final: bool):
        logger.info(f"✅ TRANSCRIPT: {text}")

    deepgram = DeepgramSTT(
        api_key=deepgram_api_key,
        language="sv-SE",
        model="nova-3",
        on_transcript=on_transcript
    )

    try:
        # Connect to room
        await ctx.connect()
        logger.info("✅ Connected to LiveKit room")

        # Connect to Deepgram
        await deepgram.connect()
        logger.info("✅ Connected to Deepgram")

        # Wait for SIP participant
        logger.info("⏳ Waiting for SIP participant...")
        sip_participant = None

        # Check existing participants
        for participant in ctx.room.remote_participants.values():
            logger.info(f"👤 Found participant: {participant.identity}")
            if "sip" in participant.identity.lower():
                sip_participant = participant
                logger.info(f"📞 SIP participant found: {participant.identity}")
                break

        # Wait for new participant if none found
        if not sip_participant:
            participant_future = asyncio.Future()

            def on_participant_connected(participant: rtc.RemoteParticipant):
                logger.info(f"👤 Participant connected: {participant.identity}")
                if "sip" in participant.identity.lower():
                    participant_future.set_result(participant)

            ctx.room.on("participant_connected", on_participant_connected)

            try:
                sip_participant = await asyncio.wait_for(participant_future, timeout=30.0)
                logger.info(f"✅ SIP participant connected: {sip_participant.identity}")
            except asyncio.TimeoutError:
                logger.error("❌ Timeout waiting for SIP participant")
                return

        # Subscribe to audio track
        logger.info("🎧 Subscribing to audio track...")
        audio_track = None

        for publication in sip_participant.track_publications.values():
            if publication.kind == rtc.TrackKind.KIND_AUDIO and publication.track:
                audio_track = publication.track
                logger.info(f"✅ Audio track found: {audio_track.sid}")
                break

        if not audio_track:
            # Wait for track to be published
            track_future = asyncio.Future()

            def on_track_published(publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
                if publication.kind == rtc.TrackKind.KIND_AUDIO and not track_future.done():
                    track_future.set_result(publication.track)

            ctx.room.on("track_published", on_track_published)

            try:
                audio_track = await asyncio.wait_for(track_future, timeout=10.0)
                logger.info(f"✅ Audio track published: {audio_track.sid}")
            except asyncio.TimeoutError:
                logger.error("❌ Timeout waiting for audio track")
                return

        # Stream audio to Deepgram
        logger.info("🎤 Streaming audio to Deepgram...")
        audio_stream = rtc.AudioStream(audio_track)

        async for audio_frame in audio_stream:
            # Convert frame to bytes and send to Deepgram
            # LiveKit provides audio as 16-bit PCM
            audio_data = audio_frame.data.tobytes()
            await deepgram.send_audio(audio_data)

    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)

    finally:
        # Cleanup
        logger.info("🧹 Cleaning up...")
        await deepgram.close()
        logger.info("✅ Test complete")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
