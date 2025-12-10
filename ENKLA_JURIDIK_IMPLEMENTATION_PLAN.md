# Enkla Juridik Agent Architecture Evolution
## Implementation Plan v1.0

**Date:** 2025-12-06
**Status:** PLANNING - DO NOT IMPLEMENT YET
**Author:** Senior LiveKit AI Engineer

---

## Executive Summary

This plan details the evolution of the existing Finn AI LiveKit phone agent into a structured legal intake system for Enkla Juridik. The transformation moves from an LLM-driven conversational model (where GPT-Realtime generates full Swedish utterances) to an **orchestrator-driven slot-filling architecture** where the LLM outputs structured JSON and a deterministic dialog policy renders templated responses.

**Key Objectives:**
1. Reduce cognitive drag (fewer turns to collect required information)
2. Eliminate conversational echoing ("Jag forstår att..." loops)
3. Introduce explicit state management with measurable metrics
4. Add liability guardrails outside the LLM where possible

---

## Section 1: Repository & Current Architecture Reconnaissance

### 1.1 LiveKit Integration Analysis

**Core Integration Points:**

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| `AgentSession` | `src/agent.py` | 682-700 | Session with OpenAI RealtimeModel |
| `Agent` | `src/agent.py` | 703-706 | Instructions + tools (NO llm) |
| Room connection | `src/agent.py` | 544 | `await ctx.connect()` |
| Session start | `src/agent.py` | 746 | `await session.start(room=ctx.room, agent=agent)` |
| Participant handler | `src/agent.py` | 827-844 | SIP disconnect cleanup |

**Current Pattern (LiveKit 2025 Best Practice):**
```python
# LLM in AgentSession
session = AgentSession(llm=openai.realtime.RealtimeModel(...))

# Agent has instructions + tools only
agent = Agent(instructions=instructions, tools=[...])

# Start together
await session.start(room=ctx.room, agent=agent)
```

### 1.2 GPT-Realtime Integration Analysis

**Configuration (src/agent.py:683-699):**

```python
RealtimeModel(
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
        language="sv" | "en"
    )
)
```

**Key Characteristics:**
- Streaming audio I/O with Whisper transcription
- Server-side VAD (700ms silence threshold)
- LLM generates full Swedish/English text responses
- Voice synthesis via OpenAI TTS (Marin voice)

### 1.3 Enkla Juridik Prompt/KB Setup

**Location:** `Prompts/Enkla juridik setup/`

| File | Content | Integration Status |
|------|---------|-------------------|
| `system_prompt` | Senior Intake Paralegal role, Case File Matrix | NOT INTEGRATED |
| `sälj_triage_` | ID 1.1, 1.2, 1.5 response blocks | NOT INTEGRATED |
| `TRIAGE OCH DIAGNOS - MATRIS (ID 2.x)` | Category-specific diagnostic questions | NOT INTEGRATED |

**Case File Matrix (Current Definition):**
```
Slot 1: CATEGORY → Arbetsrätt, Familjerätt, Tvist, etc.
Slot 2: MATURITY → Concept vs. Active
Slot 3: DOCUMENT_STATUS → Draft vs. Missing
```

### 1.4 Current State Handling

**ConversationTracker (src/agent.py:374-398):**
```python
@dataclass
class ConversationTracker:
    call_id: str = ""
    lead_name: str = ""
    phone_number: str = ""
    meeting_booked: bool = False          # ← Only outcome tracked
    conversation_items: list = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0
    debug_logs: dict = field(default_factory=dict)
```

**Problems:**
- No explicit conversation state (category, maturity, document_status)
- No slot-filling tracking
- LLM must infer state from full conversation history
- No metrics instrumentation

### 1.5 Architecture Sketch (Current vs. Target)

```
CURRENT ARCHITECTURE:
┌─────────────┐    ┌──────────────────┐    ┌─────────────┐
│  User Audio │───▶│ GPT-Realtime     │───▶│  Swedish    │
│  (SIP)      │    │ (Full Text Gen)  │    │  TTS Audio  │
└─────────────┘    └──────────────────┘    └─────────────┘
                          │
                   Full Swedish prose
                   with embedded logic

TARGET ARCHITECTURE:
┌─────────────┐    ┌──────────────────┐    ┌───────────────┐    ┌─────────────┐
│  User Audio │───▶│ GPT-Realtime     │───▶│ Orchestrator  │───▶│  Swedish    │
│  (SIP)      │    │ (JSON Output)    │    │ (Templates)   │    │  TTS Audio  │
└─────────────┘    └──────────────────┘    └───────────────┘    └─────────────┘
                          │                       │
                   {state, next_action}    Empathy + Template
                   JSON only               = Final utterance
```

---

## Section 2: Conversation State Model Introduction

### 2.1 State Schema Definition

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
import time

class LegalCategory(Enum):
    UNKNOWN = "unknown"
    ARBETSRATT = "arbetsrätt"
    FAMILJERATT = "familjerätt"
    TVIST = "tvist"
    AVTAL = "avtal"
    BOSTAD = "bostad"
    ARV = "arv"
    OTHER = "other"

class CaseMaturity(Enum):
    UNKNOWN = "unknown"
    CONCEPT = "concept"      # They're thinking about it
    ACTIVE = "active"        # There's a deadline/ongoing situation

class DocumentStatus(Enum):
    UNKNOWN = "unknown"
    MISSING = "missing"      # Needs to be created from scratch
    DRAFT_JURIST = "draft_jurist"    # Has draft written by lawyer
    DRAFT_SELF_OR_AI = "draft_self"  # Has draft written by self/AI

@dataclass
class ConversationState:
    """Explicit state object for legal intake conversation."""

    # Core slots (the Case File Matrix)
    category: LegalCategory = LegalCategory.UNKNOWN
    maturity: CaseMaturity = CaseMaturity.UNKNOWN
    document_status: DocumentStatus = DocumentStatus.UNKNOWN

    # Additional context slots
    amount_disputed: Optional[float] = None           # For TVIST
    deadline_date: Optional[str] = None               # For ARBETSRATT (preskription)
    email_collected: Optional[str] = None             # For booking close

    # Conversation flow state
    current_phase: str = "opening"                    # opening, triage, closing
    turns_taken: int = 0
    slots_asked: List[str] = field(default_factory=list)

    # Metrics tracking
    slot_fill_turns: dict = field(default_factory=lambda: {
        "category": 0,
        "maturity": 0,
        "document_status": 0
    })

    # Timestamps
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    def is_ready_for_booking(self) -> bool:
        """Check if we have enough info to transition to booking."""
        # We can book if we know category AND document is missing
        if self.category != LegalCategory.UNKNOWN:
            if self.document_status == DocumentStatus.MISSING:
                return True
            # Or if we know maturity is active (urgent case)
            if self.maturity == CaseMaturity.ACTIVE:
                return True
        return False

    def get_next_empty_slot(self) -> Optional[str]:
        """Return the next slot that needs filling."""
        if self.category == LegalCategory.UNKNOWN:
            return "category"
        if self.document_status == DocumentStatus.UNKNOWN:
            return "document_status"
        if self.maturity == CaseMaturity.UNKNOWN:
            return "maturity"
        return None

    def to_dict(self) -> dict:
        """Serialize for JSON/logging."""
        return {
            "category": self.category.value,
            "maturity": self.maturity.value,
            "document_status": self.document_status.value,
            "amount_disputed": self.amount_disputed,
            "deadline_date": self.deadline_date,
            "email_collected": self.email_collected,
            "current_phase": self.current_phase,
            "turns_taken": self.turns_taken,
            "is_ready_for_booking": self.is_ready_for_booking()
        }
