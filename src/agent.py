#!/usr/bin/env python3
"""
Enkla Juridik Inbound Intake Agent
Legal case intake and triage for Enkla Juridik

Based on LiveKit 2025 pattern: LLM in AgentSession, Agent has instructions only
"""

import asyncio
import logging
import json
import aiohttp
import os
from datetime import datetime
from typing import Optional, Any
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from livekit.agents import JobContext, WorkerOptions, cli, function_tool, RunContext, Agent
from livekit.agents.voice import AgentSession
from livekit.agents import ConversationItemAddedEvent, UserInputTranscribedEvent
from livekit.plugins import openai
from livekit import rtc, api
from openai.types.beta.realtime.session import InputAudioTranscription, TurnDetection
from dotenv import load_dotenv

load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("enkla-juridik")

# Global reference to job context for end_call function
_job_context: Optional[JobContext] = None

# Webhook configuration
ENKLA_WEBHOOK_URL = os.getenv(
    "ENKLA_WEBHOOK_URL",
    "https://n8n-08HY.sliplane.app/webhook-test/5c15e3ca-3e80-47e0-91bd-c494cfcf1bb6"
)


# ============================================================================
# CASE STATE TRACKING
# ============================================================================

@dataclass
class CaseState:
    """Track legal case intake state."""
    category: str = "unknown"           # arbetsratt, familjeratt, tvist, avtal, bostad, arv
    subtype: str = "unknown"            # e.g., uppsagning, renoveringstvist, GDPR+avtal
    main_goal: str = "unknown"          # what customer wants to achieve
    maturity: str = "unknown"           # koncept vs aktivt
    document_status: str = "unknown"    # saknas, utkast_jurist, utkast_egen, befintligt
    has_written_notice: bool = False    # brev, mail, varsel, faktura
    email_sms_sent: bool = False        # if SMS was sent to collect email

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "subtype": self.subtype,
            "main_goal": self.main_goal,
            "maturity": self.maturity,
            "document_status": self.document_status,
            "has_written_notice": self.has_written_notice,
            "email_sms_sent": self.email_sms_sent
        }

    def is_ready_for_booking(self) -> bool:
        """Check if we have enough info to book."""
        if self.category == "unknown":
            return False
        if self.document_status != "unknown" or self.maturity == "aktivt":
            return True
        return False


@dataclass
class ConversationTracker:
    """Track conversation for webhook."""
    call_id: str = ""
    caller_phone: str = ""
    case_filed: bool = False
    conversation_items: list = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0
    case_state: CaseState = field(default_factory=CaseState)
    debug_logs: dict = field(default_factory=dict)

    def add_item(self, role: str, content: str, timestamp: float):
        self.conversation_items.append({
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(timestamp).isoformat()
        })

    def get_duration(self) -> float:
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0


# Global tracker for current call
_tracker: Optional[ConversationTracker] = None


# ============================================================================
# FETCH_EMAIL FUNCTION TOOL (SMS-BASED)
# ============================================================================

