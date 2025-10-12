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

logger = logging.getLogger("finn-ai")

# Global reference to job context for end_call function
_job_context: Optional[JobContext] = None


# ============================================================================
# CALENDAR FUNCTION TOOL - N8N WEBHOOK INTEGRATION
# ============================================================================

CALENDAR_WEBHOOK_URL = "https://snmnils.app.n8n.cloud/webhook/060bdc6e-f8f4-4394-af0c-13ece37800aa"

# Calendar cache storage (keyed by date)
_calendar_cache: dict[str, list[dict]] = {}

@function_tool
async def check_availability(
    context: RunContext,
    proposed_date: str,
    proposed_time: str
) -> dict[str, Any]:
    """
    Check calendar availability for a specific date and time.

    IMPORTANT CACHING BEHAVIOR:
    - This function caches ALL availability results for a given date
    - Once you check ANY time on a specific date, all results for that day are cached
    - You do NOT need to call this function again for the same date
    - Example: If you check Tuesday 10:00, the response includes ALL available times for Tuesday
    - If customer later asks about Tuesday afternoon, use the CACHED results, don't call again

    Args:
        proposed_date: Date in YYYY-MM-DD format (e.g., "2025-10-15")
        proposed_time: Time in 24-hour HH:MM format (e.g., "14:00")

    Returns:
        Dictionary with availability status and ALL available times for that date
        The 'all_available_times' field contains all free slots for the day

    CRITICAL: After calling this once for a date, use the cached 'all_available_times' for follow-up questions.
    """
    global _calendar_cache

    logger.info(f"📅 Checking availability for {proposed_date} at {proposed_time}")

    # Check cache first
    if proposed_date in _calendar_cache:
        logger.info(f"💾 Using cached calendar data for {proposed_date}")
        cached_times = _calendar_cache[proposed_date]

        # Check if requested time is in cached results
        is_available = any(
            slot.get("time") == proposed_time and slot.get("available", False)
            for slot in cached_times
        )

        return {
            "available": is_available,
            "proposed_datetime": f"{proposed_date} {proposed_time}",
            "message": f"Tid {proposed_time} är {'ledig' if is_available else 'upptagen'}",
            "all_available_times": cached_times,
            "cached": True,
            "note": "Using cached calendar data - no need to call function again for this date"
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
                        instructions="Säg naturligt på svenska att du fortfarande kollar kalendern, typ 'jag kollar fortfarande' eller 'ett ögonblick till bara'"
                    )
                elif update_count == 2:
                    await context.session.generate_reply(
                        instructions="Säg naturligt på svenska att du nästan är klar, typ 'jag är nästan klar' eller 'bara några sekunder till'"
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
            # Simple webhook payload
            payload = {
                "date": proposed_date,
                "time": proposed_time
            }

            headers = {
                "Content-Type": "application/json"
            }

            logger.debug(f"📤 Calling calendar webhook: {payload}")

            async with session.post(
                CALENDAR_WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                response_text = await resp.text()
                logger.debug(f"📥 Webhook response: {resp.status} - {response_text[:300]}")

                if resp.status == 200:
                    try:
                        data = json.loads(response_text)
                        logger.info(f"✅ Calendar check successful: {data}")

                        # Cache the results - extract all available times for this date
                        # Assuming webhook returns list of available times in some format
                        # If webhook returns single time check, we'll cache that
                        available_times = data.get("all_available_times", [])
                        if not available_times:
                            # If webhook doesn't return all times, create cache entry with this result
                            available_times = [{
                                "time": proposed_time,
                                "available": data.get("available", True)
                            }]

                        # Store in cache for this date
                        _calendar_cache[proposed_date] = available_times
                        logger.info(f"💾 Cached {len(available_times)} time slots for {proposed_date}")

                        # Add cache info to response
                        data["all_available_times"] = available_times
                        data["cached"] = False
                        data["note"] = "Fresh calendar data - now cached for future queries on this date"

                        return data
                    except json.JSONDecodeError:
                        logger.warning(f"Non-JSON response: {response_text[:100]}")
                        # Return success with raw response
                        return {
                            "available": True,
                            "proposed_datetime": f"{proposed_date} {proposed_time}",
                            "message": response_text[:100] if response_text else "Time checked",
                            "note": "Non-JSON response from calendar"
                        }
                else:
                    logger.error(f"❌ Webhook HTTP error: {resp.status} - {response_text[:200]}")
                    return {
                        "available": False,
                        "error": f"http_{resp.status}",
                        "proposed_datetime": f"{proposed_date} {proposed_time}",
                        "message": "Jag kunde inte kolla kalendern just nu. Föreslå gärna en tid!",
                        "note": f"HTTP {resp.status}: {response_text[:100]}"
                    }

    except asyncio.TimeoutError:
        logger.error("⏱️ Webhook timeout")
        return {
            "available": False,
            "error": "timeout",
            "proposed_datetime": f"{proposed_date} {proposed_time}",
            "message": "Kalendern svarar inte. Föreslå gärna en tid!",
            "note": "Timeout after 10s"
        }
    except Exception as e:
        logger.error(f"❌ Error checking availability: {e}", exc_info=True)
        return {
            "available": False,
            "error": str(e),
            "proposed_datetime": f"{proposed_date} {proposed_time}",
            "message": "Jag kunde inte kolla kalendern. Föreslå gärna en tid!",
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

        # Get current Swedish time - FRESH FOR EACH CALL
        current_datetime = datetime.now(ZoneInfo("Europe/Stockholm"))

        # Swedish day names
        swedish_days = {
            "Monday": "måndag",
            "Tuesday": "tisdag",
            "Wednesday": "onsdag",
            "Thursday": "torsdag",
            "Friday": "fredag",
            "Saturday": "lördag",
            "Sunday": "söndag"
        }

        # Swedish month names
        swedish_months = {
            1: "januari", 2: "februari", 3: "mars", 4: "april",
            5: "maj", 6: "juni", 7: "juli", 8: "augusti",
            9: "september", 10: "oktober", 11: "november", 12: "december"
        }

        # Format for prompt with Swedish names
        weekday_en = current_datetime.strftime("%A")
        current_weekday = swedish_days.get(weekday_en, weekday_en)
        current_date_str = f"{current_weekday} {current_datetime.day} {swedish_months[current_datetime.month]} {current_datetime.year}"
        current_time_str = current_datetime.strftime("%H:%M")

        logger.info(f"📅 Agent created with date: {current_date_str} {current_time_str}")

        # Load prompt from file - Carolina from Profit Media
        prompt_file = "../../Prompts/carolina_agent_prompt.md"
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
            # Fallback to inline prompt
            instructions = f"""
# ROLE & OBJECTIVE

Du är Elsa, mötesbokare från Finn AI. Du ringer {self.lead_name} som 30 sekunder sedan fyllde i formulär för att testa AI-röstassistenter.

**Dagens datum:** {current_date_str} kl {current_time_str}

**Telefonnummer du ringer:** {self.phone_number}

**Primärt mål:** Boka demo-möte med grundarna (Nils eller Samuel) om kunden är intresserad.

**Success = Möte bokat ELLER kundvänlig avslut om ej intresserad.**

---

# PERSONALITY & TONE

**Persona:** 30-årig erfaren säljare, skandinavisk, självsäker men inte pushy.

**Du är medveten om att du är AI** - det är din styrka. Var lite kaxig om produkten (AI för telefonsamtal).

**Energinivå:** Lugn professionalism. INTE praktikant, INTE överentusiastisk.

**Ton:**
- Vänlig men INTE överexalterad
- Självsäker men INTE aggressiv eller självgod
- Nyfiken men INTE överdrivet entusiastisk
- LYSSNA aktivt - reagera på vad personen säger

**Längd:** Max 1-2 meningar per tur. Detta är ett telefonsamtal, inte textbaserat.

---

# CONTEXT

**Om Finn AI:**
- Företag: Finn AI (säljer AI-röstassistenter för företag)
- Grundare: Nils och Samuel
- Produkt exempel: Inbound-assistenter, outbound-agents, AI-receptionister

**Om möten:**
- Bokas med: En av grundarna (du vet inte vem ännu - säg ALDRIG specifikt namn)
- Format: 30-min, visar hur en AI fungerar och kollar om det finns en lösning som ger värde för kundens företag
- Syfte: Visa hur AI kan anpassas till kundens verksamhet

**Om samtalet:**
- De vet du ska ringa (fyllde i formulär)
- De är nyfikna på AI för telefonsamtal
- Du är en av produkterna de vill testa

**KRITISK REGEL - Grounding:**
- Hitta ALDRIG på kollegnamn, priser, produkter eller features
- Vid osäkerhet: "Det är något vi går igenom på mötet"

---

# REFERENCE PRONUNCIATIONS

**Svenska stavningar:**
- @ = snabel-a
- . = punkt
- Dubbelkonsonanter: Om samtalet sker på svenska kan du anta att "dubbel-v" är "w"
- Tider: 24-timmarsformat (14:00, 10:30)

---

# TOOLS

**Tillgängliga verktyg:**

## check_availability
**Syfte:** Kolla kalenderledig för möten
**När:** ENDAST efter kund valt dag (måndag, tisdag, etc.)
**Hur:** Anropa EN GÅNG per dag - returnerar ALLA tider för hela dagen
**Viktigt:** Prata medan du väntar ("Låt mig kolla kalendern...")

## end_call
**Syfte:** Avsluta samtalet korrekt
**När:** Efter naturlig avslutning (möte bokat ELLER kund tackat nej)
**Viktigt:** Säg hejdå FÖRST, anropa SEDAN

---

# INSTRUCTIONS

## Talspråk (kritiskt)

Du pratar i telefon. INTE formell text.

**Tekniker:**
- Mjukgörare: "Men", "Ja", "Alltså" (sparsamt)
- Fyllnadsord: "ju", "väl", "liksom", "typ" (sparsamt)
- Konfirmering: "...eller hur?", "...eller?" (ibland)
- Sammandragningar: "AI:n" (inte "AI")
- Naturliga frågor: "Kan inte du...", "Skulle inte det..."

**Undvik:**
- Överentusiasm: "Grymt!", "Oj!", "Jaha!" med massa utropstecken
- Formella konstruktioner: "Berätta lite – vad gör du om dagarna?"
- För många "Men du" eller andra fyllnadsord i rad

**Ton-exempel:**
- FEL ton: "Skulle det vara värt att kolla på?" (distanserat)
- RÄTT ton: Personlig, direkt fråga med "skulle det vara intressant för dig?"

## Konversationsregler

**EN fråga per tur.**
- ALDRIG två frågor samtidigt
- 1 tur = 1 reaktion/påstående + MAX 1 fråga
- LYSSNA på svaret innan nästa fråga

**Bygg egna meningar.**
- Använd din egen formulering varje gång
- Anpassa till vad kunden faktiskt sa
- Följ INTE script slaviskt

**Reagera på kunden.**
- Om de säger något oväntat: reagera först, sen fortsätt
- Om de ställer fråga: svara först, återgå sedan till flöde
- Om de visar intresse tidigt: anpassa tempot

---

# CONVERSATION FLOW

## Preferred Flow (när samtalet går naturligt)

**Fas 2: Röst-feedback**
- Syfte: Hålla samtalet levande, vara lite kaxig om produkten
- Fråga om det är första gången med AI på telefon
- Fråga vad de tycker om din röst
- Avsluta med självsäker men rolig konfirmering
- OM negativt svar: Nämn att ni har flera röster att välja på
- OM positivt: Kort bekräftelse, gå vidare
- Hålltid: Max 20 sekunder

**Fas 3: Förstå bransch**
- Syfte: Ta reda på vad de jobbar med
- Fråga konversationellt vad de gör om dagarna (talspråk)
- Lyssna på svar
- Ställ EN uppföljande fråga baserat på situation:
  - Leads/marknadsföring → hur snabbt ringer de upp leads?
  - Möten/service → vem tar samtal när upptagna?
  - Ute på jobb → hur hanterar de samtal då?

**Fas 4: Användningsfall**
- Fråga om de hade tanke på användningsfall när de fyllde i formuläret
- Lyssna noga

**Fas 5: Pitch**
- OM de HAR användningsfall: Bygg på deras idé, fråga om de vill veta mer
- OM de INTE har: Pitcha konkret lösning baserad på deras bransch
  - Använd talspråk-tekniker
  - Basera på vad de faktiskt sa
  - Fråga om de vill veta mer om AI-rösten

**Fas 6: Boka möte**
- Pitcha mötet: Kort demo med en av grundarna, se hur produkten fungerar
- Fråga om det skulle vara intressant
- Om ja → fortsätt med bokning (se Tool Usage nedan)
- Om tveksam: Visa förståelse, reframe (inte försäljning, bara förstå AI-röster)
- Om nej: Fråga EN gång varför, sedan vänlig avslut

## Override Rules (HÖGSTA PRIORITET)

**USER INTENT > FLOW**

**OM användaren säger:**
- "Jag vill boka möte" → Hoppa direkt till Fas 6
- "Inte intresserad" → Fråga en gång varför, sedan avslut
- Ställer fråga → Svara, återgå till där du var
- Förklarar affären utan att du frågat → Hoppa över Fas 3

**Flexibilitet:**
- Preferred flow = GPS-rutt
- User intent = trafikolycka som kräver omväg
- Om kund hoppar direkt till bokning → följ deras lead
- Om kund redan förklarat affären → skippa discovery
- Återgå till flödet om det fortfarande är relevant

---

# TOOL USAGE - BOOKING PROCESS

## Steg 1: Pitcha mötet
- När kund visar intresse
- Förklara: "Kort möte med en av grundarna, visa hur produkten fungerar och anpassa till er verksamhet"
- Fråga om intressant

## Steg 2: Välj dag
- Fråga om dag-preferens (måndag/tisdag/etc.)
- ANROPA INTE check_availability än
- Låt kund välja dag först

## Steg 3: Kolla kalender
- När kund valt dag: Säg "Låt mig kolla kalendern..."
- ANROPA check_availability(proposed_date="YYYY-MM-DD", proposed_time="10:00") EN GÅNG
- Fortsätt prata medan du väntar ("...ser precis vad som är ledigt...")
- Funktionen returnerar ALLA tider för hela dagen (cachad)
- Om kund frågar om annan tid samma dag: Använd cachad data, ANROPA INTE igen

## Steg 4: Presentera tider
- Baserat på kalender-svar, ge 2-3 alternativ
- Låt kund välja

## Steg 5: Samla e-post
- Fråga efter e-post, be dem säga tydligt
- STAVA HELA e-posten tecken för tecken:
  - "n-i-l-s punkt W-a-l-l-i-n snabel-a gmail punkt com"
- Inkludera "punkt", "snabel-a", siffror etc.
- På svenska kan du anta att om användaren säger "dubbel-v" så menar dem "W"
- Fråga "Stämmer det?"
- Om fel: Fråga vilken del, korrigera

## Steg 6: Bekräfta & avslut
- Sammanfatta mötet: dag, datum, tid, e-post
- Fråga om något mer de vill veta
- Om nej: Vänlig tacka för samtalet och ANROPA end_call(reason="Meeting booked")
- Om frågor: Svara, återgå till frågan om något mer

---

# SAFETY & ESCALATION

**Vid tekniska problem:**
- Kalender svarar inte: "Kalendern svarar inte just nu. Föreslå gärna en tid så bekräftar vi den."
- Osäker på detalj: "Det är något vi går igenom på mötet"

**Vid ointresse:**
- Respektera kundens beslut
- Kort, vänlig avslut utan att vara bitter
- ANROPA end_call(reason="Customer declined")

**Tidsgränser:**
- Om kund säger "gör det kort" (t.ex. kör bil): Snabbare tempo, hoppa över röst-feedback om nödvändigt
- Om långt samtal utan framsteg: Erbjud möte, avsluta vänligt om nej

---

# FINAL REMINDER

Du är en **skicklig säljare som råkar vara AI**, inte **en AI som läser upp säljscript**.

Varje mening:
1. Direkt svar på vad personen sa
2. Talspråk (inte skriftspråk)
3. Max en fråga per tur
"""

        self.agent = Agent(
            instructions=instructions,
            llm=openai.realtime.RealtimeModel(
                model="gpt-realtime",
                voice="marin",
                modalities=["text", "audio"],
                temperature=0.7,
                turn_detection=TurnDetection(
                    type="server_vad",
                    threshold=0.5,
                    prefix_padding_ms=600,
                    silence_duration_ms=1800  # Increased for Swedish rhythm
                ),
                input_audio_transcription=InputAudioTranscription(
                    model="gpt-4o-transcribe",
                    language="sv",
                    prompt="""Swedish business call transcription. Context:
- Meeting booking for AI voice assistant demo
- Common phonetic terms: 'dubbel-v' means letter W (not v-v), 'snabel-a' means @
- Email addresses with Swedish names (common: Andersson, Nilsson, Lindström, Wallin, Vallin)
- Times in 24-hour format (14:00, 10:30)
- Days: måndag, tisdag, onsdag, torsdag, fredag
- Business terminology: möte, demo, AI-röst, leads, kunder
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

    # Extract lead name from room name (format: call-name-timestamp)
    lead_name = "där"  # Swedish default fallback
    logger.info(f"Room name: {ctx.room.name}")

    if "-" in ctx.room.name:
        room_parts = ctx.room.name.split("-")
        logger.info(f"Room parts: {room_parts}")
        if len(room_parts) >= 3:
            # Get second-to-last part (name is between "call-" and "-timestamp")
            lead_name = room_parts[-2]
            logger.info(f"✅ Extracted name from room name: {lead_name}")

    # Extract phone number from SIP participant (if available)
    phone_number = "Unknown"
    for participant in ctx.room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            phone_number = participant.attributes.get('sip.phoneNumber', 'Unknown')
            logger.info(f"📞 SIP caller phone number: {phone_number}")
            break

    # Store phone number in tracker
    tracker.phone_number = phone_number

    # Create single agent
    elsa = ElsaAgent(lead_name=lead_name, phone_number=phone_number)

    # Create session
    session = AgentSession()

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
    await session.start(room=ctx.room, agent=elsa.agent)

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
        logger.info("📞 SIP audio track ready, sending greeting now")

        greeting_sent = True
        greeting = f"Hej {lead_name}, det är Carolina från Profit Media. Passar det att prata nu?"
        logger.info(f"👋 Sending greeting: {greeting}")

        await session.generate_reply(
            instructions=f"Säg EXAKT denna hälsning på svenska: '{greeting}'. Säg INGET annat."
        )

        logger.info("✅ Greeting sent. Natural conversation flow active.")

    except asyncio.TimeoutError:
        logger.warning("⏱️ Timeout waiting for SIP audio track - sending greeting anyway")
        greeting_sent = True
        greeting = f"Hej {lead_name}, det är Carolina från Profit Media. Passar det att prata nu?"
        await session.generate_reply(
            instructions=f"Säg EXAKT denna hälsning på svenska: '{greeting}'. Säg INGET annat."
        )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="elsa-english"
    ))
