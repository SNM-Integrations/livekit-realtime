"""
Simple HTTP server that triggers LiveKit calls
Run: python trigger_call_simple.py
URL: http://localhost:8000/trigger-call
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import asyncio
import os
import sys
from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
SIP_TRUNK_ID = os.getenv("OUTBOUND_SIP_TRUNK_ID")


async def create_outbound_call(lead_name: str, phone_number: str, agent_name: str = "elsa-swedish", language: str = "English"):
    """Trigger LiveKit outbound call"""

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    room_name = f"call_{lead_name.lower().replace(' ', '_')}_{int(__import__('time').time())}"

    # 1. Create agent dispatch
    await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=agent_name,
            room=room_name,
            metadata=json.dumps({
                "lead_name": lead_name,
                "phone_number": phone_number,
                "language": language
            })
        )
    )

    # 2. Create SIP participant (make the call)
    await lkapi.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            sip_trunk_id=SIP_TRUNK_ID,
            sip_call_to=phone_number,
            room_name=room_name,
            participant_identity=f"sip_{lead_name.lower().replace(' ', '_')}",
            participant_name=lead_name,
            play_ringtone=True
        )
    )

    await lkapi.aclose()

    return {
        "success": True,
        "room_name": room_name,
        "message": f"Call initiated to {phone_number}"
    }


class RequestHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        if self.path != '/trigger-call':
            self.send_error(404)
            return

        # Parse JSON body
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)

        try:
            data = json.loads(post_data.decode('utf-8'))
        except:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return

        lead_name = data.get('name')
        phone_number = data.get('phone')
        # Accept 'language' parameter: "English", "Swedish", or "Carolina"
        # Fallback to 'country' for backward compatibility
        language = data.get('language', data.get('country', 'English'))

        # Validate
        if not lead_name or not phone_number:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing name or phone"}).encode())
            return

        # Ensure E.164 format
        if not phone_number.startswith('+'):
            # Auto-format Swedish numbers if language is Swedish or legacy country is SE
            if (language == 'Swedish' or language == 'SE') and phone_number.startswith('07'):
                phone_number = '+46' + phone_number[1:]
            else:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Phone must be E.164 format (+467...)"}).encode())
                return

        # Agent selection based on language parameter
        # "Carolina" → elsa-english (Swedish Carolina Profit Media agent)
        # "English" or "Swedish" → elsa-swedish (Finn AI bilingual agent)
        # Backward compatibility: "SE" → elsa-swedish
        if language == "Carolina":
            agent_name = "elsa-english"
        else:
            agent_name = "elsa-swedish"

        # Trigger call
        try:
            result = asyncio.run(create_outbound_call(lead_name, phone_number, agent_name, language))

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

            print(f"✅ Call initiated: {lead_name} ({phone_number}) -> Agent: {agent_name}, Room: {result.get('room_name', 'unknown')}")

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
            print(f"❌ Error: {e}")

    def do_OPTIONS(self):
        # Handle CORS preflight
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        # Custom logging
        pass


if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 8000))
    server = HTTPServer(('0.0.0.0', PORT), RequestHandler)
    print(f"🚀 Server running on http://0.0.0.0:{PORT}/trigger-call")
    print(f"   Waiting for webhook calls from n8n or website...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server stopped")
        server.shutdown()
