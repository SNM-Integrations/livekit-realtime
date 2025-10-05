# Quick Start: Create Your First Agent in 2 Minutes

## For AI Assistants (Claude Code, ChatGPT, etc.)

### Simple Prompt Format

```
Using the LiveKit voice agent template, create an agent for:

OWNER: [Name]
BUSINESS: [What they do]
LANGUAGE: [Svenska/English/Spanish/etc.]
PURPOSE: [What the agent should do - be specific]
STYLE: [How it should sound]
```

### Example 1: Insurance Broker

```
Create an agent for:
OWNER: Robert
BUSINESS: Insurance broker
LANGUAGE: Svenska
PURPOSE: Handle missed calls. Understand why they're calling, get their name and phone number, say Robert will call back. Don't try to help with insurance questions.
STYLE: Professional but warm, conversational, not pushy
```

### Example 2: Restaurant

```
Create an agent for:
OWNER: Maria
BUSINESS: Italian restaurant "Bella Vista"
LANGUAGE: English
PURPOSE: Take reservation requests - collect name, phone, date, time, party size. Confirm details clearly.
STYLE: Friendly, efficient, hospitality-focused
```

### Example 3: Dental Office

```
Create an agent for:
OWNER: Dr. Chen
BUSINESS: Dental practice
LANGUAGE: English
PURPOSE: Screen calls - understand if it's emergency/urgent/routine, collect patient info, promise callback
STYLE: Calm, professional, empathetic
```

---

## What the AI Should Do

1. **Read `AI_AGENT_CREATOR_GUIDE.md`** - Contains all templates and instructions

2. **Choose the right template:**
   - Template A: Missed call handler (just collect info)
   - Template B: Customer service (try to help if possible)
   - Template C: Appointments/reservations

3. **Fill in `config/agent.creation.md`:**
   - Replace all `{{VARIABLES}}`
   - Use appropriate template from guide
   - Translate if needed

4. **Update `livekit.toml`:**
   - Set `{{AGENT_NAME}}` to something like "Robert's Assistant"
   - Leave `{{YOUR_SUBDOMAIN}}` - user will fill this

5. **Tell the user:**
   ```
   ✅ Agent configured!

   Next steps:
   1. Copy .env.example to .env
   2. Fill in your API keys in .env
   3. Run: lk project list
   4. Copy your subdomain to livekit.toml
   5. Deploy: lk agent create
   ```

---

## Manual Quick Start (Without AI)

### Option 1: Copy an Example

1. Look at `config/agent.creation.md`
2. Find the example closest to what you need (bottom of file)
3. Copy that example and customize it
4. Uncomment the example section and fill in your details

### Option 2: Fill in the Template

1. Open `config/agent.creation.md`
2. Find each `{{VARIABLE}}` and replace it:

**Basic replacements:**
```yaml
language: "English"  # Your language
voice: "cedar"       # Your preferred voice
personality_traits: "professional, helpful, friendly"
temperature: 0.9

first_message: >
  Thank you for calling! How can I help you today?
```

**The prompt** - Use templates from `AI_AGENT_CREATOR_GUIDE.md`:
```yaml
prompt: |
  [Copy appropriate template here and customize]
```

3. Save and deploy!

---

## Testing Your Agent

After deployment:

1. **Go to LiveKit Playground**: https://cloud.livekit.io/playground
2. **Select your agent**
3. **Click to call**
4. **Test these scenarios:**
   - ✅ Vague request ("I need help with something")
   - ✅ Clear request ("I want to make an appointment")
   - ✅ Multiple topics in one call
   - ✅ Agent doesn't ask too many questions
   - ✅ Agent ends call appropriately
   - ✅ Language is correct
   - ✅ Voice sounds natural

## Common Issues & Fixes

| Problem | Solution |
|---------|----------|
| Sounds robotic | Increase `temperature` to 0.9 |
| Wrong language | Check `language` setting |
| Too many questions | Add "max 1-2 questions" to prompt |
| Hangs up too early | Improve end_call criteria in prompt |
| Wrong accent | Make sure voice matches language |
| **⚠️ Excessive billing / Stuck calls** | **See [docs/SIP_INTEGRATION_BEST_PRACTICES.md](docs/SIP_INTEGRATION_BEST_PRACTICES.md)** |

---

## Real Examples

### Swedish Insurance Agent (Robert)
```yaml
language: "Svenska"
voice: "marin"
first_message: >
  Hej, tack för att du ringde. Jag är Roberts assistent. Hur kan jag hjälpa dig idag?

prompt: |
  Du är Roberts personliga assistent som svarar på HANS MISSADE SAMTAL.
  Robert är försäkringsmäklare och kan inte svara just nu.

  DITT HUVUDMÅL:
  - Förstå vad personen ringer angående
  - Få namn och telefonnummer
  - Säg att Robert ringer tillbaka
  - FÖRSÖK INTE hjälpa med försäkringar själv

  [... see AI_AGENT_CREATOR_GUIDE.md for full template]
```

### English Restaurant (Maria)
```yaml
language: "English"
voice: "shimmer"
first_message: >
  Thank you for calling Bella Vista! How can I help you today?

prompt: |
  You are the reservation assistant for Bella Vista Italian Restaurant.

  YOUR MAIN GOAL:
  - Take reservation requests
  - Collect: name, phone, date, time, party size
  - Confirm details clearly
  - End with "We look forward to seeing you!"

  [... see AI_AGENT_CREATOR_GUIDE.md for full template]
```

---

## Need Help?

- **Full Templates**: See `AI_AGENT_CREATOR_GUIDE.md`
- **Configuration Reference**: See `config/agent.creation.md`
- **Technical Setup**: See `README.md`
- **Issues**: https://github.com/SNM-Integrations/livekit-realtime/issues