```

### 2.2 State Storage Strategy

**Recommendation:** In-memory per session (attached to `ConversationTracker`)

```python
@dataclass
class ConversationTracker:
    # ... existing fields ...

    # NEW: Explicit state object
    state: ConversationState = field(default_factory=ConversationState)
```

**Rationale:**
- Each call is a single session (~2-10 minutes)
- No need for Redis/external storage
- State travels with the session
- Webhook payload includes final state for backend persistence

### 2.3 State Passing Through Pipeline

**Integration Point:** The state must be passed to the LLM on each turn.

```python
# In the message construction (before LLM call):
context_for_llm = {
    "current_state": tracker.state.to_dict(),
    "user_message": transcribed_text,
    "conversation_history_summary": get_recent_turns(tracker, n=3)
}
```

**Note:** GPT-Realtime currently receives full system prompt + conversation history. We'll modify this to include explicit state in a structured format.

---

## Section 3: LLM JSON Interface Design

### 3.1 JSON Output Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["state_updates", "next_action"],
  "properties": {
    "state_updates": {
      "type": "object",
      "description": "Only include fields that should be updated",
      "properties": {
        "category": {
          "type": "string",
          "enum": ["unknown", "arbetsrätt", "familjerätt", "tvist", "avtal", "bostad", "arv", "other"]
        },
        "maturity": {
          "type": "string",
          "enum": ["unknown", "concept", "active"]
        },
        "document_status": {
          "type": "string",
          "enum": ["unknown", "missing", "draft_jurist", "draft_self"]
        },
        "amount_disputed": {"type": "number"},
        "deadline_date": {"type": "string"}
      }
    },
    "next_action": {
      "type": "string",
      "enum": [
        "ASK_CATEGORY",
        "ASK_DOCUMENT_STATUS",
        "ASK_MATURITY",
        "ASK_AMOUNT",
        "ASK_DEADLINE",
        "CLARIFY_RESPONSE",
        "HANDLE_OBJECTION",
        "TRANSITION_TO_BOOKING",
        "COLLECT_EMAIL",
        "CONFIRM_AND_CLOSE",
        "ESCALATE_CONFUSED",
        "END_CALL_DECLINED"
      ]
    },
    "action_context": {
      "type": "object",
      "description": "Additional context for the action",
      "properties": {
        "objection_type": {
          "type": "string",
          "enum": ["price_question", "not_interested", "need_time", "want_human"]
        },
        "clarification_needed": {"type": "string"},
        "empathy_cue": {
          "type": "string",
          "description": "Brief acknowledgment of user emotion (max 10 words)"
        }
      }
    },
    "reasoning": {
      "type": "string",
      "description": "Brief explanation for debugging (not shown to user)"
    }
  }
}
```

### 3.2 Example LLM Outputs

**Turn 1 - User describes problem:**
```json
{
  "state_updates": {
    "category": "familjerätt"
  },
  "next_action": "ASK_DOCUMENT_STATUS",
  "action_context": {
    "empathy_cue": "Jag har noterat situationen."
  },
  "reasoning": "User mentioned divorce and property division, categorized as familjerätt"
}
```

**Turn 2 - User says document is missing:**
```json
{
  "state_updates": {
    "document_status": "missing"
  },
  "next_action": "TRANSITION_TO_BOOKING",
  "action_context": {
    "empathy_cue": "Förstått."
  },
  "reasoning": "Document missing confirmed, ready to transition to booking"
}
```

### 3.3 System Prompt Changes

**NEW System Prompt Structure:**

```markdown
# ROLE
You are a triage classification engine for Enkla Juridik legal intake.

# YOUR TASK
Analyze the user's message and output a JSON object with:
1. State updates (what we learned)
2. Next action (what the orchestrator should do)

# OUTPUT FORMAT
You MUST output ONLY valid JSON. No Swedish text. No explanations outside JSON.

# STATE CONTEXT (provided each turn)
{current_state_json}

# RECENT CONVERSATION
{last_3_turns}

# CLASSIFICATION RULES

## Category Detection:
- Arbetsrätt: uppsägning, avsked, varsel, anställning
- Familjerätt: skilsmässa, bodelning, vårdnad, äktenskapsförord
- Tvist: pengar, fordran, betalning, skadestånd
- Avtal: kontrakt, avtal (new or review)

## Maturity Detection:
- Active: mentions dates, deadlines, "already happened", legal letters received
- Concept: "thinking about", "might need", "considering"

## Document Status Detection:
- Missing: "behöver upprätta", "finns inget", "saknas"
- Draft by jurist: mentions lawyer involvement
- Draft self/AI: "skrev själv", "ChatGPT", "mall"

# NEXT ACTION RULES

1. If category is UNKNOWN → ASK_CATEGORY
2. If category is known BUT document_status UNKNOWN → ASK_DOCUMENT_STATUS
3. If document_status is "missing" → TRANSITION_TO_BOOKING (stop asking)
4. If document_status is "draft" AND maturity UNKNOWN → ASK_MATURITY
5. If all slots filled OR document is missing → TRANSITION_TO_BOOKING

# OBJECTION HANDLING

If user asks about price → HANDLE_OBJECTION with objection_type: "price_question"
If user says not interested → END_CALL_DECLINED
If user is confused → CLARIFY_RESPONSE

# EXAMPLE OUTPUT
{example_json}
```

### 3.4 Ensuring Reliable JSON Under Streaming

**Challenge:** GPT-Realtime streams audio, and we need to intercept before TTS.

**Solution Options (ranked by feasibility):**

1. **Option A: Use text modality only for decision layer**
   - Configure RealtimeModel with `modalities=["text"]` for decision
   - Parse JSON from text output
   - Feed to orchestrator, which generates Swedish text
   - Send Swedish text through separate TTS call

   **Pros:** Clean separation, reliable JSON
   **Cons:** Adds latency (extra TTS call), breaks current pattern

2. **Option B: Function calling with structured output**
   - Define `process_turn` as a function tool
   - LLM must call function with structured args
   - Orchestrator receives function call, not raw text

   **Pros:** Uses existing LiveKit function tool pattern
   **Cons:** GPT-Realtime function calling may have quirks

