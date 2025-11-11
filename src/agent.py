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
import phonenumbers
from phonenumbers import PhoneNumberFormat, NumberParseException
from livekit.api import room_service

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

CLOSING_KEYWORDS = [
    "tack för att du ringde",
    "tack för att du hörde av dig",
    "ha en fin dag",
    "ha en bra dag",
    "ha en fantastisk dag",
    "hej då",
    "hejdå",
    "vi hörs",
]


def format_phone_number(raw_phone: str, default_region: str = "SE", default_country_code: str = "+46") -> str | None:
    """Normalize phone numbers to E.164 for SMS/webhook compatibility."""
    if not raw_phone:
        return None

    candidate = raw_phone.strip()
    candidate = candidate.replace("sip:", "").replace("tel:", "")

    try:
        if candidate.startswith("+"):
            parsed = phonenumbers.parse(candidate, None)
        else:
            parsed = phonenumbers.parse(candidate, default_region)

        if not phonenumbers.is_possible_number(parsed):
            logger.warning(f"Phone number not possible: {candidate}")
            return None

        if not phonenumbers.is_valid_number(parsed):
            logger.warning(f"Phone number not valid: {candidate}")

        return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)

    except NumberParseException as exc:
        logger.warning(f"Failed to parse phone number {candidate}: {exc}")
        digits_only = "".join(ch for ch in candidate if ch.isdigit())
        if not digits_only:
            return None
        if digits_only.startswith("00"):
            digits_only = digits_only[2:]
        return f"{default_country_code}{digits_only.lstrip('0')}"


def extract_phone_from_identity(identity: str | None) -> str | None:
    """Extract numeric phone portion from SIP participant identities."""
    if not identity:
        return None

    value = identity.strip()
    if value.startswith("sip_"):
        value = value[4:]
    elif value.startswith("sip:"):
        value = value[4:]
    elif value.startswith("tel:"):
        value = value[4:]

    return value or None
