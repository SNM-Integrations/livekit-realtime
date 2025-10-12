# LiveKit Outbound AI Agent System - Architecture Documentation

## Table of Contents
1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Components](#components)
4. [Data Flow](#data-flow)
5. [Agent Configuration](#agent-configuration)
6. [Language Routing System](#language-routing-system)
7. [Deployment](#deployment)
8. [Testing](#testing)
9. [File Structure](#file-structure)

---

## System Overview

This system provides AI-powered outbound calling agents using LiveKit's real-time communication platform integrated with OpenAI's GPT Realtime API. The system supports multiple agents with different languages and use cases:

- **Finn AI Agent** (bilingual): English and Swedish meeting booking agent
- **Carolina Agent**: Swedish-only Profit Media sales agent

### Key Features
- Dynamic language selection per call
- Real-time voice conversation with AI
- Calendar integration for meeting booking
- SIP telephony for actual phone calls
- Webhook-based call triggering via Railway
- Conversation tracking and recording

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         User/Website                             │
│                    (Form with language selection)                │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ HTTP POST with language param
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Railway Trigger Service                       │
│                  (trigger_call_simple.py)                        │
│                                                                   │
│  Routing Logic:                                                  │
│  • "Carolina" → elsa-english (CA_WjF7u2U7wd7A)                  │
│  • "English" or "Swedish" → elsa-swedish (CA_uG7inqdgtPgQ)     │
│                                                                   │
│  Metadata: {lead_name, phone_number, language}                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                   ┌─────────┴──────────┐
                   │                    │
                   ▼                    ▼
      ┌────────────────────┐  ┌────────────────────┐
      │  LiveKit Agent     │  │  LiveKit Agent     │
      │  elsa-swedish      │  │  elsa-english      │
      │  (Finn AI)         │  │  (Carolina)        │
      │  CA_uG7inqdgtPgQ   │  │  CA_WjF7u2U7wd7A   │
      └──────────┬─────────┘  └──────────┬─────────┘
                 │                       │
                 │ Reads metadata        │ Fixed config
                 │ Configures language   │ (Swedish only)
                 │                       │
                 ▼                       ▼
      ┌────────────────────┐  ┌────────────────────┐
      │ English prompt +   │  │ Carolina prompt +  │
      │ transcription OR   │  │ Swedish transcript │
      │ Swedish prompt +   │  │                    │
      │ transcription      │  │                    │
      └──────────┬─────────┘  └──────────┬─────────┘
                 │                       │
                 └───────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │   OpenAI GPT-4       │
                  │   Realtime API       │
                  │   + Whisper          │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │   SIP Trunk          │
                  │   (Phone Network)    │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │   End User Phone     │
                  └──────────────────────┘
```

---

## Components

### 1. Railway Trigger Service (`trigger_call_simple.py`)

**Purpose**: HTTP webhook endpoint that initiates outbound calls.

**Key Functions**:
- Accepts POST requests with JSON payload: `{name, phone, language}`
- Validates phone number format (E.164)
- Routes to correct LiveKit agent based on language parameter
- Creates LiveKit agent dispatch with metadata
- Initiates SIP call to phone number

**API Endpoint**:
```
POST https://caring-wonder-production.up.railway.app/trigger-call
Content-Type: application/json

{
  "name": "John Doe",
  "phone": "+460723161614",
  "language": "English" | "Swedish" | "Carolina"
}
```

**Response**:
```json
{
  "success": true,
  "room_name": "call_johndoe_1760273919",
  "message": "Call initiated to +460723161614"
}
```

---

### 2. Finn AI Agent (`src/agent.py`)

**Purpose**: Bilingual meeting booking agent that dynamically configures itself based on metadata.

**Agent ID**: `CA_uG7inqdgtPgQ`
**Agent Name**: `elsa-swedish`

**Dynamic Configuration**:
The agent reads the `language` parameter from job metadata and configures:

| Language Parameter | Prompt File | Transcription | Greeting |
|-------------------|-------------|---------------|----------|
| "English" | `Prompts/english_agent_prompt.md` | `language="en"` | "Hi {name}, this is Elsa from Finn AI..." |
| "Swedish" | `Prompts/swedish_agent_prompt.md` | `language="sv"` | "Hej {name}, det är Elsa från Finn AI..." |

**Features**:
- Calendar availability checking (via n8n webhook)
- Meeting booking
- Natural conversation flow
- Automatic call termination
- Conversation tracking and webhooks

**Function Tools**:
1. `check_availability(start_datetime, end_datetime)` - Check calendar slots
2. `end_call(reason)` - Properly terminate the call

---

### 3. Carolina Agent (`agents/english/agent.py`)

**Purpose**: Swedish-only Profit Media sales agent with fixed configuration.

**Agent ID**: `CA_WjF7u2U7wd7A`
**Agent Name**: `elsa-english`

**Fixed Configuration**:
- Prompt: `Prompts/carolina_agent_prompt.md`
- Language: Swedish (`"sv"`)
- Greeting: "Hej {name}, det är Carolina från Profit Media. Passar det att prata nu?"

**Features**:
- Consultative sales approach for Profit Media services
- Service offerings: SEO, Google Ads, Web Development, Meta Ads, Review Booster
- Meeting booking with service interest tracking

**Function Tools**:
1. `check_availability(start_datetime, end_datetime)` - Check calendar slots
2. `profit_media_meeting_booker(...)` - Book meetings with service details
3. `end_call(reason)` - Properly terminate the call

---

## Data Flow

### Call Initiation Flow

1. **User fills form** on website, selects language preference
2. **Website sends webhook** to Railway trigger with:
   ```json
   {
     "name": "User Name",
     "phone": "+46XXXXXXXXX",
     "language": "English|Swedish|Carolina"
   }
   ```
3. **Railway trigger**:
   - Validates phone number (converts Swedish format if needed)
   - Selects agent based on language:
     - Carolina → `elsa-english`
     - English/Swedish → `elsa-swedish`
   - Creates agent dispatch with metadata:
     ```json
     {
       "lead_name": "User Name",
       "phone_number": "+46XXXXXXXXX",
       "language": "English|Swedish|Carolina"
     }
     ```
   - Creates SIP participant to initiate phone call

4. **LiveKit Agent**:
   - Receives dispatch
   - Reads metadata from `ctx.job.metadata`
   - Configures language settings (prompt, transcription, greeting)
   - Connects to room
   - Waits for SIP participant to connect
   - Sends greeting in appropriate language

5. **OpenAI Realtime API**:
   - Processes audio in real-time
   - Transcribes user speech via Whisper
   - Generates AI responses
   - Synthesizes speech output

6. **Conversation**:
   - User and AI converse naturally
   - Agent can call function tools (calendar, booking)
   - Agent tracks conversation for webhooks
   - Call ends when conversation completes

7. **Cleanup**:
   - Conversation data sent to webhook
   - Room deleted
   - SIP connection terminated

---

## Agent Configuration

### Metadata-Based Configuration (LiveKit Best Practice)

The Finn AI agent uses LiveKit's recommended metadata approach for dynamic configuration:

**How it works**:
1. Railway passes metadata when creating dispatch:
   ```python
   await lkapi.agent_dispatch.create_dispatch(
       api.CreateAgentDispatchRequest(
           agent_name=agent_name,
           room=room_name,
           metadata=json.dumps({
               "lead_name": lead_name,
               "phone_number": phone_number,
               "language": language
           })
       )
   )
   ```

2. Agent reads metadata in entrypoint:
   ```python
   metadata = json.loads(ctx.job.metadata)
   language = metadata.get("language", "English")
   ```

3. Agent configures itself:
   ```python
   if language == "Swedish":
       prompt_file = "Prompts/swedish_agent_prompt.md"
       transcription_language = "sv"
       greeting = "Hej..."
   else:
       prompt_file = "Prompts/english_agent_prompt.md"
       transcription_language = "en"
       greeting = "Hi..."
   ```

This approach is recommended by LiveKit for:
- Per-call customization
- Maintaining single agent deployment
- Reducing infrastructure complexity

---

## Language Routing System

### Routing Table

| Language Parameter | Agent | Agent ID | Language Spoken | Use Case |
|-------------------|-------|----------|-----------------|----------|
| `"English"` | elsa-swedish | CA_uG7inqdgtPgQ | English | Finn AI meeting booking |
| `"Swedish"` | elsa-swedish | CA_uG7inqdgtPgQ | Swedish | Finn AI meeting booking |
| `"Carolina"` | elsa-english | CA_WjF7u2U7wd7A | Swedish | Profit Media sales |

### Backward Compatibility

The system maintains backward compatibility with the legacy `country` parameter:

```python
language = data.get('language', data.get('country', 'English'))
```

This means old integrations using `country` parameter will still work:
- `country="SE"` → routes to `elsa-swedish` (defaults to English)
- Other country codes → routes to appropriate agent

---

## Deployment

### LiveKit Agent Deployment

**Finn AI Agent** (main bilingual agent):
```bash
cd c:/Users/Admin/gpt-realtime.new
lk agent deploy
```
This deploys to agent ID: `CA_uG7inqdgtPgQ`

**Carolina Agent** (Profit Media):
```bash
cd c:/Users/Admin/gpt-realtime.new/agents/english
lk agent deploy
```
This deploys to agent ID: `CA_WjF7u2U7wd7A`

### Railway Deployment

The trigger service is deployed on Railway and automatically rebuilds when code is pushed:

```bash
# Railway automatically detects changes and rebuilds
# Manual deployment can be done via Railway CLI or dashboard
```

**Environment Variables Required**:
- `LIVEKIT_URL` - LiveKit server URL
- `LIVEKIT_API_KEY` - API key for LiveKit
- `LIVEKIT_API_SECRET` - API secret for LiveKit
- `OUTBOUND_SIP_TRUNK_ID` - SIP trunk ID for outbound calls
- `PORT` - HTTP server port (default: 8000, Railway uses 8080)

### Environment Variables (`.env.local`)

Both agents require:
```env
LIVEKIT_URL=wss://finn-outbound-ebnrjj6k.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret
OPENAI_API_KEY=your_openai_key
OUTBOUND_SIP_TRUNK_ID=your_sip_trunk_id
WEBHOOK_URL=https://your-webhook-endpoint.com
ENABLE_CALL_RECORDING=false
```

---

## Testing

### Test Commands

**Test English Call**:
```bash
curl -X POST https://caring-wonder-production.up.railway.app/trigger-call \
  -H "Content-Type: application/json" \
  -d '{"name":"TestUser","phone":"+460723161614","language":"English"}'
```

**Expected**: Finn AI agent speaks English, uses English prompt

---

**Test Swedish Call**:
```bash
curl -X POST https://caring-wonder-production.up.railway.app/trigger-call \
  -H "Content-Type: application/json" \
  -d '{"name":"TestUser","phone":"+460723161614","language":"Swedish"}'
```

**Expected**: Finn AI agent speaks Swedish, uses Swedish prompt

---

**Test Carolina Call**:
```bash
curl -X POST https://caring-wonder-production.up.railway.app/trigger-call \
  -H "Content-Type: application/json" \
  -d '{"name":"TestUser","phone":"+460723161614","language":"Carolina"}'
```

**Expected**: Carolina agent speaks Swedish, uses Profit Media prompt

---

### Verification

After triggering a call, verify:
1. Response shows `"success": true`
2. Room name is returned
3. Phone receives call within 2-5 seconds
4. Agent greets in correct language
5. Agent uses correct prompt/personality

---

## File Structure

```
gpt-realtime.new/
├── src/
│   └── agent.py                    # Main Finn AI bilingual agent
│
├── agents/
│   └── english/                    # Carolina agent (Swedish only)
│       ├── agent.py               # Carolina agent code
│       ├── livekit.toml           # Agent config (CA_WjF7u2U7wd7A)
│       ├── Dockerfile             # Container config
│       ├── pyproject.toml         # Python dependencies
│       └── requirements.txt       # Pip requirements
│
├── Prompts/
│   ├── english_agent_prompt.md    # English Finn AI prompt
│   ├── swedish_agent_prompt.md    # Swedish Finn AI prompt
│   └── carolina_agent_prompt.md   # Carolina Profit Media prompt
│
├── trigger_call_simple.py          # Railway HTTP trigger service
├── livekit.toml                    # Main agent config (CA_uG7inqdgtPgQ)
├── Dockerfile                      # Main agent container
├── railway.json                    # Railway deployment config
├── pyproject.toml                  # Python dependencies
├── requirements.txt                # Pip requirements
│
├── .env.local                      # Environment variables (not in git)
├── .env.example                    # Example environment variables
│
└── ARCHITECTURE.md                 # This file

Removed/Cleaned:
├── agents/swedish/                 # ❌ Old unused agent directory
├── Prompts/[old files]            # ❌ 10+ old prompt versions
├── examples/                       # ❌ Example files
├── problems/                       # ❌ Old troubleshooting docs
├── *.md (old blueprints)          # ❌ Old documentation files
└── *_cleanup.py scripts           # ❌ Old utility scripts
```

---

## Key Implementation Details

### 1. Metadata Reading (src/agent.py:546-554)
```python
# Read language from metadata (default to English if not provided)
language = "English"  # Default
try:
    if ctx.job.metadata:
        metadata = json.loads(ctx.job.metadata)
        language = metadata.get("language", "English")
        logger.info(f"📝 Language from metadata: {language}")
except Exception as e:
    logger.warning(f"⚠️ Could not parse metadata, using default language (English): {e}")
```

### 2. Dynamic Prompt Loading (src/agent.py:630-638)
```python
# Load prompt instructions - Dynamic based on language
if language == "Swedish":
    prompt_file = "Prompts/swedish_agent_prompt.md"
    transcription_language = "sv"
else:  # Default to English
    prompt_file = "Prompts/english_agent_prompt.md"
    transcription_language = "en"

logger.info(f"📄 Loading prompt file: {prompt_file} (language: {transcription_language})")
```

### 3. Dynamic Greeting (src/agent.py:745-751)
```python
# Send greeting after delay - Dynamic based on language
if language == "Swedish":
    greeting = f"Hej {lead_name}, det är Elsa från Finn AI. Passar det att prata nu?"
    greeting_instruction = f"Say this greeting in Swedish: '{greeting}' and wait for response."
else:  # English
    greeting = f"Hi {lead_name}, this is Elsa from Finn AI. Is now a good time to talk?"
    greeting_instruction = f"Say this greeting in English: '{greeting}' and wait for response."
```

### 4. Agent Routing (trigger_call_simple.py:109-116)
```python
# Agent selection based on language parameter
# "Carolina" → elsa-english (Swedish Carolina Profit Media agent)
# "English" or "Swedish" → elsa-swedish (Finn AI bilingual agent)
# Backward compatibility: "SE" → elsa-swedish
if language == "Carolina":
    agent_name = "elsa-english"
else:
    agent_name = "elsa-swedish"
```

---

## Troubleshooting

### Common Issues

**Issue**: Call not initiated
- Check Railway logs for errors
- Verify phone number format (must be E.164: +46...)
- Confirm LiveKit credentials are correct

**Issue**: Wrong language spoken
- Verify `language` parameter in request
- Check Railway logs for routing decision
- Confirm agent received correct metadata

**Issue**: Agent doesn't respond
- Check LiveKit dashboard for agent status
- Verify OpenAI API key is valid
- Check agent logs for errors

**Issue**: Call drops immediately
- Verify SIP trunk is active
- Check phone number is valid
- Ensure proper call termination (end_call tool)

---

## Future Enhancements

Potential improvements:
1. Add more languages (Norwegian, Danish, German, etc.)
2. Voice selection per call (currently fixed to "marin")
3. Dynamic tool configuration based on use case
4. Call recording with automatic transcription
5. Real-time dashboard for monitoring active calls
6. A/B testing different prompts
7. Call analytics and performance metrics

---

## Contact & Support

For questions or issues:
- Check LiveKit documentation: https://docs.livekit.io
- Review OpenAI Realtime API docs: https://platform.openai.com/docs
- Contact: Nils & Samuel (Finn AI founders)

---

**Last Updated**: October 12, 2025
**Version**: 2.0.0 (Dynamic Language System)