3. **Option C: Hybrid - LLM generates both**
   - LLM outputs: `{"json": {...}, "spoken": "Swedish text"}`
   - Parse JSON portion, let spoken portion go to TTS
   - Validate spoken against templates (post-hoc check)

   **Pros:** Minimal latency impact
   **Cons:** LLM still generates Swedish, partially defeats purpose

**Recommendation:** Start with **Option B (Function Calling)** as it aligns with the existing tool pattern in `src/agent.py`.

### 3.5 Response Parsing Implementation

```python
# New file: src/enkla/llm_interface.py

import json
from typing import Optional, Tuple
from dataclasses import dataclass

@dataclass
class LLMDecision:
    state_updates: dict
    next_action: str
    action_context: dict
    reasoning: str
    raw_response: str
    parse_success: bool

def parse_llm_response(raw_output: str) -> LLMDecision:
    """
    Parse LLM JSON output into structured decision.
    Returns LLMDecision with parse_success=False if parsing fails.
    """
    try:
        # Handle potential markdown code blocks
        cleaned = raw_output.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)

        return LLMDecision(
            state_updates=data.get("state_updates", {}),
            next_action=data.get("next_action", "CLARIFY_RESPONSE"),
            action_context=data.get("action_context", {}),
            reasoning=data.get("reasoning", ""),
            raw_response=raw_output,
            parse_success=True
        )
    except json.JSONDecodeError as e:
        # Fallback: if LLM fails to produce JSON, default to safe action
        return LLMDecision(
            state_updates={},
            next_action="CLARIFY_RESPONSE",
            action_context={"error": str(e)},
            reasoning="JSON parse failed",
            raw_response=raw_output,
            parse_success=False
        )
```

---

## Section 4: Orchestrator / Dialog Policy

### 4.1 New Module Structure

```
src/
├── agent.py                    # Existing (will be modified)
├── enkla/
│   ├── __init__.py
│   ├── state.py               # ConversationState class
│   ├── orchestrator.py        # Dialog policy engine
│   ├── templates.py           # Swedish utterance templates
│   ├── llm_interface.py       # JSON parsing
│   ├── metrics.py             # Instrumentation
│   └── guardrails.py          # Safety filters
```

### 4.2 Orchestrator Implementation

```python
# src/enkla/orchestrator.py

from typing import Optional, Tuple
from .state import ConversationState, LegalCategory, DocumentStatus
from .templates import TemplateLibrary
from .llm_interface import LLMDecision

class DialogOrchestrator:
    """
    Deterministic dialog policy that maps (state, action) → utterance.
    Does NOT use LLM for text generation.
    """

    def __init__(self):
        self.templates = TemplateLibrary()

    def get_next_utterance(
        self,
        state: ConversationState,
        decision: LLMDecision
    ) -> Tuple[str, str]:
        """
        Given current state and LLM decision, return:
        - utterance: The Swedish text to speak
        - phase: Updated conversation phase

        Returns (utterance, new_phase)
        """
        action = decision.next_action
        context = decision.action_context

        # Get optional empathy prefix
        empathy = context.get("empathy_cue", "")

        if action == "ASK_CATEGORY":
            template = self.templates.get_slot_question("category")
            return self._combine(empathy, template), "triage"

        elif action == "ASK_DOCUMENT_STATUS":
            # Context-aware: different questions per category
            category = state.category.value
            template = self.templates.get_slot_question(
                "document_status",
                category=category
            )
            return self._combine(empathy, template), "triage"

        elif action == "ASK_MATURITY":
            template = self.templates.get_slot_question("maturity")
            return self._combine(empathy, template), "triage"

        elif action == "ASK_AMOUNT":
            template = self.templates.get_slot_question("amount")
            return self._combine(empathy, template), "triage"

        elif action == "ASK_DEADLINE":
            template = self.templates.get_slot_question("deadline")
            return self._combine(empathy, template), "triage"

        elif action == "HANDLE_OBJECTION":
            objection_type = context.get("objection_type", "generic")
            template = self.templates.get_objection_handler(objection_type)
            return self._combine(empathy, template), state.current_phase

        elif action == "TRANSITION_TO_BOOKING":
            # Build dynamic close based on what we know
            template = self.templates.get_booking_transition(
                category=state.category.value,
                document_status=state.document_status.value
            )
            return template, "closing"

        elif action == "COLLECT_EMAIL":
            template = self.templates.get_email_collection()
            return template, "closing"

        elif action == "CONFIRM_AND_CLOSE":
            template = self.templates.get_confirmation(
                email=state.email_collected
            )
            return template, "closed"

        elif action == "END_CALL_DECLINED":
            template = self.templates.get_polite_close()
            return template, "closed"

        elif action == "CLARIFY_RESPONSE":
            # Generic clarification
            clarification = context.get("clarification_needed", "")
            template = self.templates.get_clarification(clarification)
            return template, state.current_phase

        elif action == "ESCALATE_CONFUSED":
            template = self.templates.get_escalation()
            return template, "closing"

        else:
            # Unknown action - safe fallback
            return self.templates.get_fallback(), state.current_phase

    def _combine(self, empathy: str, question: str) -> str:
        """Combine empathy stem with question template."""
        if empathy:
            return f"{empathy} {question}"
        return question


class DialogPolicy:
    """
    Pure logic for determining next action based on state.
    Used when LLM fails or for validation.
    """

    @staticmethod
    def get_fallback_action(state: ConversationState) -> str:
        """Deterministic fallback when LLM output is invalid."""

        if state.category == LegalCategory.UNKNOWN:
            return "ASK_CATEGORY"

        if state.document_status == DocumentStatus.UNKNOWN:
            return "ASK_DOCUMENT_STATUS"

        if state.document_status == DocumentStatus.MISSING:
            return "TRANSITION_TO_BOOKING"

        if state.is_ready_for_booking():
            return "TRANSITION_TO_BOOKING"

        return "CLARIFY_RESPONSE"
```

### 4.3 Integration with LiveKit Message Loop

**Current Flow (src/agent.py):**
```python
# Line 719-723: Event handler captures transcriptions
@session.on("user_input_transcribed")
def on_user_input_transcribed(event: UserInputTranscribedEvent):
    if event.is_final:
        logger.info(f"🎤 User: {event.transcript}")
```

**Modified Flow:**

```python
# NEW: Intercept before LLM response generation
@session.on("user_input_transcribed")
async def on_user_input_transcribed(event: UserInputTranscribedEvent):
    if event.is_final:
        user_text = event.transcript
        logger.info(f"🎤 User: {user_text}")

        # 1. Get LLM decision (JSON)
        decision = await get_llm_decision(
            user_text=user_text,
            current_state=tracker.state,
            conversation_history=tracker.conversation_items[-3:]
        )

        # 2. Apply state updates
        apply_state_updates(tracker.state, decision.state_updates)
        tracker.state.turns_taken += 1

        # 3. Get orchestrated response
        utterance, new_phase = orchestrator.get_next_utterance(
            tracker.state,
            decision
        )
        tracker.state.current_phase = new_phase

        # 4. Log metrics
        metrics.log_turn(tracker.state, decision, utterance)

        # 5. Apply guardrails
        safe_utterance = guardrails.filter(utterance)

        # 6. Send to TTS
        await session.generate_reply(instructions=f"Say exactly: {safe_utterance}")
```

