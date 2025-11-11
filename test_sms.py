import asyncio
import aiohttp

async def test_sms_webhook():
    """Test SMS webhook with sample phone number"""

    test_phone = "+46701234567"  # Sample Swedish phone number

    print(f"📱 Testing SMS webhook with phone number: {test_phone}")

    async with aiohttp.ClientSession() as session:
        payload = {
            "phone_number": test_phone
        }

        print(f"📤 Sending request to webhook...")

        async with session.post(
            "https://snmnils.app.n8n.cloud/webhook/10528303-442d-4759-a966-c496e6a12e3d",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            print(f"📥 Response status: {response.status}")

            if response.status == 200:
                print("✅ SMS webhook triggered successfully!")
                response_text = await response.text()
                print(f"Response: {response_text}")
            else:
                error_text = await response.text()
                print(f"❌ Webhook failed: {error_text}")

if __name__ == "__main__":
    asyncio.run(test_sms_webhook())
