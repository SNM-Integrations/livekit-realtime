# Setup Documentation

## Current Active Setup

📄 **[WEBSITE_WEBHOOK_SETUP.md](WEBSITE_WEBHOOK_SETUP.md)** - Complete current setup documentation

**Architecture:**
- Hemsida → n8n webhook → Railway server → LiveKit API → Phone call
- **Railway URL:** `https://caring-wonder-production.up.railway.app/trigger-call`
- **n8n Webhook:** `https://n8n-08hy.sliplane.app/webhook/trigger-finn-call`

## Quick Commands

### Deploy Updates
```bash
# Update Railway webhook server
railway up

# Update LiveKit agent
lk agent deploy

# Test Railway webhook
curl -X POST https://caring-wonder-production.up.railway.app/trigger-call \
  -H "Content-Type: application/json" \
  -d '{"name": "Test", "phone": "+46723161614", "country": "SE"}'
```

### Monitoring
```bash
# Railway logs
railway logs --follow

# LiveKit rooms
lk room list

# Kill phantom processes
wmic process where "commandline like '%agent.py%'" delete
```

## Archive

Old deployment attempts (kept for reference):
- `ARCHIVE_VERCEL.md` - Vercel serverless attempt (failed: 250MB limit)
- `ARCHIVE_N8N_JWT.md` - n8n JWT generation attempt (failed: crypto blocked)
- `ARCHIVE_WEBSITE_INTEGRATION.md` - Original Flask server plan

## Other Documentation

- **Prompts:** `/Prompts/` folder
- **Agent Issues:** `/problems/` folder
- **Railway Deploy:** `RAILWAY_DEPLOY.md` (detailed setup guide)
