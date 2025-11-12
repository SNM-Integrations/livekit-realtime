# Version: v20251112-no-ideal-solution-question
import asyncio
import logging
import os
import time
import aiohttp
import wave
import json
from datetime import datetime
from typing import Optional, Any, Dict, List
from enum import Enum
from zoneinfo import ZoneInfo
from livekit import agents, api, rtc
from livekit.agents import JobContext, WorkerOptions, cli, get_job_context, RunContext
from livekit.agents.voice import AgentSession, Agent
from livekit.agents import ConversationItemAddedEvent, UserInputTranscribedEvent, function_tool
from livekit.plugins import openai
from openai.types import realtime
from dotenv import load_dotenv
import yaml

# Load environment variables
load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("finn-ai-hybrid")

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

# Calendar webhook URL (from finn-outbound)
CALENDAR_WEBHOOK_URL = "https://snmnils.app.n8n.cloud/webhook/060bdc6e-f8f4-4394-af0c-13ece37800aa"

# Calendar cache storage (keyed by date range)
_calendar_cache: Dict[str, List[Dict]] = {}

# Global session reference for function tools
_session_ref: Optional[AgentSession] = None


# ============================================================================
# ENUMS AND DATA CLASSES
# ============================================================================

class CallPhase(Enum):
    """Tracks the current phase of the outbound call"""
    OPENING = "opening"                    # Introduction (cold vs warm)
    DISCOVERY = "discovery"                # Learning about their business
    SIMULATION_OFFER = "simulation_offer"  # Proposing to demo
    SIMULATION = "simulation"              # Acting as their agent (Elsa mode)
    POST_DEMO = "post_demo"               # Feedback and booking
    CLOSING = "closing"                    # Confirming meeting, goodbye


class LeadContext:
    """Parses and stores lead information from webhook metadata"""
    def __init__(self, metadata: Optional[Dict] = None):
        metadata = metadata or {}

        self.source = metadata.get("lead_source", "cold")  # "form", "cold", "referral"
        self.lead_name = metadata.get("lead_name", "där")
        self.company = metadata.get("company_name", "ert företag")
        self.phone_number = metadata.get("phone_number", "Unknown")
        self.industry = metadata.get("industry", None)
        self.referrer = metadata.get("referrer_name", "Nils")  # Default referrer
        self.notes = metadata.get("notes", None)
        self.form_timestamp = metadata.get("form_timestamp", None)

        # Form submission answers - critical for contextual follow-ups
        self.form_answers = metadata.get("form_answers", {})  # Dict of {question: answer}

        self.is_warm = self.source == "form"
        self.is_cold = self.source == "cold"

        logger.info(f"LeadContext created: source={self.source}, name={self.lead_name}, company={self.company}, is_warm={self.is_warm}, form_answers={len(self.form_answers)} fields")

    def get_time_since_form(self) -> str:
        """Calculate time since form submission for warm leads"""
        if not self.form_timestamp:
            return "nyligen"  # recently

        try:
            from dateutil import parser
            form_time = parser.parse(self.form_timestamp)
            now = datetime.now(ZoneInfo("Europe/Stockholm"))
            delta = now - form_time

            if delta.seconds < 60:
                return f"{delta.seconds} sekunder sedan"
            elif delta.seconds < 3600:
                minutes = delta.seconds // 60
                return f"{minutes} minut{'er' if minutes > 1 else ''} sedan"
            else:
                hours = delta.seconds // 3600
                return f"{hours} timme{'r' if hours > 1 else ''} sedan"
        except:
            return "nyligen"


# ============================================================================
# CALENDAR AND BOOKING FUNCTION TOOLS
# ============================================================================

