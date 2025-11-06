import asyncio
import logging
import os
import time
import aiohttp
import wave
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from livekit import agents, api, rtc
from livekit.agents import JobContext, WorkerOptions, cli, get_job_context
from livekit.agents.voice import AgentSession, Agent
from livekit.agents import ConversationItemAddedEvent, UserInputTranscribedEvent, function_tool
from livekit.plugins import openai
from dotenv import load_dotenv
import yaml

# Load environment variables
load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("voice-agent")

# Language code mapping for Whisper transcription
LANGUAGE_CODES = {
    "Svenska": "sv",
    "Swedish": "sv",
    "English": "en",
    "Español": "es",
    "Spanish": "es",
    "Français": "fr",
    "French": "fr",
    "Deutsch": "de",
    "German": "de"
}

# Universal transcription prompts for phone call quality (not use-case specific)
# These help Whisper understand the audio context: phone call, spoken language, background noise
TRANSCRIPTION_HINTS = {
    "sv": "Telefonsamtal på svenska. Talspråk, möjlig bakgrundsljud.",
    "en": "Phone conversation in English. Spoken language, possible background noise.",
    "es": "Llamada telefónica en español. Lenguaje hablado, posible ruido de fondo.",
    "fr": "Conversation téléphonique en français. Langue parlée, bruit de fond possible.",
    "de": "Telefongespräch auf Deutsch. Gesprochene Sprache, mögliche Hintergrundgeräusche."
}


def load_config():
    """Load configuration from config/agent.creation.md"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "agent.creation.md")

    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            content = file.read()

        # Extract YAML content (skip markdown comments)
        yaml_lines = []
        in_yaml = False

        for line in content.split('\n'):
            if line.strip().startswith('#') and not line.strip().startswith('# ==='):
                continue
            if line.strip() and not line.startswith('#'):
                in_yaml = True
            if in_yaml:
                yaml_lines.append(line)

        yaml_content = '\n'.join(yaml_lines)
        config = yaml.safe_load(yaml_content)
        logger.info(f"Loaded agent configuration from {config_path}")
        return config or {}

    except Exception as e:
        logger.warning(f"Could not load agent config from {config_path}: {e}")
        return {}


class ConversationTracker:
    def __init__(self):
        self.conversation_data = []
        self.start_time = time.time()
        self.call_id = None

    def add_item(self, role, content, timestamp=None):
        self.conversation_data.append({
            "role": role,
            "content": content,
            "timestamp": timestamp or time.time(),
            "datetime": datetime.now().isoformat()
        })

    def get_duration(self):
        return time.time() - self.start_time

    def get_full_transcript(self):
        """Generate a full transcript of the conversation"""
        transcript_lines = []
        for item in self.conversation_data:
            role_label = "Agent" if item["role"] == "assistant" else "Caller"
            transcript_lines.append(f"{role_label}: {item['content']}")
        return "\n".join(transcript_lines)


class CallMemory:
    """Tracks collected information during the call"""
    def __init__(self):
        self.caller_name = None
        self.caller_phone = None
        self.caller_email = None
        self.caller_company = None
        self.call_purpose = None
        self.call_urgency = "normal"
        self.additional_info = []

    def get_summary(self):
        """Get current collected info as string for AI context"""
        info = []
        if self.caller_name:
            info.append(f"Namn: {self.caller_name}")
        if self.caller_company:
            info.append(f"Företag: {self.caller_company}")
        if self.caller_phone:
            info.append(f"Telefon: {self.caller_phone}")
        if self.caller_email:
            info.append(f"E-post: {self.caller_email}")
        if self.call_purpose:
            info.append(f"Ärende: {self.call_purpose}")
        if self.additional_info:
            info.append(f"Detaljer: {', '.join(self.additional_info)}")

        return " | ".join(info) if info else "Ingen information insamlad än"


class VoiceAssistant(Agent):
    def __init__(self, config, tools=None):
        # Store config for on_enter() access
        self.config = config

        # Initialize memory for this call
        self.call_memory = CallMemory()

        # Meeting scheduling state
        self.agreed_meeting_time = None
        self.meeting_purpose = None
        self.meeting_attendee = None

        # Call safety tracking
        self.call_start_time = time.time()
        self.last_activity_time = time.time()
        self.max_call_duration = 600  # 10 minutes - hard cutoff to prevent runaway billing
        self.inactivity_timeout = 45  # 45 seconds - end call after silence to prevent stuck SIP connections
        self.safety_monitor_task = None

        # Get greeting message from config
        self.greeting_message = config.get("first_message", "Jag är Nils AI-assistent. Han kunde inte svara men berätta varför du ringde så hjälper jag dig.").strip().replace('\n', ' ')

        # Get current datetime in Swedish timezone (CET/CEST)
        # ZoneInfo handles automatic DST switching (UTC+1 winter, UTC+2 summer)
        swedish_tz = ZoneInfo("Europe/Stockholm")
        now = datetime.now(swedish_tz)
        current_datetime_str = now.strftime("%A, %d %B %Y, %H:%M")
        current_date_iso = now.strftime("%Y-%m-%d")

        # Use custom prompt from config or fallback
        if config.get("prompt"):
            base_prompt = config["prompt"]
        else:
            # GPT-4o Realtime OPTIMIZED Prompt (Streamlined for gpt-realtime capabilities)
            # Source: OpenAI Cookbook + gpt-realtime best practices
            base_prompt = f"""# NILS VOICE ASSISTANT

