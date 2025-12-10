#!/usr/bin/env python3
"""
Test script for Enkla Juridik agent.
Triggers a test call to the specified phone number.

Usage:
    python test_call.py [phone_number] [name]

Examples:
    python test_call.py +46723161614 TestUser
    python test_call.py  # Uses defaults
"""

import asyncio
import json
import os
import sys
from dotenv import load_dotenv
from livekit import api

# Load environment
load_dotenv("../../.env.local")
load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
SIP_TRUNK_ID = os.getenv("OUTBOUND_SIP_TRUNK_ID")

# Default test values
DEFAULT_PHONE = "+46723161614"  # Test number
DEFAULT_NAME = "TestUser"


async def trigger_enkla_call(phone_number: str, lead_name: str):
    """Trigger an Enkla Juridik test call."""

    print(f"🏛️ Enkla Juridik Test Call")
    print(f"   Phone: {phone_number}")
    print(f"   Name: {lead_name}")
    print(f"   Agent: enkla-juridik")
    print()

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    room_name = f"enkla_{lead_name.lower().replace(' ', '_')}_{int(__import__('time').time())}"

    print(f"📞 Creating room: {room_name}")

    # 1. Create agent dispatch
    await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name="enkla-juridik",
            room=room_name,
            metadata=json.dumps({
                "lead_name": lead_name,
                "phone_number": phone_number,
                "language": "Swedish",
                "use_case": "legal_intake"
            })
        )
    )
    print("✅ Agent dispatch created")

    # 2. Create SIP participant (make the call)
    await lkapi.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            sip_trunk_id=SIP_TRUNK_ID,
            sip_call_to=phone_number,
            room_name=room_name,
            participant_identity=f"sip_{lead_name.lower().replace(' ', '_')}",
            participant_name=lead_name,
            play_ringtone=True,
            krisp_enabled=True
        )
    )
    print("✅ SIP call initiated")

    await lkapi.aclose()

    print()
    print(f"🎉 Call triggered successfully!")
    print(f"   Room: {room_name}")
    print()
    print("📊 Monitor at: https://cloud.livekit.io")


def main():
    # Parse command line args
    phone = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PHONE
    name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_NAME

    # Validate phone format
    if not phone.startswith('+'):
        print(f"❌ Phone must be E.164 format (e.g., +46723161614)")
        print(f"   Got: {phone}")
        sys.exit(1)

    # Validate environment
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, SIP_TRUNK_ID]):
        print("❌ Missing environment variables. Check .env.local:")
        print(f"   LIVEKIT_URL: {'✅' if LIVEKIT_URL else '❌'}")
        print(f"   LIVEKIT_API_KEY: {'✅' if LIVEKIT_API_KEY else '❌'}")
        print(f"   LIVEKIT_API_SECRET: {'✅' if LIVEKIT_API_SECRET else '❌'}")
        print(f"   OUTBOUND_SIP_TRUNK_ID: {'✅' if SIP_TRUNK_ID else '❌'}")
        sys.exit(1)

    asyncio.run(trigger_enkla_call(phone, name))


if __name__ == "__main__":
    main()
