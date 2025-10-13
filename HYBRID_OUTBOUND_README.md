# Finn AI - Hybrid Outbound Agent

## Overview

This is a **self-demoing AI sales agent** that calls prospects (both cold and warm leads), learns about their business, demonstrates itself by simulating being their AI receptionist, and books meetings with the sales team.

## Key Features

### 🎯 Dual Mode Support
- **Cold Calls**: Introduces itself with referrer mention, explains value proposition
- **Warm Calls**: Acknowledges form submission, builds on existing interest

### 🎭 Live Demo Simulation
- Agent switches persona mid-call to **Elsa** (customer's AI receptionist)
- Demonstrates realistic call handling based on prospect's industry
- Returns to **Finn** persona for feedback and booking

### 📅 Calendar Integration
- Checks availability via N8N webhook
- Caches results to avoid repeated API calls
- Books meetings directly into calendar system

### 🔒 Safety Features
- 10-minute maximum call duration
- 45-second inactivity timeout
- Proper SIP call termination
- Phase-specific timeouts (discovery, simulation, booking)

---

## Architecture

### Call Flow

```
Webhook Trigger → Lead Detection → Opening (Cold/Warm)
    ↓
Discovery Phase (2-3 questions)
    ↓
Simulation Offer → Get Permission
    ↓
Simulation Mode (Finn → Elsa) [60-90 sec demo]
    ↓
End Simulation (Elsa → Finn)
    ↓
Feedback & Booking → Calendar Check → Confirm Meeting
    ↓
Closing & Goodbye
```

### Components

1. **LeadContext** - Parses webhook metadata
2. **CallPhase** - Tracks conversation stage
3. **Function Tools**:
   - `start_simulation()` - Switch to Elsa persona
   - `end_simulation()` - Return to Finn persona
   - `check_availability()` - Query calendar
   - `book_meeting()` - Create calendar event
   - `end_call()` - Terminate call gracefully

---

## Configuration

### Webhook Metadata Format

When triggering a call, send metadata in JSON format:

```json
{
  "lead_source": "form",                    // "form", "cold", or "referral"
  "lead_name": "Anna Andersson",
  "company_name": "Acme AB",
  "phone_number": "+46701234567",
  "industry": "SaaS",                       // Optional
  "referrer_name": "Nils",                  // For cold calls
  "form_timestamp": "2025-10-13T14:30:00Z", // For warm leads
  "notes": "Interested in inbound handling" // Optional
}
```

### Config File: `config/agent.creation.md`

Key settings:

```yaml
language: "Svenska"
voice: "shimmer"
workflow_type: "hybrid_outbound"

outbound_config:
  demo_enabled: true
  simulation_max_duration: 90
  discovery_max_questions: 3
  pushy_level: 5                    # 1-10 scale
  default_referrer: "Nils"

integrations:
  calendar:
    enabled: true
    webhook_url: "https://snmnils.app.n8n.cloud/webhook/..."

  booking:
    enabled: true

advanced:
  model_overrides:
    temperature: 0.85               # Slightly lower for controlled demo

  phase_timeouts:
    discovery: 120                  # 2 minutes
    simulation: 90                  # 1.5 minutes
    post_demo: 180                  # 3 minutes
    total_call: 600                 # 10 minutes max
```

---

## Deployment

### 1. Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Edit .env with:
# - OPENAI_API_KEY
# - BOOKING_WEBHOOK_URL (optional, defaults to calendar webhook)
# - WEBHOOK_URL (for conversation logging)
```

### 2. Deploy to LiveKit Cloud

```bash
lk agent deploy
```

### 3. Test the Agent

**Cold Call Test Metadata:**
```json
{
  "lead_source": "cold",
  "lead_name": "Test Prospect",
  "company_name": "Test Company AB",
  "referrer_name": "Nils"
}
```

**Warm Lead Test Metadata:**
```json
{
  "lead_source": "form",
  "lead_name": "Test Prospect",
  "company_name": "Test Company AB",
  "form_timestamp": "2025-10-13T14:30:00Z"
}
```

---

## Usage Examples

### Example 1: Cold Call to VVS Company

**Metadata:**
```json
{
  "lead_source": "cold",
  "lead_name": "Erik Svensson",
  "company_name": "Svensson VVS AB",
  "industry": "Plumbing",
  "referrer_name": "Samuel"
}
```

**Expected Flow:**
1. **Opening**: "Hej! Det här är Finn från Finn AI. Hur mår du?"
2. **Context**: "Min kollega Samuel sa att jag skulle ringa er..."
3. **Discovery**: "Vad gör ni på Svensson VVS?" → "Vilka samtal får ni mest?"
4. **Simulation Offer**: "Vill du att jag kör en snabb demo?"
5. **Demo**: [Switches to Elsa] "Hej, det här är Elsa från Svensson VVS AB..."
6. **Feedback**: "Vad tyckte du?"
7. **Booking**: "Ska vi boka ett möte?" → Calendar check → Confirm

### Example 2: Warm Lead from Form

**Metadata:**
```json
{
  "lead_source": "form",
  "lead_name": "Lisa Nordström",
  "company_name": "Nordström Consulting",
  "industry": "Consulting",
  "form_timestamp": "2025-10-13T14:28:00Z"
}
```

**Expected Flow:**
1. **Opening**: "Hej! Det här är Finn från Finn AI. Hur mår du?"
2. **Context**: "Du fyllde precis i vårt formulär för 2 minuter sedan!"
3. **Discovery**: (Shorter, since they already expressed interest)
4. **Simulation**: Quick demo
5. **Booking**: Higher conversion likelihood

---

## Customization

### Adjusting Pushy Level

Edit `config/agent.creation.md`:

```yaml
outbound_config:
  pushy_level: 3  # Less aggressive (1-3)
  pushy_level: 5  # Balanced (4-6) [RECOMMENDED]
  pushy_level: 8  # More aggressive (7-10)
```

This affects:
- How quickly agent moves to booking
- Number of follow-up questions after "no"
- Persistence in handling objections

### Changing Default Referrer

```yaml
outbound_config:
  default_referrer: "Your Name"
```

Or pass dynamically via metadata:
```json
{
  "referrer_name": "Custom Name"
}
```

### Modifying Simulation Duration

```yaml
outbound_config:
  simulation_max_duration: 60  # Shorter demo (1 min)
  simulation_max_duration: 120 # Longer demo (2 min)
```

**Note**: Agent is instructed to keep simulations 60-90 seconds, but this setting adds a hard timeout.

---

## Troubleshooting

### Issue: Agent doesn't switch to Elsa during simulation

**Cause**: `start_simulation()` tool not being called

**Solution**: Check that prompt clearly instructs to use the tool. Verify in logs:
```
🎭 Starting simulation mode for: [Company Name]
```

### Issue: Calendar check times out

**Cause**: N8N webhook taking too long (>30s)

**Solution**: Agent sends periodic status updates ("jag kollar fortfarande..."). Check webhook performance and consider optimizing the N8N workflow.

### Issue: Agent too pushy or not pushy enough

**Cause**: `pushy_level` misconfigured

**Solution**: Adjust in `config/agent.creation.md` (scale 1-10, recommend 5 for balanced approach)

### Issue: Cold calls sound too scripted

**Cause**: Agent following rigid script instead of adapting

**Solution**: Prompt emphasizes "LYSSNA" and "adapt". May need to:
- Lower temperature (more creative): `temperature: 0.90`
- Simplify prompt (less prescriptive)
- Add more conversational examples

---

## Function Tools Reference

### `start_simulation(customer_company: str)`

Switches agent from **Finn** to **Elsa** persona.

```python
# Agent automatically calls this when prospect agrees to demo
start_simulation(customer_company="Acme AB")
```

**Effect**: Agent now acts as AI receptionist for customer's company

### `end_simulation()`

Returns agent from **Elsa** back to **Finn**.

```python
# Agent calls this after 60-90 seconds of demo
end_simulation()
```

**Effect**: Agent resumes sales conversation

### `check_availability(start_datetime, end_datetime)`

Queries calendar for available meeting slots.

```python
# Check availability for next week
check_availability(
    start_datetime="2025-10-20T09:00:00+02:00",
    end_datetime="2025-10-20T17:00:00+02:00"
)
```

**Returns**: List of available time slots

### `book_meeting(...)`

Books a confirmed meeting.

```python
book_meeting(
    contact_name="Anna Andersson",
    company="Acme AB",
    phone="+46701234567",
    email="anna@acme.se",
    meeting_datetime="2025-10-20T14:00:00+02:00",
    notes="Interested in inbound handling"
)
```

**Returns**: Booking confirmation or error

### `end_call()`

Gracefully terminates the call after goodbye.

```python
# Agent calls this after saying farewell
end_call()
```

**Effect**: SIP call properly closed, room deleted

---

## Monitoring

### Logs

Key log markers to watch:

- `📝 Parsed metadata:` - Lead context loaded
- `🎭 Starting simulation mode` - Demo started
- `🎭 Ending simulation mode` - Demo ended
- `📅 Checking availability` - Calendar query
- `✅ Meeting booked successfully` - Booking confirmed
- `⚠️ Could not parse metadata` - Metadata issue

### Success Metrics

Track in your analytics:

1. **Conversation Duration**: Average ~4-6 minutes for cold, ~3-4 for warm
2. **Simulation Rate**: % of calls that reach simulation phase
3. **Booking Rate**: % of calls that result in booked meeting
4. **Feedback Sentiment**: Positive/negative after demo
5. **Drop-off Points**: Where prospects hang up most

---

## Advanced Features

### Dynamic Prompt Injection

Lead context is automatically injected:

- `{{lead_name}}` → Actual name
- `{{company_name}}` → Company name
- `{{referrer_name}}` → Who referred them
- `{{lead_source}}` → "cold" or "form"

Plus dynamic date/time in Swedish format.

### Phase Tracking

Agent internally tracks phase progression:

```python
class CallPhase(Enum):
    OPENING = "opening"
    DISCOVERY = "discovery"
    SIMULATION_OFFER = "simulation_offer"
    SIMULATION = "simulation"
    POST_DEMO = "post_demo"
    CLOSING = "closing"
```

Can be used for analytics or timeout enforcement.

### Caching

Calendar queries are cached per date range to avoid repeated API calls during same conversation.

---

## Best Practices

### 1. Metadata Quality

**Good:**
```json
{
  "lead_source": "form",
  "lead_name": "Anna Andersson",
  "company_name": "Acme AB",
  "industry": "SaaS",
  "form_timestamp": "2025-10-13T14:30:00Z"
}
```

**Bad:**
```json
{
  "lead_source": "form",
  "lead_name": "User123"  // Not a real name
}
```

### 2. Timing Cold Calls

- Call within business hours (09:00-17:00 Swedish time)
- Avoid Mondays before 10:00 (people are busy)
- Best times: Tuesday-Thursday 10:00-16:00

### 3. Prompt Tuning

Start with provided prompt, then iterate based on:
- Actual call recordings
- Conversion rates
- Feedback from prospects

### 4. Testing

Always test with:
- **Cold lead** scenario (unknown company)
- **Warm lead** scenario (recent form submission)
- **Calendar edge cases** (no availability, errors)
- **Objection handling** (not interested, too busy)

---

## Roadmap

Potential enhancements:

- [ ] Multi-language support (English, Norwegian, Danish)
- [ ] Industry-specific simulation scripts
- [ ] A/B testing different opening strategies
- [ ] Sentiment analysis during call
- [ ] Automatic follow-up email if no meeting booked
- [ ] Integration with CRM (HubSpot, Salesforce)
- [ ] Voice cloning for custom brand voice

---

## Support

For issues or questions:

1. Check logs: `lk agent logs --agent-id [YOUR_AGENT_ID] --follow`
2. Review this README troubleshooting section
3. Test with sample metadata in LiveKit Playground
4. Contact team if calendar/booking webhooks fail

---

## License

Internal use only - Finn AI / SNM Integrations
