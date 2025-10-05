# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠️ CRITICAL: Cloud-Only Deployment

**NEVER run local development agents. This causes phantom billing issues.**

### ❌ FORBIDDEN Commands:
```bash
# NEVER DO THIS:
python src/agent.py dev
python src/agent.py start
python src/agent.py connect
lk agent dev
```

**Why:** Running agents locally creates background processes that:
- Register as workers on LiveKit Cloud
- Maintain persistent OpenAI connections (reconnecting every 20 minutes)
- Accept job requests = billable agent session minutes
- Can run for days in background shells, causing massive billing waste
- Example: 3-day background process = 4320 wasted session minutes ($43+)

### ✅ CORRECT Deployment Workflow:
```bash
# 1. Edit code locally
vim src/agent.py
vim config/agent.creation.md

# 2. Deploy to LiveKit Cloud
lk agent deploy

# 3. Test the deployed agent
# Use LiveKit Playground or make test calls to the deployed agent

# 4. Check logs
lk agent logs <AGENT_ID>

# 5. Check for active rooms (should be empty when not in calls)
lk room list
```

## Project Structure

This is a LiveKit Voice Agent Template for building simple, reliable AI voice agents for phone calls.

### Core Files

**Agent Code:**
- `src/agent.py` - Main agent with CallMemory, safety timeouts, proper SIP termination
- `config/agent.creation.md` - Agent configuration (YAML format)
- `livekit.toml` - Deployment configuration

**Documentation:**
- `README.md` - Overview and quick start
- `AI_AGENT_CREATOR_GUIDE.md` - Guide for AI assistants to auto-generate agents
- `docs/SIP_INTEGRATION_BEST_PRACTICES.md` - **CRITICAL:** Prevent phantom SIP billing

**Configuration:**
- `.env` - API keys (create from .env.example)
- `.env.example` - Template for environment variables

## Agent Configuration

Agent behavior is controlled via `config/agent.creation.md` (YAML format):

```yaml
language: "Svenska"              # Svenska, English, Español, Français, Deutsch
voice: "marin"                    # marin, cedar, shimmer, nova, alloy
personality_traits: "calm, friendly, conversational"
temperature: 0.9                  # 0.7-1.0, higher = more natural

first_message: >
  Hej, tack för att du ringde. Jag är [Name]'s assistent.
  Hur kan jag hjälpa dig idag?

prompt: |
  [Your detailed system prompt here]
```

**Important:** Agent uses this config file, NOT environment variables for behavior.

## Key Features

### 1. Proper SIP Termination (CRITICAL)
The agent uses `ctx.shutdown()` instead of `delete_room()` to properly close SIP connections:

```python
# ✅ CORRECT - Sends SIP BYE signal
await ctx.shutdown(reason="Call completed")

# ❌ WRONG - Leaves SIP trunk open (phantom billing)
await ctx.api.room.delete_room(...)
```

See `docs/SIP_INTEGRATION_BEST_PRACTICES.md` for full details.

### 2. Triple-Layer Timeout Protection
- **45-second silence timeout** - Ends call if no activity
- **10-minute max duration** - Hard cutoff to prevent runaway calls
- **Proper shutdown on all paths** - Error handling, timeouts, manual end

### 3. CallMemory System
Tracks information during calls to prevent re-asking:
- `save_caller_info()` - Store name, phone, email, purpose
- `check_caller_memory()` - Retrieve collected info
- `save_call_details()` - Add additional notes

### 4. Multi-Language Support
- Uses `LANGUAGE_CODES` mapping for proper Whisper transcription
- Supports: Swedish, English, Spanish, French, German
- Auto-configures based on `language` setting in config

### 5. OpenAI Realtime API Integration
- Uses `InputAudioTranscription` for Whisper transcription of user speech
- Proper temperature settings for natural conversation
- Language-specific transcription prompts

## Important Development Rules

1. **Cloud-Only Deployment** - NEVER run agents locally (see warning above)
2. **Config-Driven Behavior** - Change agent via config files, not code edits
3. **Always Use ctx.shutdown()** - Never use delete_room() for SIP calls
4. **Test Timeouts** - Verify calls end after 45s silence or 10min max
5. **Check for Stuck Calls** - Use `lk room list` to verify no lingering rooms
6. **Monitor Billing** - Check LiveKit dashboard for agent session minutes

## Emergency: Stuck Calls

If you discover calls lasting hours or excessive billing:

```bash
# 1. Check for stuck rooms
lk room list

# 2. If rooms exist when no calls active, something is wrong
# Contact LiveKit support or check SIP integration

# 3. Check for background processes (Windows)
wmic process where "commandline like '%agent.py%'" list brief

# 4. Kill any background agent processes
wmic process where "commandline like '%agent.py%'" delete
```

## Testing Checklist

After deploying an agent:

- [ ] Make a test call
- [ ] Verify greeting plays completely (not cut off)
- [ ] Have a normal conversation
- [ ] Let call end naturally
- [ ] Run `lk room list` - should be empty
- [ ] Check Telnyx dashboard - duration should match actual call time
- [ ] Test 45s silence timeout - call should auto-end
- [ ] Test 10min max - call should force-end at exactly 10 minutes

## Common Issues

### Issue: "project does not match agent subdomain [{{YOUR_SUBDOMAIN}}]"
**Cause:** The `livekit.toml` file still has the template placeholder `{{YOUR_SUBDOMAIN}}` instead of your actual project subdomain.

**Solution:**
```bash
# 1. Get your project subdomain
lk project list

# 2. Copy the subdomain from the URL (e.g., "my-project-abc123")
# 3. Edit livekit.toml and replace {{YOUR_SUBDOMAIN}} with your subdomain
subdomain = "my-project-abc123"
```

This is a **very common mistake** when creating new agents from the template. The livekit.toml file has detailed instructions - make sure to read them!

### Issue: First few seconds of greeting cut off
**Solution:** Agent waits for SIP participant to connect before greeting. If still happening, check logs for timing.

### Issue: Calls lasting hours in billing
**Solution:** Agent not using `ctx.shutdown()`. Verify code has the SIP termination fix.

### Issue: Agent asks for information already provided
**Solution:** CallMemory system should prevent this. Check `save_caller_info()` is being called.

### Issue: Agent sounds robotic
**Solution:** Increase `temperature` to 0.9 in `config/agent.creation.md`

## Dependencies

Core requirements (see `requirements.txt`):
- `livekit-agents[openai]` - LiveKit framework with OpenAI plugin
- `python-dotenv` - Environment variable management
- `pyyaml` - YAML configuration parsing
- `aiohttp` - HTTP client for webhooks

## Resources

- [LiveKit Agents Documentation](https://docs.livekit.io/agents/)
- [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime)
- [LiveKit Cloud Dashboard](https://cloud.livekit.io)
- [SIP Integration Best Practices](docs/SIP_INTEGRATION_BEST_PRACTICES.md)

---

**Remember:** Always deploy to cloud, never run locally. Phantom billing is expensive!
