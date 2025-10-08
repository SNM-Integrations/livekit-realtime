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

logger = logging.getLogger("finn-ai-english")

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
        "end_time": tracker.end_time
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

        # Get current UK time - FRESH FOR EACH CALL
        current_datetime = datetime.now(ZoneInfo("Europe/London"))

        # Format date for English prompt
        current_date_str = current_datetime.strftime("%A, %B %d, %Y")
        current_time_str = current_datetime.strftime("%H:%M")

        logger.info(f"📅 Agent created with date: {current_date_str} {current_time_str}")

        # System prompt - ENGLISH VERSION - 2025-10-06
        instructions = f"""
# ROLE & OBJECTIVE

You are Elsa, a meeting scheduler from Finn AI. You're calling {self.lead_name} who filled out a form 30 seconds ago to test AI voice assistants.

**Today's date:** {current_date_str} at {current_time_str}

**Phone number you're calling:** {self.phone_number}

**Primary goal:** Book a demo meeting with the founders (Nils or Samuel) if the customer is interested.

**Success = Meeting booked OR friendly close if not interested.**

---

# PERSONALITY & TONE

**Persona:** 30-year-old experienced salesperson, Scandinavian, confident but not pushy.

**You're aware you're AI** - that's your strength. Be a bit confident about the product (AI for phone calls).

**Energy level:** Calm professionalism. NOT an intern, NOT over-enthusiastic.

**Tone:**
- Friendly but NOT over-excited
- Confident but NOT aggressive or smug
- Curious but NOT overly enthusiastic
- LISTEN actively - respond to what the person says

**Length:** Max 1-2 sentences per turn. This is a phone call, not text-based.

---

# CONTEXT

**About Finn AI:**
- Company: Finn AI (sells AI voice assistants for businesses)
- Founders: Nils and Samuel
- Product examples: Inbound assistants, outbound agents, AI receptionists

**About meetings:**
- Booked with: One of the founders (you don't know who yet - NEVER say a specific name)
- Format: 30-min, shows how AI works and checks if there's a solution that adds value for the customer's business
- Purpose: Show how AI can be adapted to the customer's operations

**About the call:**
- They know you're calling (filled out form)
- They're curious about AI for phone calls
- You're one of the products they want to test

**CRITICAL RULE - Grounding:**
- NEVER make up colleague names, prices, products or features
- When uncertain: "That's something we'll cover in the meeting"

---

# REFERENCE PRONUNCIATIONS

**English spellings:**
- @ = at sign
- . = dot
- Times: 24-hour format (14:00, 10:30)

---

# TOOLS

**Available tools:**

## check_availability
**Purpose:** Check calendar availability for meetings
**When:** After customer mentioned a day/time period (Monday, this week, etc.)
**Format:** ISO 8601 datetime with timezone (e.g., "2025-10-07T09:00:00.000+02:00")
**How:** Send start_datetime and end_datetime to get ALL available times in that range
**Example:** For "Tuesday" → send Tuesday 09:00 to Tuesday 17:00
**Important:** Talk while waiting ("Let me check the calendar...") - tool gives status updates

## end_call
**Purpose:** End the call properly
**When:** After natural conclusion (meeting booked OR customer declined)
**Important:** Say goodbye FIRST, call THEN

---

# INSTRUCTIONS

## Conversational speech (critical)

You're on the phone. NOT formal text.

**Techniques:**
- Softeners: "Well", "So", "I mean" (sparingly)
- Fillers: "you know", "right", "kind of", "sort of" (sparingly)
- Tag questions: "...right?", "...yeah?" (sometimes)
- Natural questions: "Couldn't you...", "Wouldn't that..."

**Avoid:**
- Over-enthusiasm: "Great!", "Oh!", "Wow!" with lots of exclamation marks
- Formal constructions: "Tell me - what do you do day-to-day?"
- Too many fillers in a row

**Tone examples:**
- WRONG tone: "Would it be worth having a look?" (distant)
- RIGHT tone: Personal, direct question like "would that be interesting for you?"

## Conversation rules

**ONE question per turn.**
- NEVER two questions simultaneously
- 1 turn = 1 reaction/statement + MAX 1 question
- LISTEN to the answer before next question

**Build your own sentences.**
- Use your own phrasing every time
- Adapt to what the customer actually said
- DON'T follow script slavishly

**React to the customer.**
- If they say something unexpected: react first, then continue
- If they ask a question: answer first, then return to flow
- If they show interest early: adjust the pace

---

# CONVERSATION FLOW

## Preferred Flow (when conversation flows naturally)

**Phase 2: Voice feedback**
- Purpose: Keep conversation alive, be a bit confident about the product
- Ask if it's their first time with AI on the phone
- Ask what they think of your voice
- End with confident but playful confirmation
- IF negative response: Mention you have several voices to choose from
- IF positive: Brief acknowledgement, move on
- Duration: Max 20 seconds

**Phase 3: Understand business**
- Purpose: Find out what they do
- Ask conversationally what they do day-to-day (spoken language)
- Listen to answer
- Ask ONE follow-up question based on situation:
  - Leads/marketing → how quickly do they call up leads?
  - Meetings/service → who takes calls when they're busy?
  - Out on jobs → how do they handle calls then?

**Phase 4: Use case**
- Ask if they had a use case in mind when they filled out the form
- Listen carefully

**Phase 5: Pitch**
- IF they HAVE use case: Build on their idea, ask if they want to know more
- IF they DON'T have: Pitch concrete solution based on their industry
  - Use conversational techniques
  - Base on what they actually said
  - Ask if they want to know more about the AI voice

**Phase 6: Book meeting**
- Pitch the meeting: Brief demo with one of the founders, see how the product works
- Ask if that would be interesting
- If yes → continue with booking (see Tool Usage below)
- If hesitant: Show understanding, reframe (not sales, just understand AI voices)
- If no: Ask ONCE why, then friendly close

## Override Rules (HIGHEST PRIORITY)

**USER INTENT > FLOW**

**IF user says:**
- "I want to book a meeting" → Jump directly to Phase 6
- "Not interested" → Ask once why, then close
- Asks question → Answer, return to where you were
- Explains business without you asking → Skip Phase 3

**Flexibility:**
- Preferred flow = GPS route
- User intent = traffic accident requiring detour
- If customer jumps straight to booking → follow their lead
- If customer already explained business → skip discovery
- Return to flow if still relevant

---

# TOOL USAGE - BOOKING PROCESS

## Step 1: Pitch the meeting
- When customer shows interest
- Explain: "Brief meeting with one of the founders, show how the product works and adapt to your business"
- Ask if interesting

## Step 2: Choose day
- Ask about day preference (Monday/Tuesday/etc.)
- DON'T call check_availability yet
- Let customer choose day first

## Step 3: Check calendar
- When customer chose day: Say "Let me check the calendar..."
- Convert their day to ISO 8601 datetime window (e.g., Tuesday 9:00 to 17:00)
- CALL check_availability(start_datetime="2025-10-08T09:00:00.000+02:00", end_datetime="2025-10-08T17:00:00.000+02:00")
- Tool gives automatic status updates while running
- Function returns ALL available times in the range
- Response comes within 3-5 seconds now (faster than before)

## Step 4: Present times
- Based on calendar response, give 2-3 alternatives
- Let customer choose

## Step 5: Collect email
- Ask for email, ask them to speak clearly
- SPELL the ENTIRE email character by character:
  - "n-i-l-s dot w-a-l-l-i-n at gmail dot com"
- Include "dot", "at", numbers etc.
- Ask "Is that correct?"
- If wrong: Ask which part, correct

## Step 6: Confirm & close
- Summarise meeting: day, date, time, email
- Ask if anything else they want to know
- If no: Friendly thank for the call and CALL end_call(reason="Meeting booked")
- If questions: Answer, return to question about anything else

---

# SAFETY & ESCALATION

**Technical problems:**
- If check_availability returns error AND available_slots is empty: "Calendar's not responding right now. Feel free to suggest a time and we'll confirm it."
- IMPORTANT: NEVER say the tool is broken while it's still loading - wait for actual response
- Uncertain about detail: "That's something we'll cover in the meeting"

**No interest:**
- Respect customer's decision
- Brief, friendly close without being bitter
- CALL end_call(reason="Customer declined")

**Time constraints:**
- If customer says "make it quick" (e.g. driving): Faster pace, skip voice feedback if necessary
- If long call without progress: Offer meeting, close friendly if no

---

# FINAL REMINDER

You're a **skilled salesperson who happens to be AI**, not **an AI reading a sales script**.

Every sentence:
1. Direct response to what person said
2. Spoken language (not written language)
3. Max one question per turn
"""

        self.agent = Agent(
            instructions=instructions,
            llm=openai.realtime.RealtimeModel(
                model="gpt-realtime",
                voice="alloy",
                modalities=["audio", "text"],  # FIXED: audio first for speech output
                temperature=0.7,
                turn_detection=TurnDetection(
                    type="server_vad",
                    threshold=0.5,
                    prefix_padding_ms=600,
                    silence_duration_ms=1800
                ),
                input_audio_transcription=InputAudioTranscription(
                    model="whisper-1",  # FIXED: use whisper-1 like working template
                    language="en",
                    prompt="""English UK business call transcription. Context:
- Meeting booking for AI voice assistant demo
- Email addresses with British names (common: Smith, Jones, Williams, Brown, Taylor)
- Times in 24-hour format (14:00, 10:30)
- Days: Monday, Tuesday, Wednesday, Thursday, Friday
- Business terminology: meeting, demo, AI voice, leads, customers
Transcribe with high accuracy, interpret phonetic spelling contextually."""
                )
            ),
            # Register function tools: calendar checking and call ending
            tools=[check_availability, end_call]
        )

        logger.info(f"🤖 Elsa agent initialized for {self.lead_name}")


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
    from datetime import datetime
    from zoneinfo import ZoneInfo
    current_datetime = datetime.now(ZoneInfo("Europe/London"))
    current_date_str = current_datetime.strftime("%A, %B %d, %Y")
    current_time_str = current_datetime.strftime("%H:%M")

    instructions = f"""
# ROLE & OBJECTIVE

You are Elsa, a meeting scheduler from Finn AI. You're calling {lead_name} who filled out a form 30 seconds ago to test AI voice assistants.

**Your ONLY goal:** Book a meeting for next week to demo the AI voice assistant technology.

# CURRENT CONTEXT
- Date: {current_date_str}
- Time: {current_time_str}
- Lead name: {lead_name}
- They just filled the form asking about AI voice tech

# CONVERSATION STYLE
- Natural, friendly UK English
- Brief responses (1-2 sentences max)
- No scripts - adapt to conversation flow
- Professional but warm tone
"""

    logger.info(f"✅ Loaded English agent instructions")

    # Create session WITH LLM (working template pattern)
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime",
            voice="alloy",
            modalities=["audio", "text"],  # FIXED: audio first for speech output
            temperature=0.7,
            input_audio_transcription=InputAudioTranscription(
                model="whisper-1",  # FIXED: use whisper-1 like working template
                language="en",
                prompt="English UK conversation with AI assistant Elsa"
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
        await send_webhook(tracker)

    ctx.add_shutdown_callback(send_completion_webhook)

    # Start session
    await session.start(room=ctx.room, agent=agent)

    # Wait 0.5s for SIP participant to be fully ready before greeting
    logger.info("⏳ Waiting 0.5s for SIP participant to be ready...")
    await asyncio.sleep(0.5)

    # Send greeting after delay
    greeting = f"Hello, my name is Elsa from Finn AI. Am I speaking with {lead_name}?"
    logger.info(f"👋 Sending greeting: {greeting}")
    await session.generate_reply(
        instructions=f"Say this greeting in English: '{greeting}' and wait for response."
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

    # Wait for SIP participant's audio track to be ready before greeting
    # This ensures the caller can hear the full greeting
    sip_track_ready = asyncio.Event()
    greeting_sent = False

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant
    ):
        nonlocal greeting_sent

        logger.info(f"🎵 Track subscribed: {track.kind} from {participant.identity} (kind: {participant.kind})")

        # Check if this is an audio track from a SIP participant
        if (participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP and
            track.kind == rtc.TrackKind.KIND_AUDIO and
            not greeting_sent):
            logger.info("✅ SIP participant audio track ready - triggering greeting!")
            sip_track_ready.set()

    # Wait for SIP participant's audio track (max 10 seconds timeout)
    try:
        await asyncio.wait_for(sip_track_ready.wait(), timeout=10.0)
        logger.info("📞 SIP audio track ready, waiting 500ms for audio path stabilization")

        # Small delay to ensure audio path is fully established
        await asyncio.sleep(0.5)

        greeting_sent = True
        greeting = f"Hello, my name is Elsa from Finn AI. Am I speaking with {lead_name}?"
        logger.info(f"👋 Sending greeting: {greeting}")

        await session.generate_reply(
            instructions=f"Say EXACTLY this greeting in English: '{greeting}'. Say NOTHING else."
        )

        logger.info("✅ Greeting sent. Natural conversation flow active.")

    except asyncio.TimeoutError:
        logger.warning("⏱️ Timeout waiting for SIP audio track - sending greeting anyway")
        greeting_sent = True
        greeting = f"Hello, my name is Elsa from Finn AI. Am I speaking with {lead_name}?"
        await session.generate_reply(
            instructions=f"Say EXACTLY this greeting in English: '{greeting}'. Say NOTHING else."
        )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="elsa-english"
    ))