**Critical Note:** This requires overriding the default GPT-Realtime behavior where it generates both text and audio. We need to intercept at the text stage.

---

## Section 5: Template and Empathy Layer

### 5.1 Template Definitions

```python
# src/enkla/templates.py

class TemplateLibrary:
    """
    Centralized Swedish utterance templates.
    No LLM involvement in text generation.
    """

    # =========== SLOT QUESTIONS ===========

    SLOT_QUESTIONS = {
        "category": {
            "default": "För att koppla dig till rätt specialist: Vilket område rör det sig om? Är det en tvist, arbetsrätt, familjerätt, eller något annat?",
        },

        "document_status": {
            "familjerätt": "Har ni ett undertecknat äktenskapsförord, eller behöver ni hjälp med att upprätta en bodelningshandling från grunden?",
            "arbetsrätt": "Har du redan fått en skriftlig uppsägning, och när skedde det?",
            "tvist": "Finns det skriftliga avtal eller bevis för din fordran?",
            "default": "Är detta ett nytt dokument som behöver upprättas, eller har du ett befintligt utkast du vill att vi granskar?",
        },

        "maturity": {
            "default": "Är det här något akut som pågår nu, eller är det mer på planeringsstadiet?",
        },

        "amount": {
            "default": "Hur stort är beloppet det handlar om?",
        },

        "deadline": {
            "default": "Finns det någon tidsfrist eller deadline vi behöver ta hänsyn till?",
        },
    }

    # =========== OBJECTION HANDLERS ===========

    OBJECTION_HANDLERS = {
        "price_question": "Det beror helt på ärendet, men hos oss får du alltid ett fast pris i förväg. Själva bedömningen och första kontakten med juristen kostar ingenting.",

        "not_interested": "Jag förstår. Tack för din tid. Ha en bra dag.",

        "need_time": "Självklart. När du är redo kan du alltid ringa tillbaka.",

        "want_human": "Jag kopplar dig vidare till en kollega. Ett ögonblick.",

        "generic": "Jag förstår. Kan jag hjälpa dig med något annat?",
    }

    # =========== BOOKING TRANSITIONS ===========

    BOOKING_TRANSITIONS = {
        "missing": "Det är tydligt att vi behöver upprätta dokumentationen åt dig. Jag registrerar detta ärende så att vår specialist kan ge dig en fast prisoffert. Får jag din e-postadress?",

        "draft": "Bra att du har ett utkast. Jag registrerar ärendet så att vår specialist kan granska det och ge dig en offert. Får jag din e-postadress?",

        "default": "Jag har noterat din situation. En specialist kommer att kontakta dig inom en arbetsdag för en kostnadsfri bedömning. Får jag din e-postadress?",
    }

    # =========== EMPATHY STEMS ===========

    EMPATHY_STEMS = [
        "Jag har noterat det.",
        "Förstått.",
        "Det låter som en viktig fråga.",
        "Tack för informationen.",
        "Jag förstår.",
    ]

    # =========== OTHER TEMPLATES ===========

    EMAIL_COLLECTION = "Får jag din e-postadress så skickar vi bekräftelsen dit?"

    CONFIRMATION = "Perfekt. Jag har registrerat ditt ärende. En specialist kontaktar dig inom en arbetsdag."

    POLITE_CLOSE = "Tack för samtalet. Ha en bra dag."

    FALLBACK = "Ursäkta, kan du upprepa det?"

    ESCALATION = "Jag tror det är bäst att du pratar direkt med en specialist. Kan jag få din e-post så kontaktar de dig?"

    # =========== METHODS ===========

    def get_slot_question(self, slot: str, category: str = None) -> str:
        questions = self.SLOT_QUESTIONS.get(slot, {})
        if category and category in questions:
            return questions[category]
        return questions.get("default", self.FALLBACK)

    def get_objection_handler(self, objection_type: str) -> str:
        return self.OBJECTION_HANDLERS.get(objection_type, self.OBJECTION_HANDLERS["generic"])

    def get_booking_transition(self, category: str, document_status: str) -> str:
        if document_status == "missing":
            return self.BOOKING_TRANSITIONS["missing"]
        elif "draft" in document_status:
            return self.BOOKING_TRANSITIONS["draft"]
        return self.BOOKING_TRANSITIONS["default"]

    def get_email_collection(self) -> str:
        return self.EMAIL_COLLECTION

    def get_confirmation(self, email: str = None) -> str:
        if email:
            return f"{self.CONFIRMATION} Vi skickar bekräftelsen till {email}."
        return self.CONFIRMATION

    def get_polite_close(self) -> str:
        return self.POLITE_CLOSE

    def get_clarification(self, context: str = "") -> str:
        if context:
            return f"Förlåt, jag förstod inte riktigt. {context}"
        return "Förlåt, kan du förtydliga?"

    def get_fallback(self) -> str:
        return self.FALLBACK

    def get_escalation(self) -> str:
        return self.ESCALATION
```

### 5.2 Template Storage Recommendation

**For MVP:** Keep templates in Python code (`templates.py`)

**For Production:** Consider:
- YAML/JSON config files (easier for legal team to edit)
- Database table (for A/B testing)
- CMS integration (for multi-tenant deployments)

---

## Section 6: Case File Matrix as Config/Data

### 6.1 Migration from Prose to Structure

**Current (Prose in KB files):**
```
---START_ID_2.4_FAMILJERÄTT---
CONTEXT: FAMILJERÄTT (Bodelning, skilsmässa).
SLOT 2 FRÅGA: "Har ni ett undertecknat äktenskapsförord..."
---END_ID_2.4_FAMILJERÄTT---
```

**Target (Structured Config):**