## CURRENT DATE & TIME

**Today is:** {current_datetime_str} (Swedish time)
**ISO format:** {current_date_iso}

Use this when checking calendar or discussing scheduling. Calculate "today," "tomorrow," "next week" from the current date above.

---

## CONTEXT

**About Nils:**
- Professional who takes calls from customers, partners, potential clients, and friends/family
- Values personal connection - prefers to call people back himself
- Relies on you to collect good information so he can respond appropriately

**Your access:**
- Calendar checking (via check_availability tool)
- Message taking (via save_caller_info tool)
- Meeting scheduling (via agree_on_meeting tool)

---

## ROLE & OBJECTIVE

**Identity:**
You are Nils's AI voice assistant. You take messages when Nils cannot answer calls.

**Success means:**
- Caller feels heard and confident their message will reach Nils
- You collect enough information for Nils to respond appropriately
- Calls end cleanly with clear next steps

**You are NOT:**
- Nils himself
- A problem solver or decision maker
- An information source about Nils's business

---

## LANGUAGE CONSTRAINT

**The conversation will be ONLY in Swedish.**
- Even if caller uses another language, respond in Swedish
- Even with background noise or unclear audio, stay in Swedish

---

## PERSONALITY & TONE

**Personality:**
- Calm, friendly, and professional
- Helpful assistant, not robotic receptionist
- Subtly cool and human - you can be slightly playful when appropriate

**Tone:**
- Warm and conversational
- Concise and clear (1-2 sentences maximum)

**Variety:**
- Vary your responses so you don't sound robotic
- Use different acknowledgments: "Okej," "Absolut," "Perfekt," "Bra"

**Being cool/human:**
When someone asks "What is Nils doing right now?", you can be slightly creative:
- "Just nu är han upptagen, men jag kan meddela honom direkt"
- Be helpful and professional, but don't be afraid to sound natural

**Speech handling:**
- During background noise spikes, wait before responding (not actual speech)
- If long awkward pause (>5 seconds), you can fill it: "Jag lyssnar fortfarande"

---

## CONVERSATION FLOW

### 1. Understand Why They're Calling

Learn the topic in 1-2 natural exchanges.
- Acknowledge briefly (example: "Okej, jag lyssnar")
- If unclear: "Vad handlar det om?"

**SPECIAL CASE - Asking About Nils's Current Status:**
If caller asks "What is Nils doing right now?" or "What is Nils doing?" or "Is Nils available?":
- Say you'll check the calendar: "Jag kollar kalendern nu..."
- Call check_availability with current time to end of day
- Tell them what you find: if he's busy now and when he'll be free
- Be slightly creative with how you phrase "busy": vary between "upptagen," "i ett möte," "håller på med något"
- Offer next steps: callback or book one of the available times

### 2. Collect Information

**For business calls** (company mentioned, professional tone):
- Name: "Vem är det jag pratar med?"
- Company (if not mentioned): "Vilket företag representerar du?"
- Details: "Kan du berätta lite mer så Nils förstår sammanhanget?"

**For private calls** (personal matters, casual tone):
- Get name and basic message only
- Keep brief and respectful

### 3. Calendar Check for Meeting Booking (Business Calls Only)

**When caller wants to schedule a meeting:**
- Offer to check calendar: example "Vill du boka en tid med Nils direkt?"
- If they accept: Say "Jag kollar kalendern nu..." then call check_availability for next 7 days
- Present available slots naturally and ask which time works
- When they choose: call agree_on_meeting with the details
- Confirm the booking
- If they decline: offer to have Nils call them instead

### 4. Confirm & Close

