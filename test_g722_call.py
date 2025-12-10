#!/usr/bin/env python3
"""
Test script to make a call and capture SIP negotiation details
"""
import asyncio
import os
from livekit import api
from dotenv import load_dotenv

load_dotenv(".env.local")

async def make_test_call():
    lkapi = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET")
    )

    phone_number = "+46723161614"  # Nils test number
    lead_name = "CodecTest"

    room_name = f"test_g722_{int(__import__('time').time())}"

    print(f"Creating test call to {phone_number}")
    print(f"Room: {room_name}")
    print("-" * 80)

    # Create agent dispatch
    await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name="elsa-swedish",
            room=room_name,
            metadata='{"lead_name":"CodecTest","phone_number":"'+phone_number+'","language":"English"}'
        )
    )
    print("✓ Agent dispatched")

    # Create SIP participant with Krisp enabled to test
    await lkapi.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            sip_trunk_id=os.getenv("OUTBOUND_SIP_TRUNK_ID"),
            sip_call_to=phone_number,
            room_name=room_name,
            participant_identity="sip_codectest",
            participant_name="Codec Test",
            play_ringtone=True,
            krisp_enabled=True  # Testing with Krisp
        )
    )
    print("✓ SIP call initiated")
    print(f"\nCall active - check Telnyx portal for SIP logs")
    print(f"Room name: {room_name}")
    print(f"\nLook for codec negotiation in Telnyx SIP debug logs:")
    print("  - INVITE SDP (what Telnyx offers)")
    print("  - 183/200 OK SDP (what LiveKit responds with)")

    await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(make_test_call())