```python
# src/enkla/triage_config.py

TRIAGE_MATRIX = {
    "categories": {
        "arbetsrätt": {
            "id": "2.2",
            "keywords": ["uppsägning", "avsked", "varsel", "anställning", "arbetsgivare"],
            "slot_questions": {
                "document_status": "Har du redan fått en skriftlig uppsägning, och när skedde det?",
                "maturity": "Pågår detta nu eller planerar du för framtiden?",
            },
            "strategy": "prescription_clock",
            "strategy_note": "Emphasize time pressure for documentation",
            "typical_documents": ["uppsägningsbrev", "arbetsbetyg", "anställningsavtal"],
        },

        "familjerätt": {
            "id": "2.4",
            "keywords": ["skilsmässa", "bodelning", "vårdnad", "äktenskapsförord", "separation"],
            "slot_questions": {
                "document_status": "Har ni ett undertecknat äktenskapsförord, eller behöver ni hjälp med att upprätta en bodelningshandling från grunden?",
                "maturity": "Är skilsmässan redan inlämnad eller överväger ni fortfarande?",
            },
            "strategy": "documentation_audit",
            "strategy_note": "Identify if starting point is review or creation",
            "typical_documents": ["äktenskapsförord", "bodelningsavtal", "vårdnadsavtal"],
        },

        "tvist": {
            "id": "2.3",
            "keywords": ["pengar", "fordran", "betalning", "skadestånd", "krav", "faktura"],
            "slot_questions": {
                "document_status": "Finns det skriftliga avtal eller bevis för fordran?",
                "amount": "Hur stort är beloppet det tvistas om?",
            },
            "strategy": "cost_benefit_analysis",
            "strategy_note": "Evaluate small claims risk",
            "typical_documents": ["avtal", "faktura", "betalningspåminnelse"],
        },

        "avtal": {
            "id": "2.5",
            "keywords": ["kontrakt", "avtal", "villkor", "klausul"],
            "slot_questions": {
                "document_status": "Behöver du upprätta ett nytt avtal, eller granska ett befintligt?",
            },
            "strategy": "gap_analysis",
            "typical_documents": ["köpeavtal", "hyresavtal", "samarbetsavtal"],
        },
    },

    "objection_blocks": {
        "1.1": {
            "context": "OPENING / ONBOARDING",
            "strategy": "triage_nurse",
            "script": "För att kunna hjälpa dig behöver jag först ställa några frågor för att förstå din situation. Sedan kopplar jag in en specialist som gör en första bedömning kostnadsfritt.",
            "anti_pattern": "Never promise free answers on everything",
        },
        "1.2": {
            "context": "PRICE QUESTION EARLY",
            "strategy": "anchor_fixed_price",
            "script": "Det beror helt på ärendet, men hos oss får du alltid ett fast pris i förväg, så du slipper tickande klockor. Själva bedömningen vi gör nu och första kontakten med juristen kostar ingenting.",
            "anti_pattern": "Never guess a figure",
        },
        "1.5": {
            "context": "CLOSE / DATA COLLECTED",
            "strategy": "commitment",
            "script": "Tack. Jag har nu skapat ett ärende. En specialist kommer att titta på detta och kontakta dig inom en arbetsdag (oftast snabbare) för din kostnadsfria bedömning.",
            "anti_pattern": "Never leave with 'Vi hörs' - be specific",
        },
    },
}
```

### 6.2 Config Consumption in Orchestrator

```python
# In orchestrator.py

from .triage_config import TRIAGE_MATRIX

def get_category_specific_question(category: str, slot: str) -> str:
    """Get the category-specific question for a slot."""
    cat_config = TRIAGE_MATRIX["categories"].get(category, {})
    questions = cat_config.get("slot_questions", {})
    return questions.get(slot) or TemplateLibrary.SLOT_QUESTIONS[slot]["default"]

def detect_category_from_keywords(text: str) -> Optional[str]:
    """Fallback category detection without LLM."""
    text_lower = text.lower()
    for category, config in TRIAGE_MATRIX["categories"].items():
        for keyword in config["keywords"]:
            if keyword in text_lower:
                return category
    return None
```

### 6.3 RAG De-emphasis Plan

**Current:** Text-based KB files loaded into LLM context
**Target:** Remove RAG, use structured config

**Migration Steps:**
1. Extract all actionable data from `Enkla juridik setup/` files into `triage_config.py`
2. Update system prompt to NOT mention knowledge base lookup
3. Remove any RAG/retrieval code if present
4. Keep original files as documentation/reference only

---

## Section 7: Metrics & Logging

### 7.1 Metric Definitions

| Metric | Formula | Target | Purpose |
|--------|---------|--------|---------|
| **T_slots** | Avg turns to fill (category + maturity + document_status) | ≤4 turns | Measures efficiency |
| **P_advice** | % conversations with legal advice patterns | <5% | Liability compliance |
| **R_echo** | % turns where opening phrase duplicates recent turn | <10% | Detects echoing bug |
| **T_close** | % conversations reaching correct booking when eligible | >80% | Conversion tracking |

### 7.2 Implementation

```python
# src/enkla/metrics.py

import re
import logging
from typing import List, Dict
from dataclasses import dataclass, field
import time

logger = logging.getLogger("enkla-metrics")

@dataclass
class ConversationMetrics:
    """Track metrics for a single conversation."""

    call_id: str

    # T_slots tracking
    category_filled_at_turn: int = 0
    maturity_filled_at_turn: int = 0
    document_status_filled_at_turn: int = 0

    # P_advice tracking
    advice_patterns_detected: List[str] = field(default_factory=list)

    # R_echo tracking
    echo_turns: List[int] = field(default_factory=list)

    # T_close tracking
    booking_eligible: bool = False
    booking_reached: bool = False

    # Raw data
    total_turns: int = 0
    agent_utterances: List[str] = field(default_factory=list)

    def compute_t_slots(self) -> float:
        """Average turns to fill slots."""
        filled = []
        if self.category_filled_at_turn > 0:
            filled.append(self.category_filled_at_turn)
        if self.maturity_filled_at_turn > 0:
            filled.append(self.maturity_filled_at_turn)
        if self.document_status_filled_at_turn > 0:
            filled.append(self.document_status_filled_at_turn)

        if not filled:
            return 0.0
        return sum(filled) / len(filled)

    def compute_p_advice(self) -> float:
        """Percentage of turns with advice patterns."""
        if self.total_turns == 0:
            return 0.0
        return len(self.advice_patterns_detected) / self.total_turns * 100

    def compute_r_echo(self) -> float:
        """Percentage of turns with echo patterns."""
        if self.total_turns == 0:
            return 0.0
        return len(self.echo_turns) / self.total_turns * 100

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "t_slots": self.compute_t_slots(),
            "p_advice": self.compute_p_advice(),
            "r_echo": self.compute_r_echo(),
            "t_close": 1.0 if (self.booking_eligible and self.booking_reached) else 0.0,
            "total_turns": self.total_turns,
            "advice_patterns": self.advice_patterns_detected,
            "echo_turns": self.echo_turns,
        }


class MetricsCollector:
    """Collect and log conversation metrics."""

    # Legal advice patterns to detect
    ADVICE_PATTERNS = [
        r"du borde.*(?:stämma|säga upp|kräva)",
        r"mitt råd är",
        r"jag rekommenderar att du",
        r"du har rätt till",
        r"enligt lagen.*ska",
        r"du kan kräva",
        r"skriv följande.*klausul",
    ]

    def __init__(self):
        self.conversations: Dict[str, ConversationMetrics] = {}

    def init_conversation(self, call_id: str) -> ConversationMetrics:
        metrics = ConversationMetrics(call_id=call_id)
        self.conversations[call_id] = metrics
        return metrics

    def log_turn(
        self,
        call_id: str,
        turn_number: int,
        state_before: dict,
        state_after: dict,
        agent_utterance: str,
        recent_utterances: List[str]
    ):
        """Log a single turn and update metrics."""
        metrics = self.conversations.get(call_id)
        if not metrics:
            metrics = self.init_conversation(call_id)

        metrics.total_turns = turn_number
        metrics.agent_utterances.append(agent_utterance)

        # Track slot fill timing
        if state_before.get("category") == "unknown" and state_after.get("category") != "unknown":
            metrics.category_filled_at_turn = turn_number
            logger.info(f"📊 [T_slots] Category filled at turn {turn_number}")

        if state_before.get("document_status") == "unknown" and state_after.get("document_status") != "unknown":
            metrics.document_status_filled_at_turn = turn_number
            logger.info(f"📊 [T_slots] Document status filled at turn {turn_number}")

        if state_before.get("maturity") == "unknown" and state_after.get("maturity") != "unknown":
            metrics.maturity_filled_at_turn = turn_number
            logger.info(f"📊 [T_slots] Maturity filled at turn {turn_number}")

        # Check for advice patterns
        advice_found = self._check_advice_patterns(agent_utterance)
        if advice_found:
            metrics.advice_patterns_detected.extend(advice_found)
            logger.warning(f"⚠️ [P_advice] Legal advice pattern detected: {advice_found}")

        # Check for echo patterns
        if self._check_echo_pattern(agent_utterance, recent_utterances):
            metrics.echo_turns.append(turn_number)
            logger.warning(f"⚠️ [R_echo] Echo pattern detected at turn {turn_number}")

    def log_booking_eligibility(self, call_id: str, eligible: bool):
        metrics = self.conversations.get(call_id)
        if metrics:
            metrics.booking_eligible = eligible

    def log_booking_reached(self, call_id: str, reached: bool):
        metrics = self.conversations.get(call_id)
        if metrics:
            metrics.booking_reached = reached
            logger.info(f"📊 [T_close] Booking reached: {reached}")

    def _check_advice_patterns(self, text: str) -> List[str]:
        """Check for disallowed legal advice patterns."""
        found = []
        text_lower = text.lower()
        for pattern in self.ADVICE_PATTERNS:
            if re.search(pattern, text_lower):
                found.append(pattern)
        return found

    def _check_echo_pattern(self, current: str, recent: List[str]) -> bool:
        """Check if opening phrase is similar to recent turns."""
        if not recent or len(current) < 20:
            return False

        # Get first 10 words of current
        current_start = " ".join(current.split()[:10]).lower()

        for prev in recent[-3:]:  # Last 3 turns
            prev_start = " ".join(prev.split()[:10]).lower()
            # Simple similarity: more than 60% word overlap
            current_words = set(current_start.split())
            prev_words = set(prev_start.split())
            if len(current_words) > 0:
                overlap = len(current_words & prev_words) / len(current_words)
                if overlap > 0.6:
                    return True
        return False

    def finalize_conversation(self, call_id: str) -> dict:
        """Finalize and return conversation metrics."""
        metrics = self.conversations.get(call_id)
        if metrics:
            result = metrics.to_dict()
            logger.info(f"📊 Final metrics for {call_id}: {result}")
            return result
        return {}


# Global collector instance
metrics_collector = MetricsCollector()
```