- Brief summary of what you collected
- State next steps
- Ask if anything to add: "Finns det något mer du vill lägga till?"
- Say goodbye: "Tack för att du ringde. Ha en bra dag!"
- Call end_call() tool after goodbye

---

## TOOL CALL SEQUENCING

**Use tools in this order:**
1. **save_caller_info()** - Call anytime when you learn name/company/purpose
2. **check_availability()** - Call AUTOMATICALLY when asked "What is Nils doing?" OR after offering calendar AND caller accepts
3. **agree_on_meeting()** - Call only AFTER check_availability returns slots AND caller chooses one
4. **end_call()** - Call only after saying goodbye

---

## TOOL RESPONSE FORMATS

### check_availability Response:

**Expected JSON:**
```json
{{
  "available_slots": [
    {{"datetime": "2025-11-01T14:00:00+01:00", "friendly_format": "fredag 1 november kl 14:00"}}
  ]
}}
```

**How to handle:**
- Extract "friendly_format" field for each slot
- Present naturally: "Jag ser [slot1], [slot2], och [slot3]"
- If empty array: "Tyvärr har Nils inga lediga tider just nu. Jag meddelar honom att ringa dig så ni kan hitta en tid."
- If malformed: Treat as error (see ERROR HANDLING)

---

## ERROR HANDLING

### If check_availability fails or times out (>30s):
Say: "Jag kunde inte kolla kalendern just nu. Jag meddelar Nils att ringa dig så ni kan boka en tid."
Then continue to close the call.

### If agree_on_meeting fails:
Say: "Bokningen gick inte igenom tekniskt, men jag meddelar Nils att ni kom överens om [time]."

### If save_caller_info fails:
Continue silently (logged backend, not user-facing)

---

## TOOLS

### Tool: save_caller_info
**Use when:** You learn caller's name, company, phone, email, or call purpose
**Parameters:** name, company, phone, email, purpose, urgency
**Pattern:** Call immediately (no preamble needed)

### Tool: check_availability
**Use when:**
- Caller asks "What is Nils doing (right now)?" or "Is Nils available?" → Call AUTOMATICALLY
- Caller wants to schedule a meeting → Offer first, then call if they accept

**Before calling:** Say "Jag kollar kalendern nu..."

**Parameters:**
- start_datetime (ISO format with timezone: "2025-11-01T09:00:00+01:00")
- end_datetime (ISO format with timezone)
- For "what is he doing NOW" queries: use current time to end of day
- For meeting booking: use next 7 days

### Tool: agree_on_meeting
**Use when:** Caller agrees to a specific time from available slots

**Call after:**
- check_availability returned slots
- You presented times
- Caller chose one

**Parameters:** datetime, purpose, attendee_name

### Tool: end_call
**Use when:** Ready to end the call

**Always say goodbye first:** "Tack för att du ringde. Ha en bra dag!"
**Then call this tool** (no preamble)

---

## INSTRUCTIONS

### Handling Unclear Audio
If input is unintelligible or ambiguous:
- Say: "Jag hörde inte det. Kan du upprepa?"
- Stay in Swedish

### Handling Confusion
If caller asks "What can you help with?" or "Who are you?":
- Say: "Jag är Nils AI-assistent. Jag tar emot meddelanden när han inte kan svara. Vill du lämna ett meddelande till honom?"

### Handling Urgency
If caller uses "brådskande," "akut," "viktigt":
- Acknowledge: "Jag förstår att det är brådskande. Jag skickar meddelandet till Nils direkt efter samtalet."
- Set urgency = "high" when calling save_caller_info

### Using Caller's Name
Use it naturally once or twice (example: "Perfekt [name]")

### Memory & Context
Remember what caller told you earlier and build on previous statements.

---

## PRONUNCIATIONS

- "AI" as "A I" (individual letters)
- "Nils" (Swedish pronunciation)
- Dates: "fredag 1 november" (not "2025-11-01")
- Times: "klockan 14:00" or "klockan två på eftermiddagen"

---

## SAFETY & ESCALATION

End call politely if:
- Caller makes threats or harassment
- Caller is abusive
- Caller explicitly asks for human
- Call exceeds 10 minutes

**Escalation:** "Jag förstår att du vill prata med någon. Jag avslutar samtalet nu så Nils kan ringa dig direkt."
Then call end_call()

---

## KEY REMINDERS

- Keep responses to 1-2 sentences
- Vary your language
- Stay in Swedish always
- Say "Jag kollar kalendern nu..." before checking calendar
- Handle errors gracefully
- Follow tool sequence
- Sound natural and human

