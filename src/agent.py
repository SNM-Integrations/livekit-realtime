#!/usr/bin/env python3
"""
Finn AI Outbound Agent - Elsa AI
SIMPLIFIED SINGLE-AGENT with natural conversation flow
V3: Trust the model - no complex handoffs - FIXED Agent class
REBUILD: 2025-10-03-19-15
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

logger = logging.getLogger("finn-ai-swedish")

# Global reference to job context for end_call function
_job_context: Optional[JobContext] = None

# Recording configuration
ENABLE_RECORDING = os.getenv("ENABLE_CALL_RECORDING", "false").lower() == "true"
RECORDING_WEBHOOK_URL = os.getenv("RECORDING_WEBHOOK_URL")


# ============================================================================
# CALENDAR FUNCTION TOOL - N8N WEBHOOK INTEGRATION
# ============================================================================

CALENDAR_WEBHOOK_URL = "https://snmnils.app.n8n.cloud/webhook/060bdc6e-f8f4-4394-af0c-13ece37800aa"

# Calendar cache storage (keyed by date)
_calendar_cache: dict[str, list[dict]] = {}

@function_tool
async def check_availability(
    context: RunContext,
    start_datetime: str,
    end_datetime: str
) -> dict[str, Any]:
    """
    Check calendar availability within a date/time range.

    This fetches all available time slots within the specified window.
    Format: ISO 8601 with timezone, e.g., "2025-10-07T09:00:00.000+02:00"

    Args:
        start_datetime: Start of search window in ISO 8601 format (e.g., "2025-10-07T09:00:00.000+02:00")
        end_datetime: End of search window in ISO 8601 format (e.g., "2025-10-14T17:00:00.000+02:00")

    Returns:
        Dictionary with all available time slots in the range

    USAGE: When customer mentions a day (e.g., "Tuesday"), create a window from that day at 09:00 to 17:00.
    For broader requests, use a week window to show multiple options.
    """
    global _calendar_cache

    logger.info(f"📅 Checking availability from {start_datetime} to {end_datetime}")

    # Create cache key from datetime range
    cache_key = f"{start_datetime}_{end_datetime}"

    # Check cache first
    if cache_key in _calendar_cache:
        logger.info(f"💾 Using cached calendar data for range")
        cached_slots = _calendar_cache[cache_key]

        return {
            "available_slots": cached_slots,
            "cached": True,
            "note": "Using cached calendar data"
        }

    # Create background task for periodic status updates during long webhook call
    tool_completed = False
    update_count = 0

    async def send_periodic_updates():
        """Send status updates every 5 seconds while tool is running"""
        nonlocal update_count
        await asyncio.sleep(5)  # Wait 5 seconds before first update

        while not tool_completed:
            update_count += 1
            try:
                # Send conversational status update
                if update_count == 1:
                    await context.session.generate_reply(
                        instructions="Say naturally in English that you're still checking the calendar, like 'I'm still checking' or 'just one more moment'"
                    )
                elif update_count == 2:
                    await context.session.generate_reply(
                        instructions="Say naturally in English that you're almost done, like 'I'm almost done' or 'just a few more seconds'"
                    )
                # After 15 seconds total (3 updates), don't send more
                if update_count >= 3:
                    break

                await asyncio.sleep(5)  # Wait another 5 seconds
            except Exception as e:
                logger.warning(f"Error sending periodic update: {e}")
                break

    # Start the background update task
    update_task = asyncio.create_task(send_periodic_updates())

    try:
        async with aiohttp.ClientSession() as session:
            # Updated webhook payload with datetime windows
            payload = {
                "start_datetime": start_datetime,
                "end_datetime": end_datetime
            }

            headers = {
                "Content-Type": "application/json"
            }

            logger.debug(f"📤 Calling calendar webhook: {payload}")

            async with session.post(
                CALENDAR_WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)  # Increased to 30s for faster responses
            ) as resp:
                response_text = await resp.text()
                logger.debug(f"📥 Webhook response: {resp.status} - {response_text[:300]}")

                if resp.status == 200:
                    try:
                        data = json.loads(response_text)
                        logger.info(f"✅ Calendar check successful: {data}")

                        # Extract available slots from response
                        available_slots = data.get("available_slots", [])

                        # Store in cache
                        _calendar_cache[cache_key] = available_slots
                        logger.info(f"💾 Cached {len(available_slots)} time slots")

                        return {
                            "available_slots": available_slots,
                            "cached": False,
                            "note": "Fresh calendar data retrieved"
                        }
                    except json.JSONDecodeError:
                        logger.warning(f"Non-JSON response: {response_text[:100]}")
                        return {
                            "available_slots": [],
                            "error": "invalid_response",
                            "message": response_text[:100] if response_text else "Invalid response",
                            "note": "Non-JSON response from calendar"
                        }
                else:
                    logger.error(f"❌ Webhook HTTP error: {resp.status} - {response_text[:200]}")
                    return {
                        "available_slots": [],
                        "error": f"http_{resp.status}",
                        "message": "Could not check calendar right now.",
                        "note": f"HTTP {resp.status}: {response_text[:100]}"
                    }

    except asyncio.TimeoutError:
        logger.error("⏱️ Webhook timeout after 30s")
        return {
            "available_slots": [],
            "error": "timeout",
            "message": "Calendar is taking too long to respond.",
            "note": "Timeout after 30s"
        }
    except Exception as e:
        logger.error(f"❌ Error checking availability: {e}", exc_info=True)
        return {
            "available_slots": [],
            "error": str(e),
            "message": "Could not check calendar.",
            "note": f"Exception: {type(e).__name__}"
        }
    finally:
        # Mark tool as completed and cancel update task
        tool_completed = True
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass  # Expected


# ============================================================================
# END CALL FUNCTION TOOL - PROPER SIP TERMINATION
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
    - Successfully booking a meeting and saying goodbye
    - The customer declines and you've said a polite goodbye
    - The conversation has reached a natural end point

    IMPORTANT: Only call this function AFTER you have finished speaking your final message.
    Do NOT call this while you are still speaking or before saying goodbye.

    Args:
        reason: Brief reason for ending the call (e.g., "Meeting booked", "Customer declined", "Conversation complete")

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
        # Delete the room to properly terminate the SIP connection
        # This ensures the caller hears a proper hangup, not silence
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
# CONVERSATION TRACKING
# ============================================================================

@dataclass
class ConversationTracker:
    """Track conversation for webhook"""
    call_id: str = ""
    lead_name: str = ""
    phone_number: str = ""
    meeting_booked: bool = False
    conversation_items: list = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0
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


async def send_webhook(tracker: ConversationTracker):
    """Send conversation data to n8n webhook"""
    webhook_url = os.getenv("WEBHOOK_URL")  # Fixed: was N8N_WEBHOOK_URL
    if not webhook_url:
        logger.warning("No webhook URL configured")
        return

    tracker.end_time = asyncio.get_event_loop().time()

    payload = {
        "call_id": tracker.call_id,
        "lead_name": tracker.lead_name,
        "phone_number": tracker.phone_number,
        "call_outcome": "demo_booked" if tracker.meeting_booked else "unknown",
        "meeting_booked": tracker.meeting_booked,
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
                    logger.info("Webhook sent successfully")
                else:
                    logger.error(f"Webhook failed: {resp.status}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")


# ============================================================================
# SINGLE ELSA AGENT
# ============================================================================

class ElsaAgent:
    """Single conversational agent - natural flow, no rigid handoffs"""

    def __init__(self, lead_name: str = "Nils", phone_number: str = "Unknown"):
        self.lead_name = lead_name
        self.phone_number = phone_number

        # Get current Swedish time - FRESH FOR EACH CALL
        current_datetime = datetime.now(ZoneInfo("Europe/Stockholm"))

        # Format date for Swedish prompt
        swedish_days = {
            "Monday": "måndag", "Tuesday": "tisdag", "Wednesday": "onsdag",
            "Thursday": "torsdag", "Friday": "fredag", "Saturday": "lördag", "Sunday": "söndag"
        }
        swedish_months = {
            "January": "januari", "February": "februari", "March": "mars",
            "April": "april", "May": "maj", "June": "juni",
            "July": "juli", "August": "augusti", "September": "september",
            "October": "oktober", "November": "november", "December": "december"
        }

        day_english = current_datetime.strftime("%A")
        month_english = current_datetime.strftime("%B")
        day_swedish = swedish_days.get(day_english, day_english)
        month_swedish = swedish_months.get(month_english, month_english)

        current_date_str = f"{day_swedish} {current_datetime.day} {month_swedish} {current_datetime.year}"
        current_time_str = current_datetime.strftime("%H:%M")

        logger.info(f"📅 Agent created with date: {current_date_str} {current_time_str}")

        # Load prompt from file
        prompt_file = "Prompts/swedish_agent_prompt.md"
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_template = f.read()

            # Extract just the prompt content (skip the header if it exists)
            if "---" in prompt_template:
                parts = prompt_template.split("---", 2)
                if len(parts) >= 3:
                    prompt_template = parts[2].strip()

            # Replace placeholders
            instructions = prompt_template.replace("{lead_name}", self.lead_name)
            instructions = instructions.replace("{current_date}", current_date_str)
            instructions = instructions.replace("{current_time}", current_time_str)
            instructions = instructions.replace("{phone_number}", self.phone_number)

            logger.info(f"✅ Loaded prompt from {prompt_file}")

        except FileNotFoundError:
            logger.error(f"❌ Prompt file not found: {prompt_file}")
            raise
        except Exception as e:
            logger.error(f"❌ Error loading prompt: {e}")
            raise

        self.agent = Agent(
            instructions=instructions,
            llm=openai.realtime.RealtimeModel(
                model="gpt-realtime",
                voice="marin",
                modalities=["audio", "text"],  # FIXED: audio first for speech output
                temperature=0.7,
                turn_detection=TurnDetection(
                    type="server_vad",
                    threshold=0.5,
                    prefix_padding_ms=600,
                    silence_duration_ms=1800  # Increased for Swedish rhythm
                ),
                input_audio_transcription=InputAudioTranscription(
                    model="whisper-1",  # FIXED: use whisper-1 like working template
                    language="sv",
                    prompt="""Swedish business call transcription for meeting booking with AI voice assistant demo