@function_tool
async def fetch_email(
    context: RunContext,
    case_summary: str = ""
) -> dict[str, Any]:
    """
    Send an SMS to the caller asking them to reply with their email address.

    This tool sends an SMS message to the caller's phone number. The customer
    replies to the SMS with their email, which is then processed by the backend.

    IMPORTANT: Only call this AFTER the customer has agreed to proceed.
    Say to them first: "Om du vill ga vidare behover jag skicka ett SMS till dig
    dar du kan skriva din e-postadress, ar det okej?"

    Args:
        case_summary: Brief summary of the case for backend context

    Returns:
        Dictionary confirming SMS was sent
    """
    global _tracker

    logger.info(f"📧 Triggering SMS for email collection")

    phone_number = _tracker.caller_phone if _tracker else "Unknown"

    if phone_number == "Unknown":
        logger.error("❌ No phone number available for SMS")
        return {
            "success": False,
            "error": "No phone number available",
            "message": "Could not send SMS - phone number unknown"
        }

    # Update tracker state
    if _tracker:
        _tracker.case_state.email_sms_sent = True

    # Send to webhook - this triggers n8n to send SMS
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "action": "fetch_email",
                "phone_number": phone_number,
                "case_summary": case_summary,
                "case_state": _tracker.case_state.to_dict() if _tracker else {},
                "call_id": _tracker.call_id if _tracker else "unknown",
                "timestamp": datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(),
            }

            logger.info(f"📤 Sending fetch_email to webhook: {phone_number}")

            async with session.post(
                ENKLA_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    logger.info(f"✅ SMS triggered successfully")
                    return {
                        "success": True,
                        "message": "SMS sent to customer for email collection",
                        "phone": phone_number
                    }
                else:
                    logger.warning(f"⚠️ Webhook returned {resp.status}")
                    return {
                        "success": False,
                        "error": f"Webhook returned {resp.status}",
                        "message": "Could not send SMS"
                    }

    except Exception as e:
        logger.error(f"❌ SMS webhook error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Technical error sending SMS"
        }


# ============================================================================
# END CALL FUNCTION TOOL
# ============================================================================

@function_tool
async def end_call(
    context: RunContext,
    reason: str = "Call completed",
    case_outcome: str = "unknown"
) -> dict[str, Any]:
    """
    End the phone call and terminate the SIP connection properly.

    Call this when the conversation has naturally concluded:
    - After registering a case and saying goodbye
    - After the customer declines and you've said goodbye
    - After sending SMS for email and giving final instructions

    IMPORTANT: Say goodbye FIRST, then call this function.

    Args:
        reason: Brief reason for ending (e.g., "Case registered", "Customer declined")
        case_outcome: Outcome classification (e.g., "case_filed", "declined", "callback")

    Returns:
        Dictionary confirming the call is being ended
    """
    global _job_context, _tracker

    logger.info(f"🔚 END CALL - Reason: {reason}, Outcome: {case_outcome}")

    # Update tracker
    if _tracker:
        _tracker.case_filed = (case_outcome == "case_filed")

    if _job_context is None:
        logger.error("❌ No job context - cannot end call")
        return {
            "success": False,
            "error": "No active call session"
        }

    try:
        logger.info(f"🗑️ Deleting room: {_job_context.room.name}")

        await _job_context.api.room.delete_room(
            api.DeleteRoomRequest(
                room=_job_context.room.name,
            )
        )

        logger.info("✅ Room deleted - SIP call terminated")

        return {
            "success": True,
            "message": "Call ended successfully",
            "reason": reason,
            "outcome": case_outcome
        }

    except Exception as e:
        logger.error(f"❌ Error ending call: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================================
# WEBHOOK
# ============================================================================

async def send_completion_webhook(tracker: ConversationTracker):
    """Send conversation data to webhook on call completion."""

    tracker.end_time = asyncio.get_event_loop().time()

    payload = {
        "action": "call_completed",
        "call_id": tracker.call_id,
        "caller_phone": tracker.caller_phone,
        "case_filed": tracker.case_filed,
        "case_state": tracker.case_state.to_dict(),
        "conversation": tracker.conversation_items,
        "duration_seconds": tracker.get_duration(),
        "timestamp": datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(),
        "debug_logs": tracker.debug_logs
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ENKLA_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    logger.info("✅ Completion webhook sent")
                else:
                    logger.error(f"❌ Webhook failed: {resp.status}")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")


# ============================================================================
# ENTRYPOINT
# ============================================================================

async def entrypoint(ctx: JobContext):
    """Enkla Juridik inbound intake agent entrypoint."""
    global _job_context, _tracker

    _job_context = ctx

    logger.info("🏛️ Enkla Juridik Inbound Agent starting")

    await ctx.connect()

    # Initialize tracking
    tracker = ConversationTracker()
    tracker.call_id = ctx.room.name
    tracker.start_time = asyncio.get_event_loop().time()
    _tracker = tracker

    # Safety timeouts
    last_activity_time = asyncio.get_event_loop().time()
    SILENCE_TIMEOUT = 60      # 60s for inbound (callers may need time to explain)
    MAX_CALL_DURATION = 900   # 15 min max for legal intake

    session_ended = False

    def update_activity():
        nonlocal last_activity_time
        last_activity_time = asyncio.get_event_loop().time()

    async def force_end_call():
        nonlocal session_ended
        if session_ended:
            return
        session_ended = True
        logger.warning("⚠️ Force ending call - timeout")
        try:
            await ctx.shutdown(reason="Call timeout")
        except Exception as e:
            logger.error(f"Shutdown error: {e}")

    async def check_timeouts():
        start = asyncio.get_event_loop().time()
        while not session_ended:
            await asyncio.sleep(5)
            now = asyncio.get_event_loop().time()

            if now - start >= MAX_CALL_DURATION:
                logger.warning(f"⏱️ Max duration ({MAX_CALL_DURATION}s)")
                await force_end_call()
                break

            if now - last_activity_time >= SILENCE_TIMEOUT:
                logger.warning(f"🔇 Silence timeout ({SILENCE_TIMEOUT}s)")
                await force_end_call()
                break

    asyncio.create_task(check_timeouts())

    # Extract phone number from SIP participant (INBOUND - caller already in room)
    caller_phone = "Unknown"

    # Wait briefly for participant info
    await asyncio.sleep(0.3)

    for participant in ctx.room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            caller_phone = participant.attributes.get('sip.phoneNumber', 'Unknown')
            logger.info(f"📞 Inbound caller: {caller_phone}")
            break

    tracker.caller_phone = caller_phone

    # Load prompt
    prompt_file = "Prompts/enkla_juridik_inbound.md"

    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        # Skip frontmatter
        if "---" in prompt_template:
            parts = prompt_template.split("---", 2)
            if len(parts) >= 3:
                prompt_template = parts[2].strip()

        # Get Swedish date/time
        now = datetime.now(ZoneInfo("Europe/Stockholm"))
        swedish_days = {
            "Monday": "mandag", "Tuesday": "tisdag", "Wednesday": "onsdag",
            "Thursday": "torsdag", "Friday": "fredag", "Saturday": "lordag", "Sunday": "sondag"
        }
        swedish_months = {
            "January": "januari", "February": "februari", "March": "mars",
            "April": "april", "May": "maj", "June": "juni",
            "July": "juli", "August": "augusti", "September": "september",
            "October": "oktober", "November": "november", "December": "december"
        }
        day_sw = swedish_days.get(now.strftime("%A"), now.strftime("%A"))
        month_sw = swedish_months.get(now.strftime("%B"), now.strftime("%B"))
        current_date = f"{day_sw} {now.day} {month_sw} {now.year}"
        current_time = now.strftime("%H:%M")

        instructions = prompt_template.replace("{current_date}", current_date)
        instructions = instructions.replace("{current_time}", current_time)

        logger.info(f"✅ Loaded prompt from {prompt_file}")

    except FileNotFoundError:
        logger.error(f"❌ Prompt file not found: {prompt_file}")
        instructions = "Du ar paralegal for Enkla Juridik. Hjalp kunden registrera sitt arende."
    except Exception as e:
        logger.error(f"❌ Error loading prompt: {e}")
        instructions = "Du ar paralegal for Enkla Juridik. Hjalp kunden registrera sitt arende."

    # Create session with LLM (LiveKit 2025 pattern)
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime",
            voice="marin",
            modalities=["audio", "text"],
            temperature=0.6,
            turn_detection=TurnDetection(
                type="server_vad",
                threshold=0.55,
                prefix_padding_ms=200,
                silence_duration_ms=700
            ),
            input_audio_transcription=InputAudioTranscription(
                model="whisper-1",
                language="sv",
                prompt="Svenskt samtal for juridisk arenderegistrering hos Enkla Juridik. Termer: uppsagning, bodelning, fordran, aktenskapsforord, hyresratt, GDPR. dubbel-v ar W, snabel-a ar @."
            )
        )
    )

    # Create agent with instructions and tools (NO llm - it's in session)
    agent = Agent(
        instructions=instructions,
        tools=[fetch_email, end_call]
    )

    # Event handlers
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        update_activity()
        content = event.item.text_content or ""
        tracker.add_item(
            role=event.item.role,
            content=content,
            timestamp=event.created_at
        )
        if content:
            logger.debug(f"💬 {event.item.role}: {content[:100]}...")

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:
            update_activity()
            logger.info(f"🎤 Caller: {event.transcript}")

    # Completion webhook callback
    async def on_shutdown():
        logger.info("📤 Sending completion webhook...")
        tracker.debug_logs = {
            "agent": "enkla-juridik-inbound",
            "voice": "marin",
            "prompt_file": prompt_file,
            "caller_phone": caller_phone,
            "case_state": tracker.case_state.to_dict()
        }
        await send_completion_webhook(tracker)

    ctx.add_shutdown_callback(on_shutdown)

    # Start session
    await session.start(room=ctx.room, agent=agent)

    # INBOUND: Wait briefly for audio to be ready, then greet
    logger.info("⏳ Waiting for SIP audio readiness...")
    await asyncio.sleep(0.5)

    # INBOUND GREETING (customer called us)
    greeting = "Hej, du har natt Enkla Juridik, mitt namn ar Aila. Hur kan jag hjalpa dig?"
    greeting_instruction = f"Sag denna halsning pa svenska: '{greeting}' och vanta sedan pa att kunden beskriver sitt arende."

    logger.info(f"👋 Inbound greeting: {greeting}")
    await session.generate_reply(instructions=greeting_instruction)
    logger.info("✅ Greeting sent")

    # Handle participant disconnect (caller hangs up)
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info(f"📞 Participant disconnected: {participant.identity}")

        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            logger.info("🔚 Caller hung up - cleaning up")

            async def cleanup():
                try:
                    await ctx.api.room.delete_room(
                        api.DeleteRoomRequest(room=ctx.room.name)
                    )
                    logger.info("✅ Room deleted")
                except Exception as e:
                    logger.error(f"❌ Cleanup error: {e}")

            asyncio.create_task(cleanup())


if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="enkla-juridik"
    ))