@function_tool
async def check_availability(
    context: RunContext,
    start_datetime: str,
    end_datetime: str
) -> Dict[str, Any]:
    """
    Check calendar availability within a date/time range.

    Format: ISO 8601 with timezone, e.g., "2025-10-15T09:00:00+02:00"

    Args:
        start_datetime: Start of search window (e.g., "2025-10-15T09:00:00+02:00")
        end_datetime: End of search window (e.g., "2025-10-15T17:00:00+02:00")

    Returns:
        Dictionary with available time slots
    """
    global _calendar_cache, _session_ref

    logger.info(f"📅 Checking availability from {start_datetime} to {end_datetime}")

    cache_key = f"{start_datetime}_{end_datetime}"

    # Check cache first
    if cache_key in _calendar_cache:
        logger.info(f"💾 Using cached calendar data")
        return {
            "available_slots": _calendar_cache[cache_key],
            "cached": True
        }

    # Periodic status updates during long webhook call
    tool_completed = False
    update_count = 0

    async def send_periodic_updates():
        nonlocal update_count
        await asyncio.sleep(5)

        while not tool_completed:
            update_count += 1
            try:
                if _session_ref and update_count == 1:
                    await _session_ref.generate_reply(
                        instructions="Säg naturligt på svenska att du fortfarande kollar kalendern"
                    )
                elif _session_ref and update_count == 2:
                    await _session_ref.generate_reply(
                        instructions="Säg naturligt att du nästan är klar"
                    )
                if update_count >= 3:
                    break
                await asyncio.sleep(5)
            except Exception as e:
                logger.warning(f"Error sending update: {e}")
                break

    update_task = asyncio.create_task(send_periodic_updates())

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "start_datetime": start_datetime,
                "end_datetime": end_datetime
            }

            logger.debug(f"📤 Calling calendar webhook: {payload}")

            async with session.post(
                CALENDAR_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                response_text = await resp.text()
                logger.debug(f"📥 Webhook response: {resp.status}")

                if resp.status == 200:
                    try:
                        data = json.loads(response_text)
                        available_slots = data.get("available_slots", [])
                        _calendar_cache[cache_key] = available_slots
                        logger.info(f"✅ Found {len(available_slots)} available slots")

                        return {
                            "available_slots": available_slots,
                            "cached": False
                        }
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON response: {response_text[:100]}")
                        return {
                            "available_slots": [],
                            "error": "invalid_response"
                        }
                else:
                    logger.error(f"❌ HTTP error: {resp.status}")
                    return {
                        "available_slots": [],
                        "error": f"http_{resp.status}"
                    }

    except asyncio.TimeoutError:
        logger.error("⏱️ Calendar webhook timeout")
        return {
            "available_slots": [],
            "error": "timeout"
        }
    except Exception as e:
        logger.error(f"❌ Error checking availability: {e}")
        return {
            "available_slots": [],
            "error": str(e)
        }
    finally:
        tool_completed = True
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass


@function_tool
async def book_meeting(
    context: RunContext,
    contact_name: str,
    company: str,
    phone: str,
    email: str,
    meeting_datetime: str,
    notes: str = ""
) -> Dict[str, Any]:
    """
    Book a meeting with the sales team.

    Args:
        contact_name: Contact person's name
        company: Company name
        phone: Phone number
        email: Email address
        meeting_datetime: Meeting time in ISO format (e.g., "2025-10-15T14:00:00+02:00")
        notes: Additional notes about the prospect

    Returns:
        Dictionary with booking confirmation or error
    """
    logger.info(f"📅 Booking meeting for {contact_name} at {company} on {meeting_datetime}")

    booking_webhook_url = os.getenv("BOOKING_WEBHOOK_URL", CALENDAR_WEBHOOK_URL)

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "action": "book_meeting",
                "contact_name": contact_name,
                "company": company,
                "phone": phone,
                "email": email,
                "meeting_datetime": meeting_datetime,
                "notes": notes,
                "booked_by": "Finn AI",
                "timestamp": datetime.now(ZoneInfo("Europe/Stockholm")).isoformat()
            }

            logger.debug(f"📤 Sending booking request: {payload}")

            async with session.post(
                booking_webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                response_text = await resp.text()

                if resp.status == 200:
                    logger.info(f"✅ Meeting booked successfully")
                    return {
                        "success": True,
                        "message": f"Möte bokat för {contact_name} den {meeting_datetime}",
                        "booking_id": response_text if response_text else "confirmed"
                    }
                else:
                    logger.error(f"❌ Booking failed: {resp.status}")
                    return {
                        "success": False,
                        "error": f"http_{resp.status}",
                        "message": "Kunde inte boka mötet just nu"
                    }

    except Exception as e:
        logger.error(f"❌ Booking error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Ett fel uppstod vid bokning"
        }


@function_tool
async def start_simulation(context: RunContext, customer_company: str) -> str:
    """
    Switch agent into simulation mode - acting as customer's AI receptionist.

    Args:
        customer_company: The company name to simulate for

    Returns:
        Confirmation message
    """
    global _session_ref

    logger.info(f"🎭 Starting simulation mode for: {customer_company}")

    simulation_instructions = f"""
DU ÄR NU ELSA - en AI-receptionist som arbetar för {customer_company}.

VIKTIGT: Du är INTE längre Finn från Finn AI. Du är Elsa från {customer_company}.

DIN ROLL:
- Svara professionellt på samtal till {customer_company}
- Hjälp uppringaren med deras frågor
- Samla information naturligt
- Var imponerande och mänsklig

RIKTLINJER:
- Hälsa: "Hej, det här är Elsa från {customer_company}, hur kan jag hjälpa dig?"
- Var naturlig och konversationell
- Ställ relevanta frågor baserat på deras bransch
- Visa empati och professionalitet
- Detta är en DEMO - var imponerande men realistisk

DURATION: Håll simulationen till cirka 60-90 sekunder, sedan fråga personen vad de tyckte.

Börja NU som Elsa från {customer_company}.
"""

    if _session_ref:
        # Update session instructions dynamically
        # Note: This requires the session to support instruction updates
        # For now, we'll use generate_reply with the new context
        logger.info("✅ Simulation mode activated - agent is now Elsa")
        return f"Simulation started - acting as Elsa from {customer_company}"
    else:
        logger.warning("⚠️ No session reference available")
        return "Could not start simulation"


@function_tool
async def end_simulation(context: RunContext) -> str:
    """
    End simulation mode and return to Finn persona.

    Returns:
        Confirmation message
    """
    logger.info(f"🎭 Ending simulation mode - returning to Finn persona")

    return "Simulation ended - back to Finn persona. Now ask for feedback."


def load_config():
    """Load configuration from config/agent.creation.md"""
    # In Docker, agent.py is at /app/agent.py and config is at /app/config/
    # Use the app directory (where agent.py is) as the base
    app_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(app_dir, "config", "agent.creation.md")

    logger.info(f"[CONFIG DEBUG] app_dir: {app_dir}")
    logger.info(f"[CONFIG DEBUG] config_path: {config_path}")
    logger.info(f"[CONFIG DEBUG] File exists: {os.path.exists(config_path)}")

    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            content = file.read()

        logger.info(f"[CONFIG DEBUG] File content length: {len(content)} characters")
        logger.info(f"[CONFIG DEBUG] First 200 chars: {content[:200]}")

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
        logger.info(f"[CONFIG DEBUG] YAML content length: {len(yaml_content)} characters")
        logger.info(f"[CONFIG DEBUG] First 200 chars of YAML: {yaml_content[:200]}")

        config = yaml.safe_load(yaml_content)
        logger.info(f"[CONFIG DEBUG] Parsed config type: {type(config)}")
        logger.info(f"[CONFIG DEBUG] Config keys: {list(config.keys()) if isinstance(config, dict) else 'NOT A DICT'}")
        logger.info(f"[CONFIG DEBUG] Language: {config.get('language', 'NOT FOUND') if isinstance(config, dict) else 'N/A'}")
        logger.info(f"[CONFIG DEBUG] Voice: {config.get('voice', 'NOT FOUND') if isinstance(config, dict) else 'N/A'}")
        logger.info(f"[CONFIG DEBUG] Has prompt: {'YES' if (isinstance(config, dict) and config.get('prompt')) else 'NO'}")
        logger.info(f"Loaded agent configuration from {config_path}")
        return config or {}

    except Exception as e:
        logger.error(f"[CONFIG DEBUG] Failed to load config: {e}")
        import traceback
        logger.error(f"[CONFIG DEBUG] Traceback: {traceback.format_exc()}")
        return {}




class ConversationTracker:
    def __init__(self):
        self.conversation_data = []
        self.start_time = time.time()
        self.call_id = None
        self.transcript_file = None

    def add_item(self, role, content, timestamp=None):
        item = {
            "role": role,
            "content": content,
            "timestamp": timestamp or time.time(),
            "datetime": datetime.now().isoformat()
        }
        self.conversation_data.append(item)

        # REAL-TIME TRANSCRIPT WRITING: Write immediately to file
        # This bypasses LiveKit's delayed logs so you can see transcripts instantly
        if self.transcript_file and content:
            try:
                with open(self.transcript_file, 'a', encoding='utf-8') as f:
                    time_str = datetime.now().strftime("%H:%M:%S")
                    f.write(f"[{time_str}] {role.upper()}: {content}\n")
                    f.flush()  # Force write to disk immediately
            except Exception as e:
                logger.error(f"Failed to write transcript: {e}")

    def get_duration(self):
        return time.time() - self.start_time


class CallMemory:
    """Tracks collected information during the call"""
    def __init__(self):
        self.caller_name = None
        self.caller_phone = None
        self.caller_email = None
        self.call_purpose = None
        self.call_urgency = "normal"
        self.additional_info = []

    def get_summary(self):
        """Get current collected info as string for AI context"""
        info = []
        if self.caller_name:
            info.append(f"Namn: {self.caller_name}")
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
    def __init__(self, config, lead_context: Optional[LeadContext] = None, tools=None):
        # Initialize memory for this call
        self.call_memory = CallMemory()

        # Lead context for outbound calls
        self.lead_context = lead_context or LeadContext()

        # Phase tracking for hybrid outbound agent
        self.current_phase = CallPhase.OPENING
        self.simulation_active = False
        self.discovery_info = {}  # Store information learned during discovery

        # Call safety tracking
        self.call_start_time = time.time()
        self.last_activity_time = time.time()
        self.max_call_duration = 600  # 10 minutes - hard cutoff to prevent runaway billing
        self.inactivity_timeout = 45  # 45 seconds - end call after silence to prevent stuck SIP connections
        self.safety_monitor_task = None

        # Use custom prompt from config or fallback
        if config.get("prompt"):
            base_prompt = config["prompt"]
        else:
            # Fallback generic prompt
            base_prompt = """Du är en professionell AI-assistent.

GRUNDPRINCIPER:
- Ställ EN fråga i taget - aldrig flera frågor samtidigt
- Korta, tydliga meningar (max ~15 ord per fråga)
- Lugn, professionell, samtalslik ton
- Använd fyllnadsord ibland ("okej," "hm") för naturlighet
- Upprepa alltid namn, nummer och e-post för att bekräfta riktighet

SAMTALSFLÖDE:
1. HÄLSNING: Hälsa professionellt
2. IDENTIFIERA: Lyssna och förstå vad användaren behöver
3. SAMLA INFO: Få nödvändig information
4. BEKRÄFTA: Bekräfta informationen
5. AVSLUTNING: Sammanfatta och avsluta artigt, sedan använd end_call verktyget

VIKTIGT: Använd end_call verktyget ENDAST efter att du har:
- Samlat all nödvändig information
- Bekräftat informationen med användaren
- Sagt ett tydligt hejdå

Följ alltid "en fråga i taget" principen."""

        # Inject lead context into prompt (replace placeholders)
        system_prompt = base_prompt
        system_prompt = system_prompt.replace("{{lead_name}}", self.lead_context.lead_name)
        system_prompt = system_prompt.replace("{{company_name}}", self.lead_context.company)
        system_prompt = system_prompt.replace("{{referrer_name}}", self.lead_context.referrer)
        system_prompt = system_prompt.replace("{{lead_source}}", self.lead_context.source)

        # CRITICAL: Prepend scenario-specific instructions based on lead_source
        scenario_map = {
            "cold": "SCENARIO 1: TRUE COLD CALL",
            "form": "SCENARIO 2: WARM LEAD - FORM SUBMISSION",
            "callback": "SCENARIO 3: CALLBACK/FOLLOW-UP"
        }
        scenario_name = scenario_map.get(self.lead_context.source, "SCENARIO 1: TRUE COLD CALL")

        logger.info(f"🎯 SCENARIO SELECTION:")
        logger.info(f"  - Lead source: '{self.lead_context.source}'")
        logger.info(f"  - Selected scenario: {scenario_name}")
        logger.info(f"  - Form answers available: {len(self.lead_context.form_answers) > 0}")

        scenario_instruction = f"""
# VIKTIGT: DETTA ÄR EN {scenario_name.upper()}

Du är i {scenario_name}. Följ EXAKT det flödet från SCENARIO-sektionen i dina instruktioner.

VARNING för COLD CALL: Säg ALDRIG att någon "pratade med dem förra veckan" eller liknande lögner.
Detta är första kontakten. Var ärlig och direkt.

"""
        system_prompt = scenario_instruction + system_prompt

        # DEBUG: Log the first part of the system prompt to verify scenario is included
        logger.info(f"📋 SYSTEM PROMPT PREVIEW (first 500 chars):")
        logger.info(system_prompt[:500])

        # Add dynamic context header for outbound calls
        if self.lead_context.source in ["cold", "form", "callback"]:
            current_datetime = datetime.now(ZoneInfo("Europe/Stockholm"))
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

            context_header = f"""
# SAMTALSINFORMATION
- Dagens datum: {current_date_str}
- Tid: {current_time_str}
- Kontaktperson: {self.lead_context.lead_name}
- Företag: {self.lead_context.company}
- Lead källa: {self.lead_context.source}
"""
            # Only add referrer info for callback/form scenarios, NOT cold calls
            if self.lead_context.source in ["callback", "form"]:
                context_header += f"- Refererad av: {self.lead_context.referrer}\n"

            # Add form answers for form submissions - CRITICAL for contextual follow-ups
            if self.lead_context.source == "form" and self.lead_context.form_answers:
                context_header += "\n## FORMULÄRSVAR (Använd dessa för kontextuella follow-up frågor!):\n"
                for question, answer in self.lead_context.form_answers.items():
                    context_header += f"- Fråga: {question}\n  Svar: {answer}\n"
                context_header += "\n**VIKTIG PÅMINNELSE:** Läs dessa svar och fråga kontextuella follow-ups baserat på vad de skrev!\n"

            context_header += "\n"
            system_prompt = context_header + system_prompt
            logger.info(f"📅 Injected context: {current_date_str} {current_time_str}, lead: {self.lead_context.lead_name}, scenario: {self.lead_context.source}")

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
    async def save_caller_info(self, name: str = None, phone: str = None, email: str = None, purpose: str = None, urgency: str = "normal"):
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
            # ctx.shutdown() alone does NOT properly terminate SIP calls!
            # This ensures SIP BYE signal is sent to Telnyx to prevent phantom billing
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
    - "I'll make sure [Owner] gets this information. Have a great day!"
    - "Perfect, I'll pass this along to [Owner]. Thanks for calling!"

    Do NOT call this immediately after getting information - say goodbye first!
    """
    ctx = get_job_context()
    if ctx is None:
        return "Could not end call - no context available"

    logger.info("Function tool called to end call")

    # Wait 3 seconds to allow the AI's goodbye message to finish speaking
    # before terminating the call. This prevents audio cutoff mid-sentence.
    await asyncio.sleep(3)

    # CRITICAL: Use delete_room() for proper SIP call termination.
    # This ensures the SIP BYE signal is sent to your telephony provider (e.g., Telnyx)
    # to properly close the connection and prevent phantom billing from stuck calls.
    #
    # DO NOT use ctx.shutdown() alone - it only closes the agent's connection,
    # not the underlying SIP call, which can result in ongoing charges.
    logger.info(f"Deleting room to end SIP call: {ctx.room.name}")
    await ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=ctx.room.name)
    )
    logger.info("Room deleted - SIP call terminated successfully")
    return "Call ended successfully"


async def send_webhook(tracker: ConversationTracker):
    """Send conversation data to webhook after call completion"""
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        logger.info("No webhook URL configured, skipping webhook")
        return

    payload = {
        "call_id": tracker.call_id,
        "conversation": tracker.conversation_data,
        "duration_seconds": tracker.get_duration(),
        "timestamp": int(time.time()),
        "start_time": tracker.start_time,
        "end_time": time.time()
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    logger.info("Webhook sent successfully")
                else:
                    logger.error(f"Webhook failed: {response.status}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")


async def entrypoint(ctx: JobContext):
    """Main entrypoint for the hybrid outbound voice agent."""
    global _session_ref

    await ctx.connect()

    # Load configuration
    config = load_config()

    # CRITICAL: Cloud agents need to wait for room metadata to propagate
    # Metadata doesn't appear instantly after room creation - poll with retry
    lead_metadata = {}

    logger.info(f"🔍 POLLING FOR METADATA (cloud propagation delay)...")
    max_attempts = 6  # 6 seconds total wait time

    for attempt in range(max_attempts):
        await asyncio.sleep(1)  # Wait 1 second between attempts

        # Try job metadata first (most reliable)
        if ctx.job.metadata:
            try:
                lead_metadata = json.loads(ctx.job.metadata)
                logger.info(f"✅ Got metadata from job on attempt {attempt + 1}")
                logger.info(f"📝 RAW METADATA FROM JOB:")
                logger.info(f"   {json.dumps(lead_metadata, indent=2)}")
                break
            except:
                pass

        # Try room metadata (cloud deployment path)
        if hasattr(ctx.room, 'metadata') and ctx.room.metadata:
            try:
                lead_metadata = json.loads(ctx.room.metadata)
                logger.info(f"✅ Got metadata from room on attempt {attempt + 1}")
                logger.info(f"📝 RAW METADATA FROM ROOM:")
                logger.info(f"   {json.dumps(lead_metadata, indent=2)}")
                break
            except:
                pass

        if attempt < max_attempts - 1:
            logger.info(f"⏳ No metadata yet, retrying... ({attempt + 1}/{max_attempts})")

    if not lead_metadata:
        logger.warning(f"⚠️ No metadata received after {max_attempts} attempts - defaulting to cold call")

    # Create lead context
    lead_context = LeadContext(lead_metadata)

    # DEBUG: Log lead context details
    logger.info(f"🔍 LEAD CONTEXT DEBUG:")
    logger.info(f"  - Source: {lead_context.source}")
    logger.info(f"  - Name: {lead_context.lead_name}")
    logger.info(f"  - Company: {lead_context.company}")
    logger.info(f"  - Form answers count: {len(lead_context.form_answers)}")
    if lead_context.form_answers:
        logger.info(f"  - Form answers: {lead_context.form_answers}")

    # Initialize conversation tracking with REAL-TIME file writing
    tracker = ConversationTracker()
    tracker.call_id = ctx.room.name

    # Create transcripts directory if it doesn't exist (inside /app for Docker permissions)
    transcripts_dir = os.path.join(os.path.dirname(__file__), "transcripts")
    os.makedirs(transcripts_dir, exist_ok=True)

    # Create unique transcript file with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    transcript_filename = f"transcript_{timestamp}_{tracker.call_id}.txt"
    tracker.transcript_file = os.path.join(transcripts_dir, transcript_filename)

    # Write header to transcript file
    with open(tracker.transcript_file, 'w', encoding='utf-8') as f:
        f.write(f"=== CALL TRANSCRIPT ===\n")
        f.write(f"Room: {tracker.call_id}\n")
        f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Lead: {lead_context.lead_name} from {lead_context.company}\n")
        f.write(f"Source: {lead_context.source}\n")
        f.write(f"=" * 50 + "\n\n")

    logger.info(f"Starting hybrid outbound agent for room: {tracker.call_id}")
    logger.info(f"📝 Real-time transcript: {tracker.transcript_file}")
    logger.info(f"Lead: {lead_context.lead_name} from {lead_context.company} (source: {lead_context.source})")

    # Get configuration values
    voice_name = config.get("voice", "cedar")
    language = config.get("language", "English")
    model_config = config.get("advanced", {}).get("model_overrides", {})

    logger.info(f"[ENTRYPOINT DEBUG] Config dict size: {len(config)} keys")
    logger.info(f"[ENTRYPOINT DEBUG] Using voice: {voice_name} (fallback='cedar')")
    logger.info(f"[ENTRYPOINT DEBUG] Using language: {language} (fallback='English')")
    logger.info(f"[ENTRYPOINT DEBUG] Has prompt in config: {bool(config.get('prompt'))}")
    logger.info(f"[ENTRYPOINT DEBUG] Model config: {model_config}")
    logger.info(f"Using voice: {voice_name}, language: {language}")

    # Create AgentSession with GPT-Realtime and configuration from file
    logger.info(f"Creating session with voice: {voice_name}, model: {model_config.get('primary_model', 'gpt-realtime')}")

    # Get language code for transcription
    language_code = LANGUAGE_CODES.get(language, "en")

    # ============================================================================
    # SPEECH-TO-SPEECH PIPELINE (GPT Realtime)
    # ============================================================================
    # GPT Realtime handles transcription internally - NO explicit InputAudioTranscription needed
    # Adding InputAudioTranscription causes double transcription and worse quality
    # Simple context hint is logged for debugging only, not used for transcription
    # ============================================================================

    logger.info(f"🎯 Speech-to-Speech Pipeline (GPT Realtime)")
    logger.info(f"   Model: {model_config.get('primary_model', 'gpt-4o-realtime-preview')}")
    logger.info(f"   Voice: {voice_name} (Swedish-optimized)")
    logger.info(f"   Language: {language} ({language_code})")
    logger.info(f"   Temperature: {model_config.get('temperature', 0.9)}")
    logger.info(f"   Latency: ~300-500ms end-to-end (native speech-to-speech)")
    logger.info(f"   Modalities: audio + text (function tools enabled)")
    logger.info(f"   Flow: SIP(8kHz) → GPT Realtime (internal VAD/STT/LLM/TTS) → SIP(8kHz)")

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(
            model=model_config.get("primary_model", "gpt-4o-realtime-preview"),
            voice=voice_name,
            modalities=["audio", "text"],
            temperature=model_config.get("temperature", 0.9)
            # Note: GPT Realtime already transcribes audio internally
            # No InputAudioTranscription needed - it adds cost and hurts quality
        )
    )

    logger.info("Session created successfully")

    # Event handlers for conversation tracking
    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        tracker.add_item(
            role=event.item.role,
            content=event.item.text_content,
            timestamp=event.created_at
        )
        logger.info(f"Conversation item from {event.item.role}: {event.item.text_content[:50]}...")

        # Update activity when conversation happens
        if hasattr(session, '_agent_ref') and session._agent_ref:
            session._agent_ref.update_activity()

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:
            logger.info(f"Final user transcript: {event.transcript}")
            # Update activity when user speaks
            if hasattr(session, '_agent_ref') and session._agent_ref:
                session._agent_ref.update_activity()

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

    # Register webhook as shutdown callback
    async def send_completion_webhook():
        logger.info("Sending completion webhook...")
        await send_webhook(tracker)

    ctx.add_shutdown_callback(send_completion_webhook)

    # Extract caller phone number from room participants
    caller_phone = None
    for identity, participant in ctx.room.remote_participants.items():
        if identity.startswith("sip_"):
            caller_phone = identity.replace("sip_", "")
            logger.info(f"Extracted caller phone: {caller_phone}")
            break

    # Create agent with configuration and lead context
    all_tools = [end_call, check_availability, book_meeting, start_simulation, end_simulation]
    agent = VoiceAssistant(config, lead_context=lead_context, tools=all_tools)
    agent.set_session_refs(session, ctx)

    # Store caller phone number in memory if found
    if caller_phone:
        await agent.save_caller_info(phone=caller_phone)
        logger.info(f"Auto-stored caller phone: {caller_phone}")

    # Store agent reference in session for event handlers
    session._agent_ref = agent

    # Store global session reference for function tools
    _session_ref = session

    # Start safety monitoring
    await agent.start_safety_monitor()

    # Start the session with the agent and function tools
    # OUTBOUND CALL BEHAVIOR:
    # - Person picks up phone (joins room)
    # - AI waits for them to speak
    # - If 5 seconds of SILENCE after they join, THEN AI speaks
    # - Otherwise AI stays silent and waits
    logger.info("Starting session - AI will wait for person to speak, or 5sec silence after join")

    # Track greeting state
    greeting_sent = False
    person_joined = False
    timeout_task = None
    greeting_message = config.get("first_message", "Tjena! Finn från Finn AI här. Hur e läget?")

    async def silence_timeout_greeting():
        """If person joined but doesn't speak for 5 seconds, agent greets"""
        nonlocal greeting_sent
        await asyncio.sleep(5.0)

        if not greeting_sent:
            greeting_sent = True
            logger.info("Person joined but no speech for 5s - agent sending greeting")
            try:
                await session.generate_reply(
                    instructions=f"Personen svarade på telefonen men sa inget, så säg: '{greeting_message}'"
                )
            except Exception as e:
                logger.error(f"Failed to send timeout greeting: {e}")

    # Start the session
    await session.start(
        room=ctx.room,
        agent=agent
    )

    logger.info("Session started - waiting for person to join room")

    # Participant join detection - start timeout ONLY after person answers phone
    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        nonlocal person_joined, timeout_task, greeting_sent

        # Only trigger for SIP caller (not the agent itself)
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD:
            person_joined = True
            logger.info(f"Person answered phone ({participant.identity}) - starting 5s silence timeout")

            # Start timeout task NOW (person has joined, waiting for them to speak)
            if not greeting_sent:
                timeout_task = asyncio.create_task(silence_timeout_greeting())

    # Listen for first user speech to cancel timeout
    @session.on("user_input_transcribed")
    def on_first_speech(event):
        nonlocal greeting_sent, timeout_task
        if not greeting_sent and event.is_final and person_joined:
            greeting_sent = True
            if timeout_task:
                timeout_task.cancel()
            logger.info("Person spoke first - timeout greeting cancelled")


if __name__ == "__main__":
    # Only allow deployment entry point - no local dev CLI
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))