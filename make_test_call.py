#!/usr/bin/env python3
"""
Simplified LiveKit 2025 Outbound Call Test
Uses correct LiveKit dispatch pattern
ALWAYS kills phantom processes before making calls
"""

import asyncio
import os
import sys
import json
import logging
import subprocess
from dotenv import load_dotenv

# Load environment
load_dotenv(".env.local")

from livekit import api
from google.protobuf.duration_pb2 import Duration

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outbound-test")


def kill_phantom_processes():
    """Kill any existing agent.py processes to prevent phantom session minutes"""
    logger.info("🔍 Checking for phantom agent processes...")

    try:
        # Windows: Use wmic to find and kill agent.py processes
        if sys.platform == 'win32':
            result = subprocess.run(
                ['wmic', 'process', 'where', "commandline like '%agent.py%'", 'delete'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if 'Instance deletion successful' in result.stdout:
                count = result.stdout.count('Instance deletion successful')
                logger.warning(f"⚠️ KILLED {count} PHANTOM AGENT PROCESSES!")
                logger.warning(f"These were wasting session minutes in the background!")
            else:
                logger.info("✅ No phantom processes found")

        # Linux/Mac: Use pkill
        else:
            result = subprocess.run(
                ['pkill', '-f', 'agent.py'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                logger.warning("⚠️ Killed phantom agent processes")
            else:
                logger.info("✅ No phantom processes found")

    except Exception as e:
        logger.error(f"Error checking for phantom processes: {e}")
        logger.warning("Continuing anyway...")


async def make_outbound_call():
    """Make outbound call using 2025 LiveKit pattern"""

    # Your configuration
    phone_number = "+46723161614"  # Nils' number
    trunk_id = os.getenv("OUTBOUND_SIP_TRUNK_ID")

    logger.info(f"Making outbound call to {phone_number}")
    logger.info(f"Using trunk: {trunk_id}")

    # Initialize LiveKit API client
    livekit_api = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET")
    )

    try:
        # Step 1: Create a new room for the call (with lead name embedded due to metadata bug)
        timestamp = int(asyncio.get_event_loop().time())
        room_name = f"outbound_test_{timestamp}_Nils"

        # Step 2: Create agent dispatch with metadata
        metadata = {
            "phone_number": phone_number,
            "call_type": "outbound",
            "contact_data": {
                "lead_name": "Nils"
            },
            "caller_name": "Finn",
            "company_name": "Finn Outreach"
        }

        logger.info("Creating agent dispatch...")
        dispatch_request = api.CreateAgentDispatchRequest(
            room=room_name,
            agent_name="elsa-swedish",  # FIXED: Use agent name from WorkerOptions, NOT agent ID
            metadata=json.dumps(metadata)
        )

        dispatch = await livekit_api.agent_dispatch.create_dispatch(dispatch_request)
        logger.info(f"✅ Agent dispatch created: {dispatch.id}")

        # Step 3: Create SIP participant to dial the number
        logger.info("Creating SIP participant (dialing phone)...")

        sip_request = api.CreateSIPParticipantRequest(
            sip_trunk_id=trunk_id,
            sip_call_to=phone_number,
            room_name=room_name,
            participant_identity=f"caller_{int(asyncio.get_event_loop().time())}",
            participant_name=f"Outbound to {phone_number}",
            krisp_enabled=True,  # AI noise reduction for clearer Swedish transcription
            wait_until_answered=False,  # Don't wait - let it ring async
            play_dialtone=False
        )

        participant = await livekit_api.sip.create_sip_participant(sip_request)
        logger.info(f"✅ SIP participant created: {participant.participant_id}")

        logger.info(f"📞 Call initiated! Room: {room_name}")
        logger.info("🎉 Your phone should start ringing shortly!")

        return {
            "success": True,
            "room_name": room_name,
            "dispatch_id": dispatch.id,
            "participant_id": participant.participant_id
        }

    except Exception as e:
        logger.error(f"❌ Call failed: {e}")
        return {"success": False, "error": str(e)}


async def main():
    """Main function"""
    logger.info("=== LiveKit 2025 Outbound Call Test ===")

    # CRITICAL: Kill phantom processes FIRST to prevent session minute waste
    kill_phantom_processes()

    # Check environment
    required_vars = [
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OUTBOUND_SIP_TRUNK_ID"
    ]

    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logger.error(f"Missing environment variables: {missing}")
        return

    logger.info("✅ Environment check passed")
    logger.info(f"LiveKit URL: {os.getenv('LIVEKIT_URL')}")
    logger.info(f"SIP Trunk: {os.getenv('OUTBOUND_SIP_TRUNK_ID')}")

    # Make the call
    result = await make_outbound_call()

    if result["success"]:
        logger.info("✅ Outbound call successfully initiated!")
        logger.info("📱 Answer your phone to talk to the AI agent!")
    else:
        logger.error(f"❌ Call failed: {result['error']}")


if __name__ == "__main__":
    asyncio.run(main())