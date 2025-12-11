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
load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
SIP_TRUNK_ID = os.getenv("OUTBOUND_SIP_TRUNK_ID")

# Load agent configuration - use ID for direct targeting
AGENT_ID = os.getenv("AGENT_ID", "CA_uG7inqdgtPgQ")  # Default to finn-outbound agent
AGENT_NAME = os.getenv("AGENT_NAME")  # Optional: use name instead of ID


async def create_outbound_call(lead_name: str, phone_number: str, lead_source: str = "cold", company_name: str = None, referrer_name: str = "Nils", language: str = "Swedish"):
    """Trigger LiveKit outbound call with hybrid agent metadata"""

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    room_name = f"call_{lead_name.lower().replace(' ', '_')}_{int(__import__('time').time())}"

    # Build metadata for hybrid outbound agent
    metadata = {
        "lead_source": lead_source,  # "cold", "form", "referral"
        "lead_name": lead_name,
        "phone_number": phone_number,
        "company_name": company_name or lead_name,
        "referrer_name": referrer_name,
        "language": language
    }

    metadata_json = json.dumps(metadata)

    # Create agent dispatch with metadata
    dispatch_request = api.CreateAgentDispatchRequest(
        room=room_name,
        metadata=metadata_json
    )

    # Use agent ID if available (more reliable), otherwise use name
    if AGENT_ID:
        dispatch_request.agent_name = AGENT_ID
    elif AGENT_NAME:
        dispatch_request.agent_name = AGENT_NAME
    else:
        raise ValueError("Either AGENT_ID or AGENT_NAME must be set in environment")

    await lkapi.agent_dispatch.create_dispatch(dispatch_request)

    # Create SIP participant (make the call)
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

    # Update room metadata AFTER room exists (created by dispatch/SIP)
    await lkapi.room.update_room_metadata(
        api.UpdateRoomMetadataRequest(
            room=room_name,
            metadata=metadata_json
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
        company_name = data.get('company')
        referrer_name = data.get('referrer', 'Nils')
        lead_source = data.get('lead_source', 'cold')  # "cold", "form", "referral"

        # Accept 'language' parameter - default Swedish for Finn hybrid agent
        language = data.get('language', 'Swedish')

        # Validate
        if not lead_name or not phone_number:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing name or phone"}).encode())
            return

        # Ensure E.164 format
        if not phone_number.startswith('+'):
            # Auto-format Swedish numbers
            if phone_number.startswith('07'):
                phone_number = '+46' + phone_number[1:]
            else:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Phone must be E.164 format (+467...)"}).encode())
                return

        # Trigger call with hybrid agent
        try:
            result = asyncio.run(create_outbound_call(
                lead_name=lead_name,
                phone_number=phone_number,
                lead_source=lead_source,
                company_name=company_name,
                referrer_name=referrer_name,
                language=language
            ))

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

            agent_id = AGENT_ID or AGENT_NAME
            print(f"✅ Call initiated: {lead_name} ({phone_number}) -> Agent: {agent_id}, Source: {lead_source}, Room: {result.get('room_name', 'unknown')}")

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