### 7.3 Instrumentation Points in agent.py

```python
# In entrypoint(), after session starts:
from enkla.metrics import metrics_collector

# Initialize
call_metrics = metrics_collector.init_conversation(ctx.room.name)

# In on_user_input_transcribed handler:
state_before = tracker.state.to_dict()
# ... process turn ...
state_after = tracker.state.to_dict()

metrics_collector.log_turn(
    call_id=ctx.room.name,
    turn_number=tracker.state.turns_taken,
    state_before=state_before,
    state_after=state_after,
    agent_utterance=utterance,
    recent_utterances=[item["content"] for item in tracker.conversation_items[-3:] if item["role"] == "assistant"]
)

# Before booking transition:
if tracker.state.is_ready_for_booking():
    metrics_collector.log_booking_eligibility(ctx.room.name, True)

# When booking action taken:
if decision.next_action == "TRANSITION_TO_BOOKING":
    metrics_collector.log_booking_reached(ctx.room.name, True)

# In shutdown callback:
final_metrics = metrics_collector.finalize_conversation(ctx.room.name)
tracker.debug_logs["metrics"] = final_metrics
```

### 7.4 Storage Strategy

**Phase 1 (MVP):** Include in webhook payload
```python
payload = {
    # ... existing fields ...
    "metrics": final_metrics,
}
```

**Phase 2 (Production):**
- Log to structured logging service (e.g., Datadog, CloudWatch)
- Store in analytics DB for dashboards
- Set up alerting on P_advice > threshold

---

## Section 8: Liability Guardrails & Safety Filters

### 8.1 LLM System Prompt Guardrails

**Additions to JSON prompt:**

```markdown
# FORBIDDEN ACTIONS

You MUST NOT output state_updates or next_action that would lead to:

1. **Legal Advice**: Never suggest specific legal actions (sue, claim, demand)
2. **Document Content**: Never suggest what should be IN a document
3. **Price Quotes**: Never give specific prices or estimates
4. **Deadline Promises**: Never promise specific response times beyond "within one business day"
5. **Case Assessment**: Never say whether a case is "strong" or "weak"

If user asks for any of the above, output:
{
  "next_action": "HANDLE_OBJECTION",
  "action_context": {"objection_type": "wants_advice"},
  "reasoning": "User asking for legal advice - must redirect"
}
```

### 8.2 Post-Processing Safety Filters