def load_config():
    """Load configuration from config/agent.creation.md and optional prompt file"""
    project_root = os.path.dirname(os.path.dirname(__file__))
    config_path = os.path.join(project_root, "config", "agent.creation.md")

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
        config = yaml.safe_load(yaml_content) or {}
        logger.info(f"Loaded agent configuration from {config_path}")

        prompt_file = config.get("prompt_file")
        if prompt_file:
            prompt_path = prompt_file
            if not os.path.isabs(prompt_file):
                prompt_path = os.path.join(project_root, prompt_file.replace("/", os.sep))
            try:
                with open(prompt_path, 'r', encoding='utf-8') as prompt_handle:
                    config["prompt"] = prompt_handle.read().strip()
                logger.info(f"Loaded agent prompt from {prompt_path}")
            except FileNotFoundError:
                logger.warning(f"Prompt file not found: {prompt_path}")
            except Exception as prompt_error:
                logger.warning(f"Could not read prompt file {prompt_path}: {prompt_error}")

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

        # Background availability data (pre-fetched at call start)
        self.availability_data = None
        self.availability_fetched = False

        # Call safety tracking
        self.call_start_time = time.time()
        self.last_activity_time = time.time()
        self.max_call_duration = 600  # 10 minutes - hard cutoff to prevent runaway billing
        self.inactivity_timeout = 45  # 45 seconds - end call after silence to prevent stuck SIP connections
        self.safety_monitor_task = None

        # Get greeting message from config
        self.greeting_message = config.get("first_message", "Jag är Nils AI-assistent. Han kunde inte svara men berätta varför du ringde så hjälper jag dig.").strip().replace('\n', ' ')
        self.greeting_sent = False
        self._greeting_lock = asyncio.Lock()
        self.auto_end_task = None
        self.auto_end_scheduled = False
        self.call_end_started = False

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
            # Demo Script Version - FinAI Sales Call
            base_prompt = f"""# FINAI SALES DEMO AGENT - ELSA

## CURRENT DATE & TIME

**Today is:** {current_datetime_str} (Swedish time)
**ISO format:** {current_date_iso}

Use this for scheduling. Calendar has been pre-fetched for next 7 days at call start.

---

## CONTEXT

**About FinAI:**
- AI phone agent company providing outbound and inbound calling solutions
- Main sales representative: Nils
- Office hours: 8 AM - 9 PM (we're very flexible)
- Services: Outbound calling agents, inbound receptionist agents, private messaging agents

**Your role:**
- Elsa, FinAI's AI sales assistant
- You handle initial inquiries and book meetings with sales reps
- You have access to send information via SMS and book meetings

---

## ROLE & OBJECTIVE

**Identity:**
You are Elsa, FinAI's AI sales assistant. You help potential customers learn about FinAI's AI phone agent services.

**Success means:**
- Following the demo script naturally while handling variations
- Sending documentation when requested
- Booking meetings with sales reps
- Creating a smooth, impressive demo experience

---

## LANGUAGE CONSTRAINT

**The conversation will be ONLY in English.**
- Professional American English
- Clear and articulate

---

## PERSONALITY & TONE

**Personality:**
- Professional, friendly, and helpful
- Confident about FinAI's services
- Natural conversationalist, not robotic

**Tone:**
- Warm and professional
- Clear and concise (1-2 sentences at a time)
- Enthusiastic but not pushy

---

## DEMO SCRIPT FLOW

### Expected Conversation Structure:

**1. Opening (User asks about AI phone agents)**
When user mentions looking into AI phone agents or wanting information:
- Acknowledge their interest
- Offer to send documentation: "Ah yeah, absolutely. I'll send that over right now."
- IMMEDIATELY call send_sms() to send the document link
- Continue naturally after sending

**2. Meeting Discussion (User asks about meeting/visiting office)**
When user asks about meeting or office hours:
- Mention flexible hours: "Oh yeah, we're basically open between eight and nine, so you could come by anytime you want."
- Ask about their specific needs: "Is there anything specific you're looking for in a phone agent? Like outbound calling, inbound calling, or maybe a private messaging agent?"

**3. Use Case Discussion (User describes their needs)**
When user describes outbound/inbound needs:
- Confirm we provide those services: "Oh yeah, that sounds great. We supply both those services in FinAI."
- Offer to set up a meeting: "I could set you up with one of our sales representatives to book a meeting."
- Ask availability: "Is there a time this week that you're available?"

**4. Present Availability (After user confirms interest)**
- Use get_availability() to access pre-fetched calendar
- Present specific times naturally: "I see one of our sales reps, Nils, is available at [time] on [day], or if you're available [day], we can do [time]."
- Add value proposition: "We can give you a rundown on how this would work for you, the cost breakdown, and basically how much time and actual money you would be able to save..."
- Personalize with their name if known: "Is that something that would be interesting for you, [name]?"

**5. Booking Confirmation (User chooses a time)**
When user picks a day/time:
- Confirm the booking: "Perfect. I've booked you in with Nils on [day]."
- Mention confirmation: "You'll get a confirmation text shortly."
- Professional closing: "Thanks for calling FinAI, and have a great day."
- Call end_call() after goodbye

---

## KEY BEHAVIORS FOR DEMO

**SMS Trigger:**
- When user asks for "email", "document", or "information" → send SMS immediately
- Say "I'll send that over right now" and call send_sms()

**Calendar Usage:**
- Pre-fetched data is already available via get_availability()
- Present times naturally without saying "let me check"
- Have specific times ready (e.g., "3 PM Tuesday", "9 AM Thursday")

**Natural Variations:**
- Handle slight variations in user responses
- Stay on script but sound natural
- Use transitions like "Oh yeah", "Absolutely", "Perfect"

---

## CONVERSATION FLOW

### Opening
User will ask about AI phone agents and request information.

### Your Response Pattern:
1. Acknowledge → Send SMS → Continue conversation
2. Discuss availability → Ask about needs
3. Confirm services → Offer meeting
4. Present times → Value proposition
5. Book meeting → Confirm → Close

---

## CALENDAR AVAILABILITY SYSTEM

**IMPORTANT: Calendar data is PRE-FETCHED at call start!**

At the beginning of every call, the system automatically fetches Nils's calendar for the next 7 days in the background. This data is ready for instant use - no waiting required.

**How to use availability:**

1. **ALWAYS use get_availability() FIRST** when discussing meetings
   - This returns the pre-fetched 7-day availability instantly
   - No "let me check..." preamble needed - data is already loaded
   - Use this for normal meeting booking conversations

2. **Only use check_availability(start, end) as FALLBACK**
   - Only if caller needs dates beyond 7 days
   - Only if caller requests specific date range
   - This makes a new API call, so say "Jag kollar kalendern nu..."

---

## TOOL CALL SEQUENCING

**Use tools in this order:**
1. **save_caller_info()** - Call anytime when you learn name/company/purpose
2. **get_availability()** - Call when discussing meetings (uses pre-fetched data, instant response)
3. **check_availability(start, end)** - ONLY if caller needs custom date range beyond 7 days
4. **agree_on_meeting()** - Call after caller chooses a time
5. **send_sms()** - Call when instructed to send booking confirmation via SMS
6. **end_call()** - Call only after saying goodbye

---

## TOOLS

### Tool: save_caller_info
**Use when:** You learn caller's name, company, phone, email, or call purpose
**Parameters:** name, company, phone, email, purpose, urgency
**Pattern:** Call immediately (no preamble needed)

### Tool: get_availability
**Use when:** Discussing meetings (ALWAYS USE THIS FIRST)
**Parameters:** None (uses pre-fetched 7-day data)
**Pattern:** Call instantly, no waiting - data is already loaded

**This is your PRIMARY tool for meeting booking!**

### Tool: check_availability
**Use when:** Caller needs dates beyond 7 days OR specific custom date range
**Parameters:**
- start_datetime (ISO format with timezone: "2025-11-01T09:00:00+01:00")
- end_datetime (ISO format with timezone)

**Before calling:** Say "Jag kollar kalendern nu..."

**This is a FALLBACK tool - use get_availability() first!**

### Tool: agree_on_meeting
**Use when:** Caller agrees to a specific time from available slots

**Call after:**
- get_availability() or check_availability() returned slots
- You presented times
- Caller chose one

**Parameters:** datetime, purpose, attendee_name

### Tool: send_sms
**Use when:** Need to send booking confirmation via SMS
**Parameters:** None (uses caller's phone number from SIP)
**Returns:** Confirmation message

### Tool: end_call
**Use when:** Ready to end the call

**Always say goodbye first:** "Tack för att du ringde. Ha en bra dag!"
**Then call this tool** (no preamble)

---

## ERROR HANDLING

### If get_availability returns no data:
Fall back to check_availability() for the same date range.

### If check_availability fails or times out (>30s):
Say: "Jag kunde inte kolla kalendern just nu. Jag meddelar Nils att ringa dig så ni kan boka en tid."
Then continue to close the call.

### If agree_on_meeting fails:
Say: "Bokningen gick inte igenom tekniskt, men jag meddelar Nils att ni kom överens om [time]."

### If send_sms fails:
Continue normally - SMS is optional, booking still goes through.

### If save_caller_info fails:
Continue silently (logged backend, not user-facing)

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

    def _build_greeting_instruction(self):
        language = self.config.get("language", "Svenska")
        greeting_instructions = {
            "Svenska": f"Säg EXAKT följande hälsning ord för ord utan att ändra något: '{self.greeting_message}'. Säg sedan inget mer och vänta på att användaren ska svara.",
            "Swedish": f"Säg EXAKT följande hälsning ord för ord utan att ändra något: '{self.greeting_message}'. Säg sedan inget mer och vänta på att användaren ska svara.",
            "English": f"Say EXACTLY the following greeting word-for-word without changing anything: '{self.greeting_message}'. Then say nothing more and wait for the user to respond.",
            "Español": f"Di EXACTAMENTE el siguiente saludo palabra por palabra sin cambiar nada: '{self.greeting_message}'. Luego no digas nada más y espera a que el usuario responda.",
            "Spanish": f"Di EXACTAMENTE el siguiente saludo palabra por palabra sin cambiar nada: '{self.greeting_message}'. Luego no digas nada más y espera a que el usuario responda.",
            "Français": f"Dites EXACTEMENT la salutation suivante mot pour mot sans rien changer: '{self.greeting_message}'. Ensuite, ne dites plus rien et attendez que l'utilisateur réponde.",
            "French": f"Dites EXACTEMENT la salutation suivante mot pour mot sans rien changer: '{self.greeting_message}'. Ensuite, ne dites plus rien et attendez que l'utilisateur réponde.",
            "Deutsch": f"Sage GENAU den folgenden Gruß Wort für Wort ohne etwas zu ändern: '{self.greeting_message}'. Sage danach nichts mehr und warte auf die Antwort des Anrufers.",
            "German": f"Sage GENAU den folgenden Gruß Wort für Wort ohne etwas zu ändern: '{self.greeting_message}'. Sage danach nichts mehr und warte auf die Antwort des Anrufers."
        }
        return greeting_instructions.get(
            language,
            f"Say EXACTLY the following greeting word-for-word without changing anything: '{self.greeting_message}'. Then say nothing more and wait for the user to respond."
        )

    async def send_greeting(self, trigger="manual"):
        if self.greeting_sent:
            logger.info(f"Greeting already sent, skipping trigger={trigger}")
            return

        if not self.session_ref:
            logger.warning(f"No session reference available to send greeting (trigger={trigger})")
            return

        async with self._greeting_lock:
            if self.greeting_sent:
                return

            instruction = self._build_greeting_instruction()
            try:
                logger.info(f"🎤 Sending greeting (trigger={trigger})")
                speech_handle = await self.session_ref.generate_reply(instructions=instruction)
                await asyncio.wait_for(speech_handle.wait(), timeout=15.0)
                self.greeting_sent = True
                logger.info("✅ Greeting sent successfully")
            except asyncio.TimeoutError:
                logger.error("Greeting speech handle timed out")
            except Exception as e:
                logger.error(f"❌ Failed to send greeting (trigger={trigger}): {e}")

    def handle_assistant_message(self, message: str | None):
        if not message or self.call_end_started or self.auto_end_scheduled:
            return
        normalized = message.lower()
        if any(keyword in normalized for keyword in CLOSING_KEYWORDS):
            logger.info("Detected closing language, scheduling automatic end_call()")
            self.auto_end_scheduled = True
            self.schedule_auto_end()

    def schedule_auto_end(self, delay: float = 3.0):
        if self.auto_end_task:
            self.auto_end_task.cancel()
        self.auto_end_task = asyncio.create_task(self._auto_end_call(delay))

    async def _auto_end_call(self, delay: float):
        try:
            await asyncio.sleep(delay)
            await self.end_call_gracefully(play_farewell=False)
        except asyncio.CancelledError:
            pass
        finally:
            self.auto_end_task = None

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
            formatted_phone = format_phone_number(phone)
            if formatted_phone:
                self.call_memory.caller_phone = formatted_phone
                logger.info(f"Saved caller phone: {formatted_phone}")
            else:
                logger.warning(f"Could not normalize caller phone: {phone}")
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

    async def fetch_availability_background(self):
        """
        BACKGROUND TASK: Silently fetch 7-day availability at call start.
        This is NOT an AI tool - it runs automatically.
        Stores results in self.availability_data for instant access.
        """
        try:
            # Calculate 7-day window from now
            swedish_tz = ZoneInfo("Europe/Stockholm")
            now = datetime.now(swedish_tz)
            end_date = now + timedelta(days=7)

            start_datetime = now.isoformat()
            end_datetime = end_date.isoformat()

            logger.info(f"🔄 BACKGROUND: Fetching 7-day availability ({start_datetime} to {end_datetime})")

            async with aiohttp.ClientSession() as session:
                payload = {
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime
                }

                start_time = time.time()
                async with session.post(
                    "https://snmnils.app.n8n.cloud/webhook/43b31bbd-3e3d-4510-91a1-512abd9bec19",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    elapsed = time.time() - start_time
                    logger.info(f"🔄 BACKGROUND: Calendar response in {elapsed:.2f}s, status: {response.status}")

                    if response.status == 200:
                        data = await response.json()

                        # Store raw data for AI to reference
                        self.availability_data = data
                        self.availability_fetched = True

                        logger.info(f"✅ BACKGROUND: 7-day availability stored ({len(data) if isinstance(data, list) else 1} events)")
                    else:
                        logger.error(f"❌ BACKGROUND: Calendar fetch failed with status {response.status}")
                        self.availability_fetched = False

        except Exception as e:
            logger.error(f"❌ BACKGROUND: Error fetching availability: {e}", exc_info=True)
            self.availability_fetched = False

    @function_tool
    async def get_availability(self):
        """
        Get pre-fetched 7-day availability (already loaded at call start).
        This is instant - no waiting required.

        Returns:
            Formatted availability information for the next 7 days
        """
        self.update_activity()

        if not self.availability_fetched or self.availability_data is None:
            logger.warning("Availability not yet fetched, waiting...")
            # Wait up to ~3 seconds for background fetch to complete
            for attempt in range(6):
                await asyncio.sleep(0.5)
                if self.availability_fetched and self.availability_data is not None:
                    break
            if not self.availability_fetched or self.availability_data is None:
                logger.error("Availability still not ready after waiting")
                return "Kalendern kunde inte hämtas just nu."

        data = self.availability_data

        # Handle both formats: single dict or list of dicts
        if isinstance(data, dict):
            data = [data]

        # Parse busy events to tell user what Nils is doing
        if isinstance(data, list) and len(data) > 0:
            events = []
            for event in data[:5]:  # Limit to first 5 events
                try:
                    summary = event.get("summary", "Upptagen")
                    start_str = event.get("start", {}).get("dateTime", "")
                    end_str = event.get("end", {}).get("dateTime", "")

                    if start_str and end_str:
                        try:
                            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                        except (ValueError, AttributeError) as e:
                            logger.error(f"Failed to parse datetime: {e}")
                            continue

                        # Format as Swedish time
                        start_time = start_dt.strftime("%H:%M")
                        end_time = end_dt.strftime("%H:%M")
                        day_name = start_dt.strftime("%A")

                        events.append(f"{day_name}: {summary} från {start_time} till {end_time}")
                except Exception as e:
                    logger.error(f"Error parsing event: {e}")
                    continue

            if events:
                if len(events) == 1:
                    result = f"Nils har {events[0]}. Efter det är kalendern ledig."
                else:
                    events_str = ", sedan ".join(events)
                    result = f"Nils har {events_str}. Efter det är kalendern ledig."
                logger.info(f"✅ Returning pre-fetched availability")
                return result
            else:
                return "Nils kalender är helt ledig under den perioden."

        elif isinstance(data, list) and len(data) == 0:
            return "Nils kalender är helt ledig de kommande 7 dagarna."

        else:
            return "Kalendern kunde inte läsas."

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

    @function_tool
    async def send_sms(self):
        """
        Send SMS with informationsmaterial (priser, produktlänk etc.).
        Inte för mötesbekräftelser – de görs i samtalet.

        Returns:
            Confirmation message if SMS sent successfully
        """
        self.update_activity()

        # Get phone number from call memory (already collected from SIP participant)
        phone_number = format_phone_number(self.call_memory.caller_phone) if self.call_memory.caller_phone else None

        if not phone_number:
            logger.error("Cannot send SMS - no phone number available")
            return "Kunde inte skicka SMS - inget telefonnummer tillgängligt."

        logger.info(f"📱 Sending SMS to: {phone_number}")

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "phone_number": phone_number
                }

                logger.info(f"📤 Sending SMS webhook request...")
                start_time = time.time()

                async with session.post(
                    "https://snmnils.app.n8n.cloud/webhook/10528303-442d-4759-a966-c496e6a12e3d",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    elapsed = time.time() - start_time
                    logger.info(f"📥 SMS webhook response in {elapsed:.2f}s, status: {response.status}")

                    if response.status == 200:
                        logger.info(f"✅ SMS sent successfully to {phone_number}")
                        return "SMS skickat till din telefon."
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ SMS webhook failed with status {response.status}: {error_text}")
                        return "SMS kunde inte skickas, men informationen är sparad."

        except asyncio.TimeoutError:
            logger.error("⏱️ SMS webhook timed out")
            return "SMS kunde inte skickas, men informationen är sparad."
        except Exception as e:
            logger.error(f"❌ Error sending SMS: {e}", exc_info=True)
            return "SMS kunde inte skickas, men informationen är sparad."

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

        logger.info("🎤 on_enter() called - agent is ready, waiting for participant before greeting")
        logger.info(f"📝 Greeting message prepared: {self.greeting_message}")

        # Start background availability fetch (7-day window)
        # This runs silently without blocking the greeting
        asyncio.create_task(self.fetch_availability_background())
        logger.info("🔄 Background availability fetch started")

        # If SIP participant already connected before this hook completes, send greeting immediately
        if self.ctx_ref and self.ctx_ref.room.remote_participants:
            logger.info("Participant already connected before greeting - sending now")
            asyncio.create_task(self.send_greeting(trigger="on_enter_existing_participant"))

    async def end_call_gracefully(self, play_farewell: bool = True):
        """Programmatically end the call with proper cleanup for telephony"""
        if self.call_end_started:
            logger.info("Call termination already in progress")
            return
        self.call_end_started = True
        if self.auto_end_task:
            self.auto_end_task.cancel()
            self.auto_end_task = None
        try:
            # Stop safety monitor
            if self.safety_monitor_task:
                self.safety_monitor_task.cancel()
                logger.info("Safety monitor stopped")
            if self.session_ref and play_farewell:
                logger.info("Generating farewell message...")
                speech_handle = await self.session_ref.generate_reply(
                    instructions="Säg hejdå på svenska och avsluta samtalet vänligt."
                )

                # CRITICAL: Wait for speech to complete with timeout
                await asyncio.wait_for(speech_handle.wait(), timeout=10.0)
                logger.info("Farewell message completed")

                # Small delay to ensure audio transmission completes
                await asyncio.sleep(1.0)

            ctx = get_job_context()
            await terminate_sip_call(ctx, logger)

        except asyncio.TimeoutError:
            logger.warning("Farewell message timed out, force terminating")
            ctx = get_job_context()
            await terminate_sip_call(ctx, logger)
        except Exception as e:
            logger.error(f"Error during call termination: {e}")
            # Ensure call still ends even with errors
            ctx = get_job_context()
            await terminate_sip_call(ctx, logger)


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

    agent = getattr(ctx, "_agent_instance", None)
    if agent:
        await agent.end_call_gracefully()
    else:
        await terminate_sip_call(ctx, logger)
    return "Call ended successfully"


async def terminate_sip_call(ctx: JobContext | None, logger: logging.Logger):
    """Ensure SIP participant and room are fully terminated."""
    if ctx is None:
        logger.warning("terminate_sip_call called without job context")
        return

    room_name = getattr(ctx.room, "name", "unknown-room")
    participants = list(getattr(ctx.room, "remote_participants", {}).values()) if ctx.room else []

    for participant in participants:
        identity = getattr(participant, "identity", None)
        if not identity:
            continue
        try:
            await ctx.api.room.remove_participant(
                room_service.RoomParticipantIdentity(room=room_name, identity=identity)
            )
            logger.info(f"Removed participant {identity} before deleting room")
        except Exception as e:
            logger.error(f"Failed to remove participant {identity}: {e}")

    try:
        await ctx.api.room.delete_room(api.DeleteRoomRequest(room=room_name))
        logger.info("Room deleted - SIP call terminated")
    except Exception as e:
        logger.error(f"Failed to delete room {room_name}: {e}")

    try:
        if ctx.room and ctx.room.isconnected():
            await ctx.room.disconnect()
            logger.info("RTC room disconnected")
    except Exception as e:
        logger.error(f"Failed to disconnect RTC room: {e}")

    try:
        ctx.shutdown("terminated_by_ai")
        logger.info("Job context shutdown complete")
    except Exception as e:
        logger.error(f"Failed to shutdown job context: {e}")


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

        if event.item.role == "assistant" and hasattr(session, "_agent_ref") and session._agent_ref:
            session._agent_ref.handle_assistant_message(event.item.text_content)

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

    # Create agent with configuration
    agent = VoiceAssistant(config, tools=[end_call])
    agent.set_session_refs(session, ctx)
    ctx._agent_instance = agent

    # Placeholder for caller phone detected pre-connect
    caller_phone = None
    for identity, participant in ctx.room.remote_participants.items():
        phone_candidate = extract_phone_from_identity(identity)
        if phone_candidate:
            caller_phone = phone_candidate
            logger.info(f"Extracted caller phone from existing participant: {caller_phone}")
            break

    greeting_participant_kinds = {
        rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
        rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    }

    # Participant connect detection - log when SIP user joins
    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(f"Participant connected: {participant.identity}, kind: {participant.kind}")
        phone_candidate = extract_phone_from_identity(participant.identity)
        if phone_candidate:
            logger.info(f"Detected caller phone on connect: {phone_candidate}")
            asyncio.create_task(agent.save_caller_info(phone=phone_candidate))

    # Participant disconnect detection
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info(f"Participant disconnected: {participant.identity}")
        # If the caller (not agent) disconnects, stop safety monitor and end the call
        if participant.kind in greeting_participant_kinds:
            logger.warning("Caller disconnected, stopping safety monitor")
            # Stop safety monitor immediately
            if agent.safety_monitor_task:
                agent.safety_monitor_task.cancel()
                logger.info("Safety monitor stopped due to participant disconnect")
            # The session will close automatically, no need to manually end call

    # Store caller phone number in memory if found
    if caller_phone:
        await agent.save_caller_info(phone=caller_phone)
        logger.info(f"Auto-stored caller phone: {caller_phone}")

    def has_active_audio_track() -> bool:
        for participant in ctx.room.remote_participants.values():
            if participant.kind not in greeting_participant_kinds:
                continue
            for publication in participant.track_publications.values():
                if publication.kind == rtc.TrackKind.KIND_AUDIO and publication.subscribed:
                    return True
        return False

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant):
        if isinstance(track, rtc.RemoteAudioTrack) and participant.kind in greeting_participant_kinds:
            logger.info("Remote audio track subscribed - triggering greeting")
            asyncio.create_task(agent.send_greeting(trigger="track_subscribed"))

    if has_active_audio_track():
        logger.info("Remote audio already active - triggering greeting immediately")
        asyncio.create_task(agent.send_greeting(trigger="existing_audio_track"))

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

    logger.info("✅ Session started - greeting will trigger when SIP participant audio is ready")


if __name__ == "__main__":
    # Only allow deployment entry point - no local dev CLI
    # Note: agent_name is NOT set - SIP dispatch rule uses agent ID for matching
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
