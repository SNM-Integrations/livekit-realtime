# Website → n8n → Railway → LiveKit Setup

## Översikt

**Flow:**
```
Hemsida formulär → n8n webhook → Railway Python server → LiveKit API → Samtal
```

## Komponenter

### 1. Railway Webhook Server
- **URL:** `https://caring-wonder-production.up.railway.app/trigger-call`
- **Fil:** `trigger_call_simple.py`
- **Deploys:** Railway (europe-west4)
- **Cost:** $0-2/månad (gratis tier)

**Environment Variables (Railway Dashboard):**
- `LIVEKIT_URL` = `wss://finn-outbound-ebnrjj6k.livekit.cloud`
- `LIVEKIT_API_KEY` = `APIiTNn7S5KFftf`
- `LIVEKIT_API_SECRET` = `dGjQQjxqqOArdXhpihbQHQ3Ts74db0HeUIS6mLe15fGA`
- `OUTBOUND_SIP_TRUNK_ID` = `ST_eYfMKqYz3bmE`

### 2. n8n Workflow
- **Service:** Self-hosted n8n på Sliplane
- **Nodes:** 3 total
  1. Webhook (trigger från hemsida)
  2. HTTP Request (POST till Railway)
  3. Respond Success

**n8n Webhook URL:** `https://n8n-08hy.sliplane.app/webhook/trigger-finn-call`

### 3. LiveKit Agent
- **Agent:** `elsa-swedish` (deployed på LiveKit Cloud)
- **Deploy:** `lk agent deploy`
- **File:** `src/agent.py`

## Request Format

### Från Hemsida → n8n
```json
POST https://n8n-08hy.sliplane.app/webhook/trigger-finn-call

{
  "name": "Kundnamn",
  "phone": "+46723161614",
  "country": "SE"
}
```

### Från n8n → Railway
```json
POST https://caring-wonder-production.up.railway.app/trigger-call

{
  "name": "Kundnamn",
  "phone": "+46723161614",
  "country": "SE"
}
```

### Response
```json
{
  "success": true,
  "room_name": "call-kundnamn-1759753331254",
  "message": "Call initiated to +46723161614"
}
```

## Deployment

### Railway Update
När du ändrar `trigger_call_simple.py`:
```bash
cd c:\Users\Admin\gpt-realtime.new
railway up
```

### LiveKit Agent Update
När du ändrar `src/agent.py`:
```bash
lk agent deploy
```

### n8n Update
Ändringar sparas automatiskt i n8n UI.

## Testing

### Test Railway webhook direkt:
```bash
curl -X POST https://caring-wonder-production.up.railway.app/trigger-call \
  -H "Content-Type: application/json" \
  -d '{"name": "Nils", "phone": "+46723161614", "country": "SE"}'
```

### Test via n8n webhook:
```bash
curl -X POST https://n8n-08hy.sliplane.app/webhook/trigger-finn-call \
  -H "Content-Type: application/json" \
  -d '{"name": "Nils", "phone": "+46723161614", "country": "SE"}'
```

## Monitoring

### Railway Logs
```bash
railway logs --follow
```

### n8n Logs
Gå till: https://n8n-08hy.sliplane.app/workflows

Klicka på workflow → Executions

### LiveKit Sessions
```bash
lk room list
```

## Troubleshooting

### Railway inte svarar
1. Kolla logs: `railway logs`
2. Verifiera env vars i Railway dashboard
3. Redeploy: `railway up`

### n8n webhook failar
1. Testa Railway direkt (se ovan)
2. Kolla n8n execution logs
3. Verifiera HTTP Request node URL är korrekt

### LiveKit samtal startar inte
1. Verifiera Railway logs visar "Call initiated"
2. Kolla `lk agent list` att agent är deployed
3. Verifiera SIP trunk ID är korrekt

### Telefonnummer format error
- Måste vara E.164 format: `+46723161614`
- Svenska nummer: `+467...` (inte `07...`)

## För Hemsideutvecklaren

**Webhook URL:**
```
https://n8n-08hy.sliplane.app/webhook/trigger-finn-call
```

**Request:**
```javascript
fetch('https://n8n-08hy.sliplane.app/webhook/trigger-finn-call', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: formData.name,          // Från formulär
    phone: formData.phone,        // MÅSTE börja med +
    country: "SE"                 // Hårdkoda "SE" för svenska
  })
})
.then(res => res.json())
.then(data => {
  if (data.success) {
    console.log('Samtal startat!', data.room_name);
  }
})
.catch(err => console.error('Error:', err));
```

**Viktigt:**
- Telefonnummer MÅSTE vara E.164 format (`+467...`)
- Om användaren skriver `0723161614`, konvertera till `+46723161614`

## Arkitektur Diagram

```
┌─────────────────┐
│  Hemsida Form   │
│  (Lovable)      │
└────────┬────────┘
         │ POST {name, phone, country}
         ▼
┌─────────────────┐
│  n8n Webhook    │
│  (Sliplane)     │
└────────┬────────┘
         │ Forward samma data
         ▼
┌─────────────────┐
│ Railway Server  │  ← trigger_call_simple.py
│ (europe-west4)  │  ← Genererar JWT, anropar LiveKit API
└────────┬────────┘
         │ CreateDispatch + CreateSIPParticipant
         ▼
┌─────────────────┐
│  LiveKit Cloud  │
│  finn-outbound  │
└────────┬────────┘
         │ Agent + SIP call
         ▼
┌─────────────────┐
│  Kundens Telefon│
│  +467...        │
└─────────────────┘
```

## Varför Railway?

**Problem:** n8n self-hosted blockerade `crypto` och `jsonwebtoken` modules → Kunde inte generera LiveKit JWT tokens.

**Lösning:** Externt Python server (Railway) som använder LiveKit SDK för JWT generation.

**Alternativ vi testade:**
- ❌ Vercel Serverless - För stort package (250MB limit)
- ❌ n8n Code node - Blockerade crypto modules
- ✅ Railway - Fungerar perfekt, $0-2/månad

## Files

- **Railway server:** `trigger_call_simple.py`
- **Requirements:** `requirements.txt` (används av Railway)
- **Railway config:** `railway.json`, `Procfile`
- **Ignore:** `.railwayignore`
- **Agent:** `src/agent.py` (separat, deployas med `lk agent deploy`)
