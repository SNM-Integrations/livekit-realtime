# Claude Code Instructions for LiveKit Single-Agent Outbound Demo

## Single-Agent Framework

This is a **single-agent outbound calling framework** designed for easy customization and deployment.

⚠️ **NEVER RUN LOCAL AND CLOUD AGENTS SIMULTANEOUSLY**

This project uses **CLOUD-ONLY deployment** to prevent conflicts, caching issues, and performance problems.

## Quick Start

### Making Changes
1. Edit code in `src/agent.py`
2. Deploy to cloud: `lk agent deploy`
3. Test: `python make_test_call.py`

### Before Any Deployment
**ALWAYS kill local processes first:**
```bash
wmic process where "commandline like '%agent.py%'" delete
```

### Key Commands
- **Deploy updates**: `lk agent deploy` (NOT `lk agent create`)
- **Test agent**: `python make_test_call.py`
- **List agents**: `lk agent list`
- **Kill locals**: `wmic process where "commandline like '%agent.py%'" delete`

## Current Setup
- **Agent ID**: CA_uG7inqdgtPgQ (Single agent - Swedish base)
- **Agent Name**: elsa-swedish
- **Project**: finn-outbound-ebnrjj6k.livekit.cloud
- **Test number**: +46723161614 (Nils)

## What NOT to Do
❌ **NEVER run**: `python src/agent.py dev` (causes phantom session minutes!)
❌ Don't run: `python src/agent.py start`
❌ Don't use: `lk agent create` for updates
❌ Don't ignore local processes running in background

## ⚠️ BILLING WARNING: Phantom Session Minutes
**Background dev processes cause massive billing waste:**
- Local `python src/agent.py dev` maintains persistent OpenAI connection
- Reconnects every 20 minutes even when idle
- **Example**: 3-day background process = 4320 wasted session minutes ($$$)

**ALWAYS check and kill before starting work:**
```bash
wmic process where "commandline like '%agent.py%'" delete
```

## If Things Break
1. Kill all processes: `wmic process where "commandline like '%agent.py%'" delete`
2. Redeploy: `lk agent deploy`
3. Test: `python make_test_call.py`

## Agent Configuration
- **Single agent setup** (no language switching)
- Swedish conversational AI base (easily customizable)
- Natural dialogue (no rigid scripts)
- 400-600ms response time
- 0.5s delay before greeting for SIP readiness
- Uses working LiveKit 2025 pattern (LLM in AgentSession)

## Customization
To customize for a new client:
1. Edit `src/agent.py` - change prompt, voice, behavior
2. Edit `Prompts/swedish_agent_prompt.md` - update instructions
3. Deploy: `lk agent deploy`

## Railway Webhook
- Endpoint: `https://caring-wonder-production.up.railway.app/trigger-call`
- Always uses `elsa-swedish` agent (country parameter ignored)
- Single-agent architecture for simplicity

## Critical Fix Applied
✅ Agent now speaks on outbound calls
✅ Uses agent NAME in dispatch (not agent ID)
✅ LLM in AgentSession (correct LiveKit 2025 pattern)
✅ 0.5s greeting delay for audio readiness
