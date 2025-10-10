#!/usr/bin/env python3
"""
Emergency cleanup script to delete stuck LiveKit rooms/calls
Use this to close the two active calls that are accumulating session minutes
"""

import asyncio
import os
import logging
from dotenv import load_dotenv
from livekit import api

# Load environment
load_dotenv(".env.local")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleanup")


async def cleanup_stuck_rooms():
    """Delete all rooms to force-close stuck SIP calls"""

    logger.info("🧹 Starting cleanup of stuck rooms/calls")

    # Initialize LiveKit API client
    livekit_api = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET")
    )

    try:
        # List all active rooms
        logger.info("Fetching all active rooms...")
        response = await livekit_api.room.list_rooms(api.ListRoomsRequest())

        if not response.rooms:
            logger.info("✅ No active rooms found")
            return

        logger.info(f"Found {len(response.rooms)} active room(s)")

        # Delete each room
        for room in response.rooms:
            logger.info(f"🗑️  Deleting room: {room.name} (SID: {room.sid})")
            try:
                await livekit_api.room.delete_room(api.DeleteRoomRequest(room=room.name))
                logger.info(f"✅ Deleted room: {room.name}")
            except Exception as e:
                logger.error(f"❌ Failed to delete room {room.name}: {e}")

        logger.info("🎉 Cleanup complete!")

    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        raise
    finally:
        await livekit_api.aclose()


async def main():
    """Main function"""
    logger.info("=== LiveKit Room Cleanup ===")

    # Check environment
    required_vars = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logger.error(f"Missing environment variables: {missing}")
        return

    logger.info(f"LiveKit URL: {os.getenv('LIVEKIT_URL')}")

    # Run cleanup
    await cleanup_stuck_rooms()


if __name__ == "__main__":
    asyncio.run(main())