```python
# src/enkla/guardrails.py

import re
from typing import Tuple, List
import logging

logger = logging.getLogger("enkla-guardrails")

class SafetyFilter:
    """
    Post-processing safety filters applied AFTER orchestrator output,
    BEFORE TTS rendering.
    """

    # Patterns that should NEVER appear in agent speech
    FORBIDDEN_PATTERNS = [
        # Legal advice patterns
        (r"du borde stämma", "legal_advice", "Suggests suing"),
        (r"du har rätt till", "legal_advice", "Claims rights"),
        (r"du kan kräva", "legal_advice", "Suggests demanding"),
        (r"enligt \d+ kap.*§", "legal_citation", "Cites specific law"),
        (r"lagen säger att", "legal_advice", "Interprets law"),

        # Price patterns
        (r"\d+\s*(kr|kronor|sek)", "price_quote", "Mentions specific price"),
        (r"kostar (cirka|ungefär|ca)\s*\d+", "price_quote", "Estimates price"),

        # Document content patterns
        (r"klausulen borde innehålla", "document_content", "Suggests clause content"),
        (r"skriv att", "document_content", "Dictates content"),

        # Overly specific promises
        (r"inom \d+ (timmar|minuter)", "timing_promise", "Too specific timing"),
        (r"garantera", "guarantee", "Makes guarantees"),
    ]

    # Replacement templates for detected patterns
    SAFE_REPLACEMENTS = {
        "legal_advice": "Det är något en jurist behöver bedöma.",
        "price_quote": "Priset beror på ärendet, men du får alltid en offert i förväg.",
        "document_content": "Juristen hjälper dig med innehållet.",
        "timing_promise": "inom en arbetsdag",
        "legal_citation": "",  # Remove entirely
        "guarantee": "göra vårt bästa för att",
    }

    def filter(self, utterance: str) -> Tuple[str, List[str]]:
        """
        Filter utterance for safety.
        Returns (filtered_utterance, list_of_violations).
        """
        violations = []
        filtered = utterance

        for pattern, category, description in self.FORBIDDEN_PATTERNS:
            matches = re.findall(pattern, filtered, re.IGNORECASE)
            if matches:
                violations.append(f"{category}: {description}")

                # Apply replacement
                replacement = self.SAFE_REPLACEMENTS.get(category, "")
                if replacement:
                    filtered = re.sub(pattern, replacement, filtered, flags=re.IGNORECASE)
                else:
                    filtered = re.sub(pattern, "", filtered, flags=re.IGNORECASE)

        # Clean up whitespace
        filtered = re.sub(r'\s+', ' ', filtered).strip()

        if violations:
            logger.warning(f"⚠️ Safety filter triggered: {violations}")
            logger.info(f"Original: {utterance}")
            logger.info(f"Filtered: {filtered}")

        return filtered, violations

    def validate_action(self, action: str, context: dict) -> Tuple[bool, str]:
        """
        Validate that an action is safe to execute.
        Returns (is_safe, reason_if_unsafe).
        """
        # Prevent certain actions without proper context
        if action == "CONFIRM_AND_CLOSE" and not context.get("email_collected"):
            return False, "Cannot close without email"

        if action == "TRANSITION_TO_BOOKING":
            # Must have at least category
            if context.get("category") == "unknown":
                return False, "Cannot book without category"

        return True, ""


# Convenience function
def filter_utterance(utterance: str) -> str:
    """Apply safety filter and return safe utterance."""
    filter_instance = SafetyFilter()
    safe_utterance, _ = filter_instance.filter(utterance)
    return safe_utterance
```

### 8.3 Error Handling & Fallbacks

```python
# In orchestrator integration:

async def process_turn(user_text: str, tracker: ConversationTracker, session: AgentSession):
    """Main turn processing with error handling."""

    try:
        # 1. Get LLM decision
        decision = await get_llm_decision(user_text, tracker.state)

        # 2. Validate decision
        if not decision.parse_success:
            logger.warning("LLM JSON parse failed, using fallback")
            fallback_action = DialogPolicy.get_fallback_action(tracker.state)
            decision.next_action = fallback_action

        # 3. Validate action is safe
        is_safe, reason = SafetyFilter().validate_action(
            decision.next_action,
            tracker.state.to_dict()
        )
        if not is_safe:
            logger.warning(f"Action validation failed: {reason}")
            decision.next_action = "CLARIFY_RESPONSE"

        # 4. Get orchestrated utterance
        utterance, new_phase = orchestrator.get_next_utterance(tracker.state, decision)

        # 5. Apply safety filter
        safe_utterance = filter_utterance(utterance)

        # 6. Send to TTS
        await session.generate_reply(instructions=f"Say exactly: {safe_utterance}")

    except Exception as e:
        logger.error(f"Turn processing error: {e}", exc_info=True)
        # Ultimate fallback
        await session.generate_reply(
            instructions="Say: Ursäkta, jag hade ett tekniskt problem. Kan du upprepa det?"
        )
```

---

## Section 9: LiveKit/GPT-Realtime Integration Considerations

### 9.1 One Question Per TTS Turn

**Current Problem:** LLM might generate multiple sentences/questions in one response.

**Solution in Orchestrator:**
```python
def get_next_utterance(self, state, decision) -> Tuple[str, str]:
    # ... existing logic ...

    # Enforce single question
    utterance = self._enforce_single_question(utterance)
    return utterance, phase

def _enforce_single_question(self, text: str) -> str:
    """Ensure only one question per utterance."""
    # Split on question marks
    parts = text.split("?")
    if len(parts) > 2:  # More than one question
        # Keep only the first question + any preceding statement
        return parts[0] + "?"
    return text
```

### 9.2 Handling Streaming and Interruptions

**GPT-Realtime Streaming Behavior:**
- Audio is streamed as generated
- User can interrupt mid-response
- Interruption triggers new transcription event

**Integration Strategy:**

```python
# Track interruption state
class InterruptionHandler:
    def __init__(self):
        self.current_utterance_complete = True
        self.pending_utterance: Optional[str] = None

    def on_generation_started(self, utterance: str):
        self.current_utterance_complete = False
        self.pending_utterance = utterance

    def on_generation_complete(self):
        self.current_utterance_complete = True
        self.pending_utterance = None

    def on_user_interruption(self):
        if not self.current_utterance_complete:
            logger.info(f"User interrupted: {self.pending_utterance[:50]}...")
            # Don't repeat interrupted content
            self.current_utterance_complete = True
            return True
        return False

# In event handlers:
@session.on("agent_speech_started")
def on_speech_started():
    interruption_handler.on_generation_started(current_utterance)

@session.on("agent_speech_stopped")
def on_speech_stopped():
    interruption_handler.on_generation_complete()

@session.on("user_input_transcribed")
def on_transcribed(event):
    if event.is_final:
        was_interrupted = interruption_handler.on_user_interruption()
        # Continue processing...
```

### 9.3 Turn-Taking Adjustments

**Current Settings (src/agent.py:688-692):**
```python
turn_detection=TurnDetection(
    type="server_vad",
    threshold=0.55,
    prefix_padding_ms=200,
    silence_duration_ms=700
)
```

**Recommendations for Legal Intake:**
- Keep `silence_duration_ms=700` (already balanced)
- Consider increasing `threshold=0.6` if false triggers occur during user thinking
- Monitor if users need more time to think about legal matters

### 9.4 Transcript Assembly

**Current Tracking (src/agent.py:709-717):**
```python
@session.on("conversation_item_added")
def on_conversation_item_added(event: ConversationItemAddedEvent):
    tracker.add_item(
        role=event.item.role,
        content=event.item.text_content,
        timestamp=event.created_at
    )
```

**Enhancement for Metrics:**
```python
@session.on("conversation_item_added")
def on_conversation_item_added(event: ConversationItemAddedEvent):
    # Store with additional metadata for metrics
    tracker.add_item(
        role=event.item.role,
        content=event.item.text_content,
        timestamp=event.created_at
    )

    # If assistant turn, check for metrics
    if event.item.role == "assistant":
        recent_assistant = [
            item["content"] for item in tracker.conversation_items
            if item["role"] == "assistant"
        ][-3:]

        # Echo detection
        if metrics_collector._check_echo_pattern(event.item.text_content, recent_assistant):
            logger.warning("Echo pattern in real-time")
```

---

## Section 10: Risk Analysis and Migration Strategy

### 10.1 Risk Assessment per Component

