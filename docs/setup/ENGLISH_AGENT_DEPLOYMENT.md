# English Agent Deployment Guide

## Status
⏸️ **READY TO DEPLOY** - English agent file created at `src/agent_english.py`

## Changes Applied

The English agent (`agent_english.py`) is a copy of the Swedish agent with these strategic changes:

### 1. Voice Configuration
```python
# Line 649
voice="alloy"  # Was: voice="marin"
```
**Reasoning:** `alloy` is professional, clear female voice suitable for UK business calls

### 2. Timezone
```python
# Line 366
current_datetime = datetime.now(ZoneInfo("Europe/London"))  # Was: Europe/Stockholm
```

### 3. Language Code
```python
# Line 660
language="en"  # Was: language="sv"
```

### 4. Day Names (Lines 368-377)
Kept English format (Monday, Tuesday, etc.)

### 5. Month Names (Lines 379-384)
English month names (January, February, etc.)

### 6. Greeting (Line 846)
```python
greeting = f"Hello, my name is Elsa from Finn AI. Am I speaking with {lead_name}?"
# Was: "Hejsan, mitt namn är Elsa från Finn AI, har jag kommit fram till {lead_name}?"
```

### 7. System Prompt (Lines 395-643)
**COMPLETELY TRANSLATED** to UK English:
- Professional British tone
- "Meeting" not "demo"
- Conversational but professional
- Adapted fillers and conversational techniques for English
- All instructions translated
- Calendar messages in English
- Email spelling: "at sign" and "dot" instead of "snabel-a" and "punkt"

### 8. Transcription Prompt (Lines 661-669)
```python
prompt="""English UK business call transcription. Context:
- Meeting booking for AI voice assistant demo
- Email addresses with British names (common: Smith, Jones, Williams, Brown, Taylor)
- Times in 24-hour format (14:00, 10:30)
- Days: Monday, Tuesday, Wednesday, Thursday, Friday
- Business terminology: meeting, demo, AI voice, leads, customers
Transcribe with high accuracy, interpret phonetic spelling contextually."""
```

### 9. Logger Name
```python
logger = logging.getLogger("finn-ai-english")  # Was: "finn-ai"
```

### 10. All User-Facing Messages
All Swedish messages translated:
- Calendar status updates
- Error messages
- Confirmations

## Deployment Steps

### Step 1: Verify File Created
```bash
ls src/agent_english.py
```

### Step 2: Deploy to LiveKit Cloud
```bash
lk agent deploy --agent-name elsa-english
```

**Important:** Use `--agent-name elsa-english` (NOT default name)

### Step 3: Verify Deployment
```bash
lk agent list
```

You should see:
- `elsa-swedish` (existing)
- `elsa-english` (new)

### Step 4: Test with UK Number
You'll need a UK phone number (+44...) to test properly.

**Test command (when you have UK number):**
```python
# In make_test_call.py, change:
phone_number = "+447XXXXXXXXX"  # UK number
agent_name = "elsa-english"
```

Then run:
```bash
python make_test_call.py
```

## Routing Logic

Railway webhook (`trigger_call_simple.py`) already has routing logic:

```python
# Line ~95
agent_name = "elsa-swedish" if country == "SE" else "elsa-english"
```

**Website form must send:**
```json
{
  "name": "John Smith",
  "phone": "+447123456789",
  "country": "EN"  // This triggers English agent
}
```

## Testing Checklist

After deployment, verify:

- [ ] Agent appears in `lk agent list` as `elsa-english`
- [ ] Test call connects
- [ ] Voice is `alloy` (female, professional)
- [ ] Greeting is in English
- [ ] Agent speaks British English throughout
- [ ] Calendar checking works
- [ ] Email spelling uses "at" and "dot"
- [ ] end_call function works
- [ ] Room deletion on hangup works

## Monitoring

### Check Logs
```bash
lk agent logs --agent-name elsa-english --follow
```

### Check Active Rooms
```bash
lk room list
```

### Check Session Minutes
Visit LiveKit Cloud dashboard to monitor usage.

## Rollback

If English agent has issues:

```bash
# Delete English agent
lk agent delete elsa-english

# Swedish agent continues working unaffected
```

## Future: Additional Languages

To add more languages (e.g., German, French):

1. Copy `agent_english.py` → `agent_german.py`
2. Change voice (research best German voice)
3. Translate prompt to German
4. Change timezone to appropriate (Europe/Berlin)
5. Deploy: `lk agent deploy --agent-name elsa-german`
6. Update Railway routing logic

## Cost Impact

**Current:** 1 agent (Swedish)
**After:** 2 agents (Swedish + English)

**Billing:**
- Agents deployed: No additional cost (both on same plan)
- Session minutes: Only charged when actively on calls
- No idle cost for deployed agents

**Expected:** Minimal increase unless UK call volume is high.

## Documentation Updated

- [WEBSITE_WEBHOOK_SETUP.md](WEBSITE_WEBHOOK_SETUP.md) - Already includes multi-language routing
- Railway `trigger_call_simple.py` - Already has routing logic
- n8n workflow - No changes needed (passes through `country` field)

## Ready to Deploy?

When you're ready:
```bash
cd c:\Users\Admin\gpt-realtime.new
lk agent deploy --agent-name elsa-english
```

Then test with a UK phone number!