Context: Swedish business call transcription for meeting booking with AI voice assistant demo

Common phonetic terms:
- 'dubbel-v' means letter W (not v-v)
- 'snabel-a' means @
- Email addresses with Swedish names (common: Andersson, Nilsson, Lindström, Wallin, Vallin)
- Times in 24-hour format (14:00, 10:30)
- Days: måndag, tisdag, onsdag, torsdag, fredag
- Business terminology: möte, demo, AI-röst, leads, kunder

Instruction: Transcribe with high accuracy, interpret phonetic spelling contextually."""
                )
            ),
            # Register function tools: calendar checking and call ending
            tools=[check_availability, end_call]
        )

        logger.info(f"🤖 Elsa agent initialized (Swedish) for {self.lead_name}")


# ============================================================================
# ENTRYPOINT
# ============================================================================

async def entrypoint(ctx: JobContext):
    """Simplified single-agent entrypoint"""
    global _job_context
    _job_context = ctx  # Store context for end_call function

    logger.info("🚀 Finn AI Agent (Elsa) starting - SIMPLIFIED SINGLE-AGENT")

    await ctx.connect()

    # Initialize tracking
    tracker = ConversationTracker()
    tracker.call_id = ctx.room.name
    tracker.start_time = asyncio.get_event_loop().time()

    # Safety timeouts
    last_activity_time = asyncio.get_event_loop().time()
    SILENCE_TIMEOUT = 45  # End call if silent for 45 seconds
    MAX_CALL_DURATION = 600  # Hard cutoff at 10 minutes (600 seconds)

    session_ended = False  # Track if we've already ended the session

    def update_activity():
        nonlocal last_activity_time
        last_activity_time = asyncio.get_event_loop().time()

    async def force_end_call():
        """Force end the call by deleting the room"""
        nonlocal session_ended
        if session_ended:
            return
        session_ended = True

        logger.warning("⚠️ Force ending call - deleting room")
        try:
            # Use ctx.shutdown to properly end the session
            await ctx.shutdown(reason="Call timeout reached")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    async def check_timeouts():
        """Monitor both silence and absolute time limits"""
        start_time = asyncio.get_event_loop().time()

        while not session_ended:
            await asyncio.sleep(5)
            current_time = asyncio.get_event_loop().time()

            # Check absolute time limit (10 minutes)
            total_duration = current_time - start_time
            if total_duration >= MAX_CALL_DURATION:
                logger.warning(f"⏱️ Maximum call duration reached ({MAX_CALL_DURATION}s). Ending call.")
                await force_end_call()
                break

            # Check silence timeout (45 seconds)
            silence_duration = current_time - last_activity_time
            if silence_duration >= SILENCE_TIMEOUT:
                logger.warning(f"🔇 Silence timeout reached ({SILENCE_TIMEOUT}s). Ending call.")
                await force_end_call()
                break

    asyncio.create_task(check_timeouts())

    # Extract lead name from room name
    # Format: call_name_timestamp
    lead_name = "there"
    if "_" in ctx.room.name:
        room_parts = ctx.room.name.split("_")
        if len(room_parts) >= 3:
            lead_name = room_parts[-2]  # Second-to-last is the name
            logger.info(f"Extracted name from room: {lead_name}")

    # Extract phone number from SIP participant (if available)
    phone_number = "Unknown"
    for participant in ctx.room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            phone_number = participant.attributes.get('sip.phoneNumber', 'Unknown')
            logger.info(f"📞 SIP caller phone number: {phone_number}")
            break

    # Store phone number in tracker
    tracker.phone_number = phone_number

    # Load prompt instructions
    prompt_file = "Prompts/swedish_agent_prompt.md"
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        # Extract prompt content (skip frontmatter if exists)
        if "---" in prompt_template:
            parts = prompt_template.split("---", 2)
            if len(parts) >= 3:
                prompt_template = parts[2].strip()

        # Replace placeholders
        from datetime import datetime
        from zoneinfo import ZoneInfo
        current_datetime = datetime.now(ZoneInfo("Europe/Stockholm"))
        swedish_days = {"Monday": "måndag", "Tuesday": "tisdag", "Wednesday": "onsdag",
                       "Thursday": "torsdag", "Friday": "fredag", "Saturday": "lördag", "Sunday": "söndag"}
        swedish_months = {"January": "januari", "February": "februari", "March": "mars",
                         "April": "april", "May": "maj", "June": "juni", "July": "juli",
                         "August": "augusti", "September": "september", "October": "oktober",
                         "November": "november", "December": "december"}
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
        instructions = f"Du är Elsa från Finn AI. Du pratar med {lead_name}."

    # Create session WITH LLM (working template pattern)
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime",
            voice="marin",
            modalities=["audio", "text"],
            temperature=0.7,
            input_audio_transcription=InputAudioTranscription(
                model="whisper-1",
                language="sv",
                prompt="Svenska konversation med AI-assistent Elsa"
            )
        )
    )

    # Create agent with instructions and tools ONLY (NO llm)
    agent = Agent(
        instructions=instructions,
        tools=[check_availability, end_call]
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
        logger.debug(f"💬 {event.item.role}: {event.item.text_content[:80]}...")

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:
            update_activity()
            logger.info(f"🎤 User: {event.transcript}")

    # Register webhook callback
    async def send_completion_webhook():
        logger.info("📤 Sending completion webhook...")
        tracker.lead_name = lead_name

        # Add debug info
        tracker.debug_logs = {
            "agent_name": "elsa-swedish",
            "voice": "marin",
            "language": "sv",
            "prompt_file": "Prompts/swedish_agent_prompt.md",
            "lead_name_extracted": lead_name,
            "phone_number_extracted": phone_number
        }

        await send_webhook(tracker)

    ctx.add_shutdown_callback(send_completion_webhook)

    # Start session
    await session.start(room=ctx.room, agent=agent)

    # Wait 0.5s for SIP participant to be fully ready before greeting
    logger.info("⏳ Waiting 0.5s for SIP participant to be ready...")
    await asyncio.sleep(0.5)

    # Send greeting after delay
    greeting = f"Hej, jag heter Elsa från Finn AI. Pratar jag med {lead_name}?"
    logger.info(f"👋 Sending greeting: {greeting}")
    await session.generate_reply(
        instructions=f"Säg hälsningen på svenska: '{greeting}' och vänta på svar."
    )
    logger.info("✅ Greeting sent")

    # Start recording if enabled
    egress_id = None
    if ENABLE_RECORDING:
        try:
            logger.info("🎙️ Starting call recording...")
            egress_request = api.RoomCompositeEgressRequest(
                room_name=ctx.room.name,
                audio_only=True,
                file_outputs=[
                    api.EncodedFileOutput(
                        file_type=api.EncodedFileType.OGG,
                        filepath=f"recordings/{ctx.room.name}.ogg"
                    )
                ]
            )
            egress = await ctx.api.egress.start_room_composite_egress(egress_request)
            egress_id = egress.egress_id
            logger.info(f"✅ Recording started: {egress_id}")
        except Exception as e:
            logger.error(f"❌ Failed to start recording: {e}")

    # Send recording info to webhook when call ends
    async def send_recording_webhook():
        if egress_id and RECORDING_WEBHOOK_URL:
            try:
                logger.info(f"📤 Fetching recording info for egress: {egress_id}")

                # Get egress info to retrieve download URL
                egress_info = await ctx.api.egress.list_egress(room_name=ctx.room.name)

                recording_url = None
                for egress_item in egress_info:
                    if egress_item.egress_id == egress_id:
                        # Extract file URL from egress info
                        if egress_item.file_results:
                            recording_url = egress_item.file_results[0].download_url
                        break

                payload = {
                    "call_id": ctx.room.name,
                    "egress_id": egress_id,
                    "recording_url": recording_url,
                    "duration_seconds": tracker.get_duration(),
                    "timestamp": int(asyncio.get_event_loop().time())
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        RECORDING_WEBHOOK_URL,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            logger.info("✅ Recording webhook sent successfully")
                        else:
                            logger.error(f"❌ Recording webhook failed: {resp.status}")
            except Exception as e:
                logger.error(f"❌ Error sending recording webhook: {e}")

    if ENABLE_RECORDING:
        ctx.add_shutdown_callback(send_recording_webhook)

    # CRITICAL: Handle when user hangs up the phone
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info(f"📞 Participant disconnected: {participant.identity} (kind: {participant.kind})")

        # If a SIP participant (phone user) disconnects, delete the room immediately
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            logger.warning("🔚 SIP participant hung up - deleting room to stop billing")

            async def cleanup_room():
                try:
                    await ctx.api.room.delete_room(
                        api.DeleteRoomRequest(room=ctx.room.name)
                    )
                    logger.info("✅ Room deleted after SIP disconnect")
                except Exception as e:
                    logger.error(f"❌ Failed to delete room: {e}")

            asyncio.create_task(cleanup_room())

    # NOTE: Greeting is now sent immediately after session.start (see above)
    # Old track_subscribed waiting pattern removed - caused delays and silence


if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="elsa-swedish"
    ))