| Component | Risk Level | Impact on Current Flow | Mitigation |
|-----------|------------|----------------------|------------|
| State Model Addition | **LOW** | Additive - doesn't break existing | Feature flag |
| JSON LLM Interface | **HIGH** | Replaces current text generation | Shadow mode first |
| Orchestrator | **MEDIUM** | New code path for responses | Gradual rollout |
| Templates | **LOW** | Additive - stored separately | Easy rollback |
| Triage Config | **LOW** | Replaces prose KB (not in use) | None needed |
| Metrics | **LOW** | Logging only | No impact |
| Guardrails | **LOW** | Post-processing filter | Bypass flag |

### 10.2 Migration Phases

#### Phase 1: Foundation (LOW RISK)
**Duration:** 1-2 days
**Changes:**
- Add `src/enkla/` module structure
- Implement `ConversationState` dataclass
- Implement `MetricsCollector`
- Add state to `ConversationTracker`

**Testing:**
- Unit tests for state model
- Verify no impact on current calls

**Feature Flag:** None needed (additive)

#### Phase 2: Metrics & Logging (LOW RISK)
**Duration:** 1-2 days
**Changes:**
- Integrate `MetricsCollector` into agent.py
- Add metrics to webhook payload
- Set up basic logging

**Testing:**
- Run test calls, verify metrics appear in logs
- Verify webhook payload includes metrics

**Feature Flag:** `ENABLE_ENKLA_METRICS=true`

#### Phase 3: Shadow Mode JSON (MEDIUM RISK)
**Duration:** 3-5 days
**Changes:**
- Implement `llm_interface.py` with JSON parsing
- Create parallel LLM call (JSON prompt) alongside current flow
- Log JSON decisions without acting on them
- Compare LLM decisions to actual flow

**Testing:**
- Run 50+ test calls
- Analyze JSON output validity
- Compare slot detection accuracy

**Feature Flag:** `ENABLE_SHADOW_JSON=true`

#### Phase 4: Templates & Orchestrator (MEDIUM RISK)
**Duration:** 3-5 days
**Changes:**
- Implement `templates.py` with all Swedish utterances
- Implement `orchestrator.py` with dialog policy
- Implement `guardrails.py` with safety filters

**Testing:**
- Unit tests for all template combinations
- Unit tests for guardrail patterns
- Integration tests with mock LLM responses

**Feature Flag:** None (not yet connected)

#### Phase 5: A/B Routing (HIGH RISK, CONTROLLED)
**Duration:** 1-2 weeks
**Changes:**
- Add routing logic in `agent.py`
- 10% of calls use new orchestrator
- 90% continue with current flow
- Monitor all metrics

**Testing:**
- Real calls with volunteer users
- Compare T_slots, R_echo between groups
- Monitor for errors/failures

**Feature Flag:** `ORCHESTRATOR_ROLLOUT_PERCENT=10`

#### Phase 6: Full Migration
**Duration:** 1 week
**Changes:**
- Increase rollout to 50%, then 100%
- Remove old flow
- Clean up feature flags

**Success Criteria:**
- T_slots ≤ 4 turns
- R_echo < 10%
- P_advice < 5%
- T_close > 80%
- No increase in call drops

### 10.3 Rollback Plan

Each phase has a clear rollback:

1. **Phase 1-2:** Delete new code, no user impact
2. **Phase 3:** Disable shadow logging
3. **Phase 4:** Not exposed to users yet
4. **Phase 5-6:** Set `ORCHESTRATOR_ROLLOUT_PERCENT=0`

### 10.4 Implementation Order Recommendation

```
Week 1:
├── Day 1-2: Phase 1 (Foundation)
└── Day 3-4: Phase 2 (Metrics)

Week 2:
├── Day 1-3: Phase 3 (Shadow JSON)
└── Day 4-5: Phase 4 (Templates/Orchestrator)

Week 3:
├── Day 1-2: Integration testing
└── Day 3-5: Phase 5 (A/B at 10%)

Week 4:
├── Day 1-3: Monitor and tune
└── Day 4-5: Increase to 50%

Week 5:
├── Day 1-3: Full rollout (100%)
└── Day 4-5: Cleanup and documentation
```

---

## Appendix A: File Changes Summary

### New Files to Create

```
src/enkla/
├── __init__.py
├── state.py           # ConversationState, LegalCategory, etc.
├── orchestrator.py    # DialogOrchestrator, DialogPolicy
├── templates.py       # TemplateLibrary
├── llm_interface.py   # LLMDecision, parse_llm_response
├── metrics.py         # MetricsCollector, ConversationMetrics
├── guardrails.py      # SafetyFilter
└── triage_config.py   # TRIAGE_MATRIX structured config
```

### Files to Modify

```
src/agent.py
├── Import enkla modules
├── Add state to ConversationTracker
├── Add metrics collection points
├── Add orchestrator integration (behind feature flag)
└── Modify event handlers for new flow
```

### Files to Archive (Not Delete)

```
Prompts/Enkla juridik setup/
├── system_prompt           → Reference only
├── sälj_triage_            → Reference only
└── TRIAGE OCH DIAGNOS...   → Reference only
```

---

## Appendix B: Key Technical Decisions

### Decision 1: LLM Role Change
**Choice:** JSON output only, no Swedish text generation
**Rationale:** Deterministic utterances prevent echoing and ensure consistency
**Trade-off:** More upfront template work, but more control

### Decision 2: Function Calling vs. Text Parsing
**Choice:** Start with function calling pattern
**Rationale:** Aligns with existing tool pattern in agent.py
**Fallback:** Text parsing if function calling has issues

### Decision 3: State Storage
**Choice:** In-memory per session
**Rationale:** Calls are short-lived, no persistence needed during call
**Alternative:** Redis if multi-agent handoffs needed

### Decision 4: Metrics Storage
**Choice:** Webhook payload + structured logs
**Rationale:** Minimal infrastructure changes, backend can persist
**Evolution:** Add dedicated metrics DB in production

---

## Appendix C: Testing Checklist

### Unit Tests Required
- [ ] `ConversationState.is_ready_for_booking()`
- [ ] `ConversationState.get_next_empty_slot()`
- [ ] `parse_llm_response()` with valid JSON
- [ ] `parse_llm_response()` with invalid JSON
- [ ] `DialogOrchestrator.get_next_utterance()` for all actions
- [ ] `TemplateLibrary` for all slot/category combinations
- [ ] `SafetyFilter.filter()` for all forbidden patterns
- [ ] `MetricsCollector._check_echo_pattern()`
- [ ] `MetricsCollector._check_advice_patterns()`

### Integration Tests Required
- [ ] Full turn cycle: transcription → LLM → orchestrator → TTS
- [ ] State persistence across turns
- [ ] Metrics accumulation across conversation
- [ ] Guardrail filtering in live flow
- [ ] Webhook payload includes new fields

### E2E Tests Required
- [ ] Complete intake call reaching booking
- [ ] Objection handling (price question)
- [ ] User decline flow
- [ ] Interruption handling
- [ ] Timeout handling

---

**END OF IMPLEMENTATION PLAN**

*This document should be reviewed and approved before implementation begins.*