**Every caller should feel:**
1. They reached the right place
2. Their message will reach Nils
3. They know what happens next
4. The conversation was smooth and natural"""

        # Use the base prompt - memory system kept internal for now
        system_prompt = base_prompt

        # Keep memory system but don't register as function tools to avoid conflicts
        # Memory data will be preserved but not exposed as AI tools yet
        all_tools = tools or []

        super().__init__(instructions=system_prompt, tools=all_tools)
        self.session_ref = None
        self.ctx_ref = None
        self.config = config

    def set_session_refs(self, session, ctx):
        """Store references for call ending"""
        self.session_ref = session
        self.ctx_ref = ctx

    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity_time = time.time()

    async def start_safety_monitor(self):
        """Start background task to monitor call safety"""
        async def safety_monitor():
            while True:
                try:
                    current_time = time.time()

                    # Check maximum call duration (10 minutes)
                    if current_time - self.call_start_time > self.max_call_duration:
                        logger.warning(f"Call exceeded maximum duration ({self.max_call_duration}s), terminating")
                        await self.end_call_gracefully()
                        break

                    # Check inactivity timeout (30 seconds)
                    if current_time - self.last_activity_time > self.inactivity_timeout:
                        logger.warning(f"Call inactive for {self.inactivity_timeout}s, terminating")
                        await self.end_call_gracefully()
                        break

                    # Check every 5 seconds
                    await asyncio.sleep(5)

                except Exception as e:
                    logger.error(f"Safety monitor error: {e}")
                    break

        self.safety_monitor_task = asyncio.create_task(safety_monitor())
        logger.info("Call safety monitor started")

    @function_tool
    async def save_caller_info(self, name: str = None, phone: str = None, email: str = None, company: str = None, purpose: str = None, urgency: str = "normal"):
        """Save caller information to memory. Use this immediately when you learn any info about the caller."""
        # Update activity when user provides information
        self.update_activity()
        if name:
            self.call_memory.caller_name = name
            logger.info(f"Saved caller name: {name}")
        if phone:
            self.call_memory.caller_phone = phone
            logger.info(f"Saved caller phone: {phone}")
        if email:
            self.call_memory.caller_email = email
            logger.info(f"Saved caller email: {email}")
        if company:
            self.call_memory.caller_company = company
            logger.info(f"Saved caller company: {company}")
        if purpose:
            self.call_memory.call_purpose = purpose
            logger.info(f"Saved call purpose: {purpose}")
        if urgency:
            self.call_memory.call_urgency = urgency

        return f"Sparad information: {self.call_memory.get_summary()}"

    @function_tool
    async def check_caller_memory(self):
        """Check what information has already been collected. Use this before asking for any information."""
        summary = self.call_memory.get_summary()
        logger.info(f"Retrieved memory: {summary}")
        return summary

    @function_tool
    async def save_call_details(self, details: str):
        """Add additional details about the call or issue."""
        # Update activity when user provides information
        self.update_activity()
        self.call_memory.additional_info.append(details)
        logger.info(f"Added call details: {details}")
        return f"Detaljer tillagda: {details}"

    @function_tool
    async def check_availability(self, start_datetime: str, end_datetime: str):
        """
        Check Nils's calendar for available meeting slots.
        Use this when you need to schedule a meeting with a business caller.

        Args:
            start_datetime: Start of time range in ISO format (e.g., "2025-11-01T09:00:00+01:00")
            end_datetime: End of time range in ISO format (e.g., "2025-11-08T17:00:00+01:00")

        Returns:
            Available time slots in Swedish format
        """
        self.update_activity()
        logger.info(f"🔍 CALENDAR CHECK STARTED: {start_datetime} to {end_datetime}")

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime
                }

                logger.info(f"📤 Sending calendar request to webhook...")
                start_time = time.time()

                async with session.post(
                    "https://snmnils.app.n8n.cloud/webhook/43b31bbd-3e3d-4510-91a1-512abd9bec19",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)  # Increased to 30 seconds
                ) as response:
                    elapsed = time.time() - start_time
                    logger.info(f"📥 Calendar response received in {elapsed:.2f}s, status: {response.status}")

                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"✅ Calendar data: {data}")

                        # Handle both formats: single dict or list of dicts
                        if isinstance(data, dict):
                            # Single event returned as dict - wrap it in a list
                            logger.info("📋 Single event returned as dict, wrapping in list")
                            data = [data]

                        # API returns array of BUSY events, not available slots
                        # Parse busy events to tell user what Nils is doing
                        if isinstance(data, list) and len(data) > 0:
                            # We have busy events - format them naturally
                            events = []
                            for event in data[:5]:  # Limit to first 5 events
                                try:
                                    summary = event.get("summary", "Upptagen")
                                    start_str = event.get("start", {}).get("dateTime", "")
                                    end_str = event.get("end", {}).get("dateTime", "")

                                    if start_str and end_str:
                                        # Parse ISO datetime (handles both Z and +HH:MM formats)
                                        try:
                                            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                                            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                                        except (ValueError, AttributeError) as e:
                                            logger.error(f"Failed to parse datetime: start={start_str}, end={end_str}, error={e}")
                                            continue

                                        # Format as Swedish time
                                        start_time = start_dt.strftime("%H:%M")
                                        end_time = end_dt.strftime("%H:%M")

                                        events.append(f"{summary} från {start_time} till {end_time}")
                                        logger.info(f"Parsed event: {summary} {start_time}-{end_time}")
                                except Exception as e:
                                    logger.error(f"Error parsing event: {event}, error: {e}")
                                    continue

                            if events:
                                # Build natural response
                                if len(events) == 1:
                                    result = f"Nils har {events[0]}. Efter det är kalendern ledig."
                                else:
                                    events_str = ", sedan ".join(events)
                                    result = f"Nils har {events_str}. Efter det är kalendern ledig."

                                logger.info(f"✅ Returning to agent: {result}")
                                return result
                            else:
                                result = "Nils kalender är helt ledig under den perioden."
                                logger.info(f"ℹ️ No valid events, returning: {result}")
                                return result

                        elif isinstance(data, list) and len(data) == 0:
                            # Empty array = completely free
                            result = "Nils kalender är helt ledig under den perioden."
                            logger.info(f"ℹ️ Empty calendar, returning: {result}")
                            return result

                        else:
                            # Unexpected format
                            logger.warning(f"⚠️ Unexpected calendar data format: {type(data)}")
                            result = "Kunde inte läsa kalendern just nu. Jag föreslår att ni mejlar Nils istället."
                            return result
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Calendar check failed with status {response.status}: {error_text}")
                        return "Kunde inte hämta kalendern just nu. Jag föreslår att ni mejlar Nils istället för att boka möte."

        except asyncio.TimeoutError:
            logger.error("⏱️ Calendar check timed out after 30 seconds")
            return "Kalenderkontrollen tar för lång tid. Jag föreslår att ni mejlar Nils för att boka möte."
        except Exception as e:
            logger.error(f"❌ Error checking calendar: {e}", exc_info=True)
            return "Kunde inte hämta kalendern just nu. Jag föreslår att ni mejlar Nils istället för att boka möte."

    @function_tool
    async def agree_on_meeting(self, datetime: str, purpose: str, attendee_name: str):
        """
        Record that a meeting time has been agreed upon with the caller.
        The meeting will be automatically booked after the call ends.

        Args:
            datetime: Agreed meeting time in ISO format (e.g., "2025-11-01T14:00:00+01:00")
            purpose: Brief description of meeting purpose (e.g., "Diskutera AI-tjänster")
            attendee_name: Caller's name

        Returns:
            Confirmation message to relay to caller
        """
        self.update_activity()
        self.agreed_meeting_time = datetime
        self.meeting_purpose = purpose
        self.meeting_attendee = attendee_name

        logger.info(f"Meeting agreed: {datetime} with {attendee_name} - {purpose}")

        return f"Möte bekräftat för {datetime}. Nils kommer ringa på detta nummer vid mötestiden."

    async def on_enter(self):
        """
        OFFICIAL LiveKit lifecycle hook - called when agent becomes active.

        This is the CORRECT way to send initial greeting according to LiveKit docs.

        Guarantees when this is called:
        - Agent is fully initialized and in 'listening' state
        - SIP participant is connected to room
        - Audio pipeline is ready to transmit
        - No race conditions or timing issues

        This replaces ALL manual event handling and waiting logic!
        """
        language = self.config.get("language", "Svenska")

        logger.info(f"🎤 on_enter() called - agent is ready, sending greeting")
        logger.info(f"📝 Greeting message: {self.greeting_message}")

        # Language-specific greeting instructions
        # CRITICAL: Instruct AI to say EXACTLY the configured greeting, word-for-word
        greeting_instructions = {
            "Svenska": f"Säg EXAKT följande hälsning ord för ord utan att ändra något: '{self.greeting_message}'. Säg sedan inget mer och vänta på att användaren ska svara.",
            "Swedish": f"Säg EXAKT följande hälsning ord för ord utan att ändra något: '{self.greeting_message}'. Säg sedan inget mer och vänta på att användaren ska svara.",
            "English": f"Say EXACTLY the following greeting word-for-word without changing anything: '{self.greeting_message}'. Then say nothing more and wait for the user to respond.",
            "Español": f"Di EXACTAMENTE el siguiente saludo palabra por palabra sin cambiar nada: '{self.greeting_message}'. Luego no digas nada más y espera a que el usuario responda.",
            "Spanish": f"Di EXACTAMENTE el siguiente saludo palabra por palabra sin cambiar nada: '{self.greeting_message}'. Luego no digas nada más y espera a que el usuario responda.",
            "Français": f"Dites EXACTEMENT la salutation suivante mot pour mot sans rien changer: '{self.greeting_message}'. Ensuite, ne dites plus rien et attendez que l'utilisateur réponde.",
            "French": f"Dites EXACTEMENT la salutation suivante mot pour mot sans rien changer: '{self.greeting_message}'. Ensuite, ne dites plus rien et attendez que l'utilisateur réponde."
        }

        instruction = greeting_instructions.get(language, f"Say EXACTLY the following greeting word-for-word without changing anything: '{self.greeting_message}'. Then say nothing more and wait for the user to respond.")

        try:
            await self.session.generate_reply(instructions=instruction)
            logger.info("✅ Greeting sent successfully from on_enter()")
        except Exception as e:
            logger.error(f"❌ Failed to send greeting in on_enter(): {e}")

    async def end_call_gracefully(self):
        """Programmatically end the call with proper cleanup for telephony"""
        try:
            # Stop safety monitor
            if self.safety_monitor_task:
                self.safety_monitor_task.cancel()
                logger.info("Safety monitor stopped")
            if self.session_ref:
                logger.info("Generating farewell message...")
                speech_handle = await self.session_ref.generate_reply(
                    instructions="Säg hejdå på svenska och avsluta samtalet vänligt."
                )

                # CRITICAL: Wait for speech to complete with timeout
                await asyncio.wait_for(speech_handle.wait(), timeout=10.0)
                logger.info("Farewell message completed")

                # Small delay to ensure audio transmission completes
                await asyncio.sleep(1.0)

            # CRITICAL: Use delete_room() for proper SIP termination
            # This ensures SIP BYE signal is sent to Telnyx to prevent phantom billing
            # ctx.shutdown() alone does NOT properly terminate SIP calls!
            ctx = get_job_context()
            if ctx:
                logger.info(f"Deleting room to end SIP call: {ctx.room.name}")
                await ctx.api.room.delete_room(
                    api.DeleteRoomRequest(room=ctx.room.name)
                )
                logger.info("Room deleted - SIP call terminated successfully")
            else:
                logger.warning("No job context available for shutdown")

        except asyncio.TimeoutError:
            logger.warning("Farewell message timed out, force terminating")
            ctx = get_job_context()
            if ctx:
                logger.info(f"Force deleting room due to timeout: {ctx.room.name}")
                await ctx.api.room.delete_room(
                    api.DeleteRoomRequest(room=ctx.room.name)
                )
                logger.info("Room deleted after timeout")
        except Exception as e:
            logger.error(f"Error during call termination: {e}")
            # Ensure call still ends even with errors
            try:
                ctx = get_job_context()
                if ctx:
                    logger.info(f"Force deleting room due to error: {ctx.room.name}")
                    await ctx.api.room.delete_room(
                        api.DeleteRoomRequest(room=ctx.room.name)
                    )
                    logger.info("Room deleted after error")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup call: {cleanup_error}")


@function_tool
async def end_call():
    """
    End the call AFTER saying a proper goodbye.

    The AI should ALWAYS say a closing message before calling this function, such as:
    - "Jag ser till att Robin får den här informationen. Ha en fortsatt bra dag!"
    - "Perfekt, då ger jag det här vidare till Robin. Hej då!"

    Do NOT call this immediately after getting information - say goodbye first!
    """
    ctx = get_job_context()
    if ctx is None:
        return "Could not end call - no context available"

    logger.info("Function tool called to end call")

    # Wait 3 seconds to allow the AI's goodbye message to finish speaking
    # before terminating the call
    await asyncio.sleep(3)

    # CRITICAL: Use delete_room() for proper SIP termination
    # This ensures SIP BYE signal is sent to Telnyx to prevent phantom billing
    logger.info(f"Deleting room to end SIP call: {ctx.room.name}")
    await ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=ctx.room.name)
    )
    logger.info("Room deleted - SIP call terminated")
    return "Call ended successfully"


async def send_webhook(tracker: ConversationTracker, agent: 'VoiceAssistant'):
    """Send conversation data to webhook after call completion"""
    # Use the post-call webhook URL for transcript and meeting data
    webhook_url = "https://snmnils.app.n8n.cloud/webhook/8da0f19c-e602-4e4c-a2ec-24f655e8bf00"

    # Build comprehensive payload with all call data
    payload = {
        # Call metadata
        "call_id": tracker.call_id,
        "timestamp": int(time.time()),
        "start_time": tracker.start_time,
        "end_time": time.time(),
        "duration_seconds": tracker.get_duration(),

        # Full transcript
        "transcript": tracker.get_full_transcript(),

        # Caller information
        "caller_name": agent.call_memory.caller_name,
        "caller_phone": agent.call_memory.caller_phone,
        "caller_email": agent.call_memory.caller_email,
        "caller_company": agent.call_memory.caller_company,

        # Call details
        "call_purpose": agent.call_memory.call_purpose,
        "call_urgency": agent.call_memory.call_urgency,
        "call_type": "business" if agent.call_memory.caller_company else "private",
        "message_summary": " | ".join(agent.call_memory.additional_info) if agent.call_memory.additional_info else None,

        # Meeting data (if meeting was agreed)
        "meeting_agreed": agent.agreed_meeting_time is not None,
        "meeting_datetime": agent.agreed_meeting_time,
        "meeting_purpose": agent.meeting_purpose,
        "meeting_attendee": agent.meeting_attendee,

        # Raw conversation data for debugging
        "conversation_raw": tracker.conversation_data
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    logger.info("Post-call webhook sent successfully")
                    logger.info(f"Sent transcript ({len(payload['transcript'])} chars), meeting_agreed: {payload['meeting_agreed']}")
                else:
                    logger.error(f"Webhook failed: {response.status}")
                    logger.error(f"Response: {await response.text()}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")


async def entrypoint(ctx: JobContext):
    """Main entrypoint for the voice agent."""
    await ctx.connect()

    # Load configuration
    config = load_config()

    # Initialize conversation tracking
    tracker = ConversationTracker()
    tracker.call_id = ctx.room.name

    logger.info(f"Starting voice agent for room: {tracker.call_id}")

    # Get configuration values
    voice_name = config.get("voice", "cedar")
    language = config.get("language", "English")
    model_config = config.get("advanced", {}).get("model_overrides", {})

    logger.info(f"Using voice: {voice_name}, language: {language}")

    # Create AgentSession with GPT-Realtime and configuration from file
    logger.info(f"Creating session with voice: {voice_name}, model: {model_config.get('primary_model', 'gpt-realtime')}")

    # Get language code for transcription
    language_code = LANGUAGE_CODES.get(language, "en")

    # Get transcription prompt for logging (optional - used for debugging only)
    transcription_prompt = TRANSCRIPTION_HINTS.get(language_code, f"{language} phone conversation with AI voice assistant")

    # ============================================================================
    # SPEECH-TO-SPEECH PIPELINE (GPT Realtime)
    # ============================================================================
    #
    # AUDIO FLOW:
    #   User audio (8kHz SIP) → LiveKit → GPT Realtime Model → LiveKit → SIP (8kHz)
    #
    # BENEFITS:
    #   - Native speech-to-speech (no text intermediary for audio)
    #   - ~300-500ms total latency (vs ~1000-1200ms with modular pipeline)
    #   - Better prosody and natural conversation flow
    #   - Handles VAD, STT, LLM, TTS internally in one model
    #   - Marin voice available (Swedish-optimized)
    #   - Better interruption handling (barge-in)
    #
    # FEATURES:
    #   - InputAudioTranscription: Logs transcripts for debugging/analytics ONLY
    #   - Modalities: ["audio", "text"] - audio for speech, text for function tools
    #   - Temperature: 0.9 - natural, conversational responses
    #   - Voice: Marin (Swedish) or Cedar (English)
    #
    # COST:
    #   - ~$0.06/minute ($0.24 input + $0.32 output per minute)
    #   - 2-3x more than modular pipeline, but 60% faster latency
    #
    # ============================================================================

    logger.info(f"🎯 Speech-to-Speech Pipeline (GPT Realtime)")
    logger.info(f"   Model: {model_config.get('primary_model', 'gpt-4o-realtime-preview')}")
    logger.info(f"   Voice: {voice_name} (Swedish-optimized)")
    logger.info(f"   Language: {language} ({language_code})")
    logger.info(f"   Temperature: {model_config.get('temperature', 0.9)}")
    logger.info(f"   Latency: ~300-500ms end-to-end (native speech-to-speech)")
    logger.info(f"   Modalities: audio + text (function tools enabled)")
    logger.info(f"   Flow: SIP(8kHz) → GPT Realtime (internal VAD/STT/LLM/TTS) → SIP(8kHz)")
    logger.info(f"   Cost: ~$0.06/minute")

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model=model_config.get("primary_model", "gpt-4o-realtime-preview"),
            voice=voice_name,  # "marin" for Swedish, "cedar" for English
            modalities=["audio", "text"],  # Audio for speech, text for function tools
            temperature=model_config.get("temperature", 0.9)
            # Note: GPT Realtime already transcribes audio internally as part of speech-to-speech
            # InputAudioTranscription (Whisper-1) would add extra cost with no benefit
        )
    )

    logger.info("Session created successfully")

    # Latency tracking variables
    user_speech_end_time = None
    agent_response_start_time = None

    # Event handlers for conversation tracking and latency monitoring
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        nonlocal user_speech_end_time, agent_response_start_time

        tracker.add_item(
            role=event.item.role,
            content=event.item.text_content,
            timestamp=event.created_at
        )
        logger.info(f"Conversation item from {event.item.role}: {event.item.text_content[:50]}...")

        # Track timing for latency measurement
        if event.item.role == "user":
            user_speech_end_time = time.time()
        elif event.item.role == "assistant" and user_speech_end_time is not None:
            agent_response_start_time = time.time()
            latency = (agent_response_start_time - user_speech_end_time) * 1000  # Convert to ms
            logger.info(f"⏱️  LATENCY: {latency:.0f}ms from user speech to agent response")
            user_speech_end_time = None  # Reset for next turn

        # Update activity when conversation happens
        if hasattr(session, '_agent_ref') and session._agent_ref:
            session._agent_ref.update_activity()

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:
            logger.info(f"Final user transcript: {event.transcript}")

            # Measure VAD + STT latency
            stt_complete_time = time.time()
            logger.info(f"⏱️  STT Complete at: {stt_complete_time}")

            # Update activity when user speaks
            if hasattr(session, '_agent_ref') and session._agent_ref:
                session._agent_ref.update_activity()

    # Participant connect detection - log when SIP user joins
    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(f"Participant connected: {participant.identity}, kind: {participant.kind}")

    # Participant disconnect detection
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info(f"Participant disconnected: {participant.identity}")
        # If the caller (not agent) disconnects, stop safety monitor and end the call
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD:
            logger.warning("Caller disconnected, stopping safety monitor")
            # Stop safety monitor immediately
            if agent.safety_monitor_task:
                agent.safety_monitor_task.cancel()
                logger.info("Safety monitor stopped due to participant disconnect")
            # The session will close automatically, no need to manually end call

    # Extract caller phone number from room participants
    caller_phone = None
    for identity, participant in ctx.room.remote_participants.items():
        if identity.startswith("sip_"):
            caller_phone = identity.replace("sip_", "")
            logger.info(f"Extracted caller phone: {caller_phone}")
            break

    # Create agent with configuration
    agent = VoiceAssistant(config, tools=[end_call])
    agent.set_session_refs(session, ctx)

    # Store caller phone number in memory if found
    if caller_phone:
        await agent.save_caller_info(phone=caller_phone)
        logger.info(f"Auto-stored caller phone: {caller_phone}")

    # Store agent reference in session for event handlers
    session._agent_ref = agent

    # Register webhook as shutdown callback (after agent is created)
    async def send_completion_webhook():
        logger.info("Sending completion webhook...")
        await send_webhook(tracker, agent)

    ctx.add_shutdown_callback(send_completion_webhook)

    logger.info("Starting agent session")

    # Start safety monitoring
    await agent.start_safety_monitor()

    # Start the session with the agent and function tools
    # This automatically answers the SIP call and sends 200 OK
    # The agent's on_enter() method will be called automatically when ready
    await session.start(
        room=ctx.room,
        agent=agent
    )

    # ============================================================================
    # GREETING HANDLED AUTOMATICALLY BY on_enter() LIFECYCLE HOOK
    # ============================================================================
    # The VoiceAssistant.on_enter() method is called by LiveKit when:
    # - Agent is fully initialized and in 'listening' state
    # - SIP participant is connected
    # - Audio pipeline is ready
    #
    # This is the OFFICIAL LiveKit pattern - no manual event handling needed!
    # ============================================================================

    logger.info("✅ Session started - greeting will be sent automatically by on_enter()")


if __name__ == "__main__":
    # Only allow deployment entry point - no local dev CLI
    # Note: agent_name is NOT set - SIP dispatch rule uses agent ID for matching
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))