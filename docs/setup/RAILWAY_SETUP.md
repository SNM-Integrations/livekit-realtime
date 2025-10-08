# Railway Deployment Setup & Architecture

## Overview

Railway hosts the **HTTP trigger service** that receives webhook calls from n8n/website and initiates LiveKit outbound phone calls. The actual AI agents run on LiveKit Cloud, NOT on Railway.

**Railway URL:** `https://caring-wonder-production.up.railway.app`

---

## Architecture Flow

```
Website Form
    ↓ (webhook)
n8n Workflow
    ↓ (HTTP POST)
Railway Server (trigger_call_simple.py)
    ↓ (creates dispatch + SIP call)
LiveKit Cloud
    ↓ (AI agent connects)
Phone Call to User
```

**Key Point:** Railway ONLY triggers calls. The AI agent code (agent.py, agent_english.py) is deployed to LiveKit Cloud separately.

---

## Railway Configuration

### Files Used by Railway

- **`trigger_call_simple.py`** - Main HTTP server that receives webhooks and triggers calls
- **`railway.json`** - Railway deployment configuration
- **`.env.local`** - Environment variables (NOT committed to git)
- **`.railwayignore`** - Files to exclude from Railway deployment

### railway.json

```json
{
  "build": {
    "builder": "NIXPACKS"  // Railway auto-detects Python
  },
  "deploy": {
    "startCommand": "python trigger_call_simple.py",  // Runs on port 8080
    "numReplicas": 1,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

### Environment Variables in Railway

Railway needs these environment variables (set in Railway dashboard):

- `LIVEKIT_URL` - LiveKit project URL (e.g., wss://finn-outbound-xxxxx.livekit.cloud)
- `LIVEKIT_API_KEY` - LiveKit API key
- `LIVEKIT_API_SECRET` - LiveKit API secret
- `OUTBOUND_SIP_TRUNK_ID` - SIP trunk ID for outbound calls
- `PORT` - Auto-set by Railway (usually 8080)

**Important:** Railway uses its own environment variables, NOT `.env.local` from the project folder.

---

## How trigger_call_simple.py Works

### 1. HTTP Server Setup

```python
PORT = int(os.environ.get('PORT', 8000))
server = HTTPServer(('0.0.0.0', PORT), RequestHandler)
```

- Listens on Railway's assigned port (default 8080)
- Endpoint: `/trigger-call` (POST requests only)

### 2. Request Handling

**Expected webhook payload from n8n:**
```json
{
  "name": "Samuel",
  "phone": "+460723161614",
  "country": "SE"  // or "GB"
}
```

**Agent selection logic:**
```python
agent_name = "elsa-swedish" if country == "SE" else "elsa-english"
```

- `country: "SE"` → Swedish agent (`CA_uG7inqdgtPgQ`)
- `country: "GB"` → English agent (`CA_WjF7u2U7wd7A`)

### 3. LiveKit Call Initiation

**Two-step process:**

**Step 1: Create Agent Dispatch**
```python
await lkapi.agent_dispatch.create_dispatch(
    api.CreateAgentDispatchRequest(
        agent_name=agent_name,      // "elsa-swedish" or "elsa-english"
        room=room_name,             // "call_samuel_1759855233"
        metadata=json.dumps({...})
    )
)
```

This tells LiveKit Cloud: "When someone joins this room, start this agent."

**Step 2: Create SIP Participant (Dial Phone)**
```python
await lkapi.sip.create_sip_participant(
    api.CreateSIPParticipantRequest(
        sip_trunk_id=SIP_TRUNK_ID,
        sip_call_to=phone_number,
        room_name=room_name,
        participant_identity=f"sip_{lead_name}",
        participant_name=lead_name,
        play_ringtone=True
    )
)
```

This dials the phone number and connects them to the LiveKit room where the agent is waiting.

### 4. Room Naming Convention

**Critical:** Room names must use **underscores** not hyphens:

```python
room_name = f"call_{lead_name.lower().replace(' ', '_')}_{int(__import__('time').time())}"
```

**Example:** `call_samuel_1759855233`

The agent code expects this format to extract the lead name:
```python
if "_" in ctx.room.name:
    room_parts = ctx.room.name.split("_")
    if len(room_parts) >= 3:
        lead_name = room_parts[-1]  // Gets "samuel"
```

If you use hyphens (`call-samuel-123`), the agent can't extract the name and defaults to "där" (Swedish) or "there" (English).

---

## Deployment Process

### Method 1: Railway CLI (Recommended)

**Install Railway CLI:**
```bash
npm i -g @railway/cli
```

**Deploy from project root:**
```bash
railway up
```

This uploads all files (except those in `.railwayignore`) and triggers a new deployment.

**Monitor deployment:**
- Railway provides a build logs URL after running `railway up`
- Or check Railway dashboard: https://railway.com/project/[your-project-id]

### Method 2: Railway Dashboard

1. Go to Railway dashboard
2. Select your project/service
3. Go to "Deployments" tab
4. Click "Deploy" → "Redeploy" or connect to GitHub for auto-deploys

### Method 3: Git Push (If GitHub Connected)

If Railway is connected to your GitHub repo:
```bash
git add .
git commit -m "Update trigger code"
git push
```

Railway auto-deploys on push to the connected branch.

---

## Deployment Checklist

**Before deploying to Railway:**

1. ✅ Test locally: `python trigger_call_simple.py` (runs on port 8000)
2. ✅ Verify environment variables are set in Railway dashboard
3. ✅ Check `.railwayignore` excludes unnecessary files
4. ✅ Ensure `railway.json` has correct start command
5. ✅ Deploy: `railway up`
6. ✅ Wait ~1-2 minutes for deployment to complete
7. ✅ Test webhook endpoint: `POST https://caring-wonder-production.up.railway.app/trigger-call`

**After deploying to Railway:**

- Check Railway logs for errors
- Verify HTTP server started: "🚀 Server running on http://0.0.0.0:8080/trigger-call"
- Test with actual form submission from website

---

## Debugging Railway Deployments

### View Live Logs

**Via CLI:**
```bash
railway logs
```

**Via Dashboard:**
- Go to your Railway project
- Click on the service
- "Deployments" tab → Select active deployment → View logs

### Common Issues

**1. "room_name is not defined" error**
- **Cause:** Variable scope issue in older versions
- **Fix:** Ensure using `result.get('room_name', 'unknown')` in print statement (line 119)

**2. Agent says "där" instead of actual name**
- **Cause:** Room name format mismatch (hyphens vs underscores)
- **Fix:** Ensure room_name uses underscores: `call_{name}_{timestamp}`

**3. Port binding error**
- **Cause:** Railway assigns dynamic PORT variable
- **Fix:** Always use `PORT = int(os.environ.get('PORT', 8000))`

**4. Environment variables not found**
- **Cause:** `.env.local` is NOT used by Railway
- **Fix:** Set all variables in Railway dashboard under "Variables" tab

### Health Check

Test if Railway server is running:
```bash
curl -X POST https://caring-wonder-production.up.railway.app/trigger-call \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","phone":"+460723161614","country":"SE"}'
```

Expected response:
```json
{
  "success": true,
  "room_name": "call_test_1759855233",
  "message": "Call initiated to +460723161614"
}
```

---

## Relationship to LiveKit Agent Deployment

**Important:** Railway and LiveKit are SEPARATE deployments.

### Railway Deploys:
- `trigger_call_simple.py` (HTTP trigger server)
- Runs continuously waiting for webhooks
- Creates LiveKit dispatches and SIP calls

### LiveKit Deploys:
- `agent.py` (Swedish agent)
- `agents/english/agent.py` (English agent)
- Deployed via: `lk agent deploy`
- Run on-demand when calls are initiated

**They communicate via:**
1. Railway creates agent dispatch → LiveKit receives
2. LiveKit starts agent in room → Agent handles conversation
3. Agent ends → Railway already finished (just triggered the call)

---

## File Structure for Railway

```
project-root/
├── trigger_call_simple.py    ← Railway runs this
├── railway.json               ← Railway config
├── .railwayignore            ← Files Railway ignores
├── requirements.txt          ← Python dependencies (if needed)
├── .env.local                ← NOT used by Railway (local only)
└── src/
    ├── agent.py              ← NOT deployed to Railway
    └── agent_english.py      ← NOT deployed to Railway
```

---

## Quick Reference Commands

```bash
# Deploy to Railway
railway up

# View Railway logs
railway logs

# Check Railway status
railway status

# Link to Railway project (first time setup)
railway link

# Set environment variable
railway variables set LIVEKIT_URL="wss://..."
```

---

## Emergency Rollback

If deployment breaks:

1. **Via Dashboard:** Go to Deployments → Select previous working deployment → "Redeploy"
2. **Via CLI:** Not directly supported - redeploy previous git commit instead

**Best Practice:** Always test locally before deploying to Railway.

---

## Contact & Support

- **Railway Docs:** https://docs.railway.com
- **Railway Status:** https://status.railway.com
- **Railway Discord:** Community support for Railway-specific issues
- **LiveKit Docs:** https://docs.livekit.io (for agent deployment issues)
