#!/usr/bin/env python3
"""Debug script to get full trunk configuration"""
import asyncio
import json
import os
from livekit import api
from dotenv import load_dotenv

load_dotenv(".env.local")

async def get_trunk_details():
    lkapi = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET")
    )

    # Get outbound trunk list
    trunk_list = await lkapi.sip.list_sip_outbound_trunk(api.ListSIPOutboundTrunkRequest())

    for trunk in trunk_list.items:
        print("=" * 80)
        print(f"Trunk ID: {trunk.sip_trunk_id}")
        print(f"Name: {trunk.name}")
        print(f"Address: {trunk.address}")
        print(f"Transport: {trunk.transport}")
        print(f"Numbers: {list(trunk.numbers)}")
        print(f"Auth Username: {trunk.auth_username}")
        print(f"Headers: {dict(trunk.headers) if trunk.headers else {}}")
        print(f"Metadata: {trunk.metadata}")
        print(f"Headers to Attributes: {dict(trunk.headers_to_attributes) if trunk.headers_to_attributes else {}}")
        print(f"Attributes to Headers: {dict(trunk.attributes_to_headers) if trunk.attributes_to_headers else {}}")

        # Get all available attributes
        print("\nAll trunk attributes:")
        for attr in dir(trunk):
            if not attr.startswith('_'):
                try:
                    value = getattr(trunk, attr)
                    if not callable(value):
                        print(f"  {attr}: {value}")
                except:
                    pass
        print("=" * 80)

    await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(get_trunk_details())
