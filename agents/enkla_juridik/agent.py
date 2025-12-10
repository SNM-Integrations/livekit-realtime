#!/usr/bin/env python3
"""
Enkla Juridik Intake Agent
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

from livekit.agents import JobContext, WorkerOptions, cli, function_tool, RunContext, Agent, llm
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
ENKLA_WEBHOOK_URL = os.getenv("ENKLA_WEBHOOK_URL", "")


# ============================================================================
# CONVERSATION STATE TRACKING
# ============================================================================

@dataclass
class CaseState:
    """Track legal case intake state."""
    category: str = "unknown"           # arbetsratt, familjeratt, tvist, etc.
    document_status: str = "unknown"    # saknas, utkast_jurist, utkast_egen
    maturity: str = "unknown"           # koncept, aktiv
    amount_disputed: Optional[float] = None
    email_collected: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "document_status": self.document_status,
            "maturity": self.maturity,
            "amount_disputed": self.amount_disputed,
            "email_collected": self.email_collected
        }

    def is_ready_for_booking(self) -> bool:
        """Check if we have enough info to close."""
        # Can book if we know category AND document is missing
        if self.category != "unknown" and self.document_status == "saknas":
            return True
        # Or if we have category + document status
        if self.category != "unknown" and self.document_status != "unknown":
            return True
        return False


@dataclass
class ConversationTracker:
    """Track conversation for webhook."""
    call_id: str = ""
    lead_name: str = ""
    phone_number: str = ""
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
# COLLECT EMAIL FUNCTION TOOL
# ============================================================================

@function_tool
async def collect_email(
    context: RunContext,
    email: str,
    category: str,
    document_status: str,
    notes: str = ""
) -> dict[str, Any]:
    """
    Collect customer email and register the legal case.

    This function should be called after the customer has confirmed their
    document needs and provided their email address.

    Args:
        email: Customer's email address (spelled out and confirmed)
        category: Legal category (arbetsratt, familjeratt, tvist, avtal, bostad, arv, annat)
        document_status: Document status (saknas, utkast_jurist, utkast_egen)
        notes: Any additional notes from the conversation

    Returns:
        Dictionary confirming the case was registered
    """
    global _tracker

    logger.info(f"📧 Collecting email: {email}, Category: {category}, Status: {document_status}")

    # Update tracker state
    if _tracker:
        _tracker.case_state.category = category
        _tracker.case_state.document_status = document_status
        _tracker.case_state.email_collected = email
        _tracker.case_filed = True

    # Send to webhook if configured
    if ENKLA_WEBHOOK_URL:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "email": email,
                    "category": category,
                    "document_status": document_status,
                    "notes": notes,
                    "lead_name": _tracker.lead_name if _tracker else "Unknown",
                    "phone_number": _tracker.phone_number if _tracker else "Unknown",
                    "call_id": _tracker.call_id if _tracker else "Unknown",
                    "filed_at": datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(),
                    "agent": "enkla-juridik"
                }

                logger.debug(f"📤 Sending to Enkla webhook: {payload}")

                async with session.post(
                    ENKLA_WEBHOOK_URL,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"✅ Case registered successfully")
                    else:
                        logger.warning(f"⚠️ Webhook returned {resp.status}")

        except Exception as e:
            logger.error(f"❌ Webhook error: {e}")
            # Continue anyway - don't fail the call

    return {
        "success": True,
        "message": f"Case registered for {email}",
        "category": category,
        "document_status": document_status,
        "case_filed": True
    }


# ============================================================================
# END CALL FUNCTION TOOL
# ============================================================================

@function_tool
async def end_call(
    context: RunContext,
    reason: str = "Call completed"
) -> dict[str, Any]:
    """
    End the phone call and terminate the SIP connection properly.

    This function should be called when the conversation has naturally concluded,
    for example after:
    - Successfully registering a case and saying goodbye
    - The customer declines and you've said a polite goodbye
    - The conversation has reached a natural end point

    IMPORTANT: Only call this function AFTER you have finished speaking your final message.
    Do NOT call this while you are still speaking or before saying goodbye.

    Args:
        reason: Brief reason for ending the call (e.g., "Case filed", "Customer declined")

    Returns:
        Dictionary confirming the call is being ended
    """
    global _job_context

    logger.info(f"🔚 END CALL requested - Reason: {reason}")

    if _job_context is None:
        logger.error("❌ No job context available - cannot end call")
        return {
            "success": False,
            "error": "No active call session",
            "message": "Could not end call - no active session"
        }

    try:
        logger.info(f"🗑️ Deleting room: {_job_context.room.name}")

        await _job_context.api.room.delete_room(
            api.DeleteRoomRequest(
                room=_job_context.room.name,
            )
        )

        logger.info("✅ Room deleted successfully - SIP call terminated")

        return {
            "success": True,
            "message": "Call ended successfully",
            "reason": reason,
            "room": _job_context.room.name
        }

    except Exception as e:
        logger.error(f"❌ Error ending call: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": f"Failed to end call: {str(e)}"
        }


# ============================================================================
# WEBHOOK
# ============================================================================

async def send_webhook(tracker: ConversationTracker):
    """Send conversation data to webhook."""
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        logger.warning("No WEBHOOK_URL configured")
        return

    tracker.end_time = asyncio.get_event_loop().time()

    payload = {
        "call_id": tracker.call_id,
        "lead_name": tracker.lead_name,
        "phone_number": tracker.phone_number,
        "call_outcome": "case_filed" if tracker.case_filed else "no_case",
        "case_filed": tracker.case_filed,
        "case_state": tracker.case_state.to_dict(),
        "conversation": tracker.conversation_items,
        "duration_seconds": tracker.get_duration(),
        "timestamp": int(tracker.end_time),
        "start_time": tracker.start_time,
        "end_time": tracker.end_time,
        "debug_logs": tracker.debug_logs
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.info("✅ Webhook sent successfully")
                else:
                    logger.error(f"❌ Webhook failed: {resp.status}")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")


# ============================================================================
# ENTRYPOINT
# ============================================================================

async def entrypoint(ctx: JobContext):
    """Enkla Juridik intake agent entrypoint."""
    global _job_context, _tracker

    _job_context = ctx

    logger.info("🏛️ Enkla Juridik Agent starting")

    await ctx.connect()

    # Initialize tracking
    tracker = ConversationTracker()
    tracker.call_id = ctx.room.name
    tracker.start_time = asyncio.get_event_loop().time()
    _tracker = tracker

    # Safety timeouts
    last_activity_time = asyncio.get_event_loop().time()
    SILENCE_TIMEOUT = 45
    MAX_CALL_DURATION = 600

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
            await ctx.shutdown(reason="Call timeout reached")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    async def check_timeouts():
        start_time = asyncio.get_event_loop().time()
        while not session_ended:
            await asyncio.sleep(5)
            current_time = asyncio.get_event_loop().time()

            if current_time - start_time >= MAX_CALL_DURATION:
                logger.warning(f"⏱️ Max duration reached ({MAX_CALL_DURATION}s)")
                await force_end_call()
                break

            if current_time - last_activity_time >= SILENCE_TIMEOUT:
                logger.warning(f"🔇 Silence timeout ({SILENCE_TIMEOUT}s)")
                await force_end_call()
                break

    asyncio.create_task(check_timeouts())

    # Extract lead name from room name (format: call_name_timestamp)
    lead_name = "dar"
    if "_" in ctx.room.name:
        room_parts = ctx.room.name.split("_")
        if len(room_parts) >= 3:
            lead_name = room_parts[-2]
            logger.info(f"Extracted name: {lead_name}")

    # Extract phone number from SIP participant
    phone_number = "Unknown"
    for participant in ctx.room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            phone_number = participant.attributes.get('sip.phoneNumber', 'Unknown')
            logger.info(f"📞 Phone: {phone_number}")
            break

    tracker.lead_name = lead_name
    tracker.phone_number = phone_number

    # Load prompt - try multiple paths for local vs deployed
    prompt_paths = [
        "Prompts/enkla_juridik_agent_prompt.md",           # Deployed (copied to container)
        "../../Prompts/enkla_juridik_agent_prompt.md",     # Local (relative to agents/enkla_juridik/)
    ]

    prompt_template = None
    prompt_file = None
    for path in prompt_paths:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
                prompt_file = path
                break
        except FileNotFoundError:
            continue

    if not prompt_template:
        logger.error(f"❌ Could not find prompt file in any of: {prompt_paths}")
        raise FileNotFoundError(f"Prompt file not found")

    try:

        # Skip frontmatter
        if "---" in prompt_template:
            parts = prompt_template.split("---", 2)
            if len(parts) >= 3:
                prompt_template = parts[2].strip()

        # Get Swedish date/time
        current_datetime = datetime.now(ZoneInfo("Europe/Stockholm"))
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
        day_swedish = swedish_days.get(current_datetime.strftime("%A"), current_datetime.strftime("%A"))
        month_swedish = swedish_months.get(current_datetime.strftime("%B"), current_datetime.strftime("%B"))
        current_date_str = f"{day_swedish} {current_datetime.day} {month_swedish} {current_datetime.year}"
        current_time_str = current_datetime.strftime("%H:%M")

        instructions = prompt_template.replace("{lead_name}", lead_name)
        instructions = instructions.replace("{current_date}", current_date_str)
        instructions = instructions.replace("{current_time}", current_time_str)
        instructions = instructions.replace("{phone_number}", phone_number)

        logger.info(f"✅ Loaded prompt from {prompt_file}")

    except Exception as e:
        logger.error(f"❌ Error loading prompt: {e}")
        instructions = f"Du ar en paralegal for Enkla Juridik. Du pratar med {lead_name}."

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
                prompt="Svenskt samtal for juridisk arenderegistrering. Enkla Juridik. Vanliga termer: uppsagning, bodelning, fordran, aktenskapsforord, dubbel-v ar W, snabel-a ar @."
            )
        )
    )

    # Create agent with instructions and tools (NO llm)
    agent = Agent(
        instructions=instructions,
        tools=[collect_email, end_call]
    )

    # Event handlers
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        update_activity()
        tracker.add_item(
            role=event.item.role,
            content=event.item.text_content,
            timestamp=event.created_at
        )
        logger.debug(f"💬 {event.item.role}: {event.item.text_content[:80] if event.item.text_content else ''}...")

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:
            update_activity()
            logger.info(f"🎤 User: {event.transcript}")

    # Webhook callback
    async def send_completion_webhook():
        logger.info("📤 Sending completion webhook...")
        tracker.debug_logs = {
            "agent_name": "enkla-juridik",
            "voice": "marin",
            "language": "sv",
            "prompt_file": prompt_file,
            "lead_name": lead_name,
            "phone_number": phone_number,
            "case_state": tracker.case_state.to_dict()
        }
        await send_webhook(tracker)

    ctx.add_shutdown_callback(send_completion_webhook)

    # Start session
    await session.start(room=ctx.room, agent=agent)

    # Wait for SIP readiness
    logger.info("⏳ Waiting 0.5s for SIP participant...")
    await asyncio.sleep(0.5)

    # Send greeting
    greeting = f"Hej {lead_name}, det ar Emma fran Enkla Juridik. Passar det att prata nu?"
    greeting_instruction = f"Sag denna halsning pa svenska: '{greeting}' och vanta pa svar."

    logger.info(f"👋 Greeting: {greeting}")
    await session.generate_reply(instructions=greeting_instruction)
    logger.info("✅ Greeting sent")

    # Handle participant disconnect
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info(f"📞 Participant disconnected: {participant.identity}")

        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            logger.warning("🔚 SIP participant hung up - cleaning up")

            async def cleanup_room():
                try:
                    await ctx.api.room.delete_room(
                        api.DeleteRoomRequest(room=ctx.room.name)
                    )
                    logger.info("✅ Room deleted after SIP disconnect")
                except Exception as e:
                    logger.error(f"❌ Failed to delete room: {e}")

            asyncio.create_task(cleanup_room())


if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="enkla-juridik"
    ))
