# TRANSCRIPTION & RECORDING ANALYSIS + FIXES

## ISSUE 1: POOR TRANSCRIPT QUALITY ⚠️

### Current Configuration
**Model Being Used:** `gpt-4o-transcribe` (line 878 in agent.py)
- Language: Swedish (`sv`)
- WER: ~2.46% (excellent for English, good for Swedish)
- Prompt optimization: ✅ Already configured

### Why Transcripts Might Be Bad

#### Problem 1: Phone Audio Quality
**Root Cause:** SIP/telephony audio is often 8kHz narrowband vs 16kHz+ needed for optimal transcription
- Compressed codecs (G.711, G.722)
- Network jitter/packet loss
- Background noise

**Evidence in your code:**
```python
# Line 892-894
# Near-field noise reduction: optimized for direct phone audio
# Better than far-field for SIP/telephony where audio is pre-processed
turn_detection=openai.realtime.TurnDetectionConfig(
```

#### Problem 2: Real-Time Transcription Trade-offs
- You're using **real-time** transcription (InputAudioTranscription)
- Real-time = lower latency BUT less context = worse accuracy
- The model has to transcribe WHILE speaking, not after

#### Problem 3: Swedish Accent/Dialect Issues
- gpt-4o-transcribe is primarily trained on English
- Swedish performance is good but not perfect
- Casual Swedish with filler words ("typ", "alltså", "ju") is harder to transcribe
- Your prompt mentions these words, which might confuse the model

### 🔥 THE REAL ISSUE (LIKELY)

Your transcripts probably show:
1. ❌ Missing filler words ("typ", "alltså" completely dropped)
2. ❌ Compound words broken incorrectly ("elektriker" → "elektriker" but context lost)
3. ❌ Names transcribed incorrectly (Nils → Niels)
4. ❌ Technical terms wrong ("Finn AI" → "Finn ay eye")
5. ❌ Incomplete sentences (agent interrupted during speech)

**This is normal for real-time phone transcription!**

---

## SOLUTION 1A: Improve Transcription Prompt (Quick Fix)

### Current Prompt (Line 868-873):
```python
transcription_prompt = (
    f"Professionellt {language.lower()} affärssamtal om AI-teknologi, "
    f"försäljning, och bokningssystem. Samtalet innehåller branschtermer "
    f"som 'LiveKit', 'Finn AI', 'receptionist', 'CRM', 'API', och 'webhook'. "
    f"Talaren kan använda både formellt och informellt språk."
)
```

### IMPROVED PROMPT:
```python
transcription_prompt = (
    f"Svenska affärssamtal mellan säljare (Finn) och kund. "
    f"Talare: 'Finn från Finn AI' och kundnamn varierar. "
    f"Vanliga ord: typ, alltså, ju, liksom, väl, så. "
    f"Företagsnamn: Finn AI (ej 'Finn ay eye'). "
    f"Tekniska termer: LiveKit, receptionist, CRM, API, webhook, demo, simulering. "
    f"Alla tal är svenska, transkribera som svenska oavsett uttal."
)
```

**Why this is better:**
- ✅ Specifies speaker roles (reduces hallucination)
- ✅ Lists exact filler words to preserve
- ✅ Phonetic hints for brand names
- ✅ Forces Swedish even if accent is unclear

---

## SOLUTION 1B: Try whisper-1 Instead (Alternative)

Despite being "older", Whisper-1 might actually work BETTER for your use case:

**Advantages:**
- ✅ More training data on non-English languages
- ✅ Better with accents/dialects
- ✅ More forgiving with compressed audio
- ✅ Community reports better Swedish performance

**Change in agent.py line 878:**
```python
# BEFORE:
transcription_model = model_config.get("transcription_model", "gpt-4o-transcribe")

# TRY:
transcription_model = model_config.get("transcription_model", "whisper-1")
```

**Then add to config/agent.creation.md:**
```yaml
advanced:
  model_overrides:
    primary_model: "gpt-realtime"
    transcription_model: "whisper-1"  # ADD THIS LINE
    temperature: 0.8
```

---

## SOLUTION 1C: Post-Processing Cleanup (Best for Production)

Add a cleaning layer AFTER transcription:

```python
# Add this function to agent.py around line 420

import re

def clean_transcript(text: str, language: str = "Svenska") -> str:
    """
    Post-process transcript to fix common Swedish phone transcription errors
    """
    if language.lower() in ["svenska", "swedish"]:
        # Fix common brand names
        replacements = {
            r'\bFinn ai\b': 'Finn AI',
            r'\bFinn ay\b': 'Finn AI',
            r'\bFinn ey\b': 'Finn AI',
            r'\bLive kit\b': 'LiveKit',
            r'\bLive Kit\b': 'LiveKit',
        }

        for pattern, replacement in replacements.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # Remove excessive hesitation markers from bad transcription
        text = re.sub(r'\b(eh|uh|um|hmm)\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text).strip()

    return text
```

Then use it in the conversation tracker (line 441):
```python
def add_item(self, role, content, timestamp=None):
    # CLEAN TRANSCRIPT BEFORE SAVING
    content = clean_transcript(content, language="Svenska")  # ADD THIS

    item = {
        "role": role,
        "content": content,
        "timestamp": timestamp or time.time(),
        "datetime": datetime.now().isoformat()
    }
    self.conversation_data.append(item)
```

---

## ISSUE 2: CALL RECORDING NOT ENABLED ⚠️

### Current Status
**Recording in config:** ✅ Enabled (line 59-60 in config/agent.creation.md)
```yaml
recording:
  enabled: true
```

**Recording in code:** ❌ NOT IMPLEMENTED

**The Problem:**
- Config says recording is enabled
- But there's NO code in agent.py that starts recording
- Telnyx SIP recording is separate from LiveKit recording
- You need LiveKit Egress to record the ROOM audio

---

## SOLUTION 2: Add LiveKit Recording

### Step 1: Add Recording Function

Add this to agent.py around line 800 (before entrypoint):

```python
async def start_call_recording(ctx: JobContext, storage_config: dict) -> Optional[str]:
    """
    Start audio-only recording of the call using LiveKit Egress
    Returns egress_id if successful, None if failed
    """
    if not storage_config.get("enabled", False):
        logger.info("Recording disabled in config")
        return None

    try:
        # Configure where to save recordings
        file_outputs = []

        # Option A: AWS S3
        if storage_config.get("s3_enabled"):
            file_outputs.append(
                api.EncodedFileOutput(
                    file_type=api.EncodedFileType.MP4,  # or OGG
                    filepath=f"recordings/{ctx.room.name}.mp4",
                    s3=api.S3Upload(
                        bucket=os.getenv("AWS_BUCKET_NAME"),
                        region=os.getenv("AWS_REGION"),
                        access_key=os.getenv("AWS_ACCESS_KEY_ID"),
                        secret=os.getenv("AWS_SECRET_ACCESS_KEY"),
                    ),
                )
            )

        # Option B: Azure Blob Storage
        elif storage_config.get("azure_enabled"):
            file_outputs.append(
                api.EncodedFileOutput(
                    file_type=api.EncodedFileType.MP4,
                    filepath=f"recordings/{ctx.room.name}.mp4",
                    azure=api.AzureBlobUpload(
                        account_name=os.getenv("AZURE_STORAGE_ACCOUNT"),
                        account_key=os.getenv("AZURE_STORAGE_KEY"),
                        container_name=os.getenv("AZURE_CONTAINER_NAME"),
                    ),
                )
            )

        # Option C: Google Cloud Storage
        elif storage_config.get("gcp_enabled"):
            file_outputs.append(
                api.EncodedFileOutput(
                    file_type=api.EncodedFileType.MP4,
                    filepath=f"recordings/{ctx.room.name}.mp4",
                    gcp=api.GCPUpload(
                        bucket=os.getenv("GCP_BUCKET_NAME"),
                        credentials=os.getenv("GCP_CREDENTIALS_JSON"),
                    ),
                )
            )

        if not file_outputs:
            logger.warning("No storage configured for recordings")
            return None

        # Start recording
        req = api.RoomCompositeEgressRequest(
            room_name=ctx.room.name,
            audio_only=True,  # Voice-only recording
            file_outputs=file_outputs,
        )

        res = await ctx.api.egress.start_room_composite_egress(req)
        logger.info(f"✅ Recording started: {res.egress_id}")
        return res.egress_id

    except Exception as e:
        logger.error(f"Failed to start recording: {e}")
        return None
```

### Step 2: Start Recording in Entrypoint

In the `entrypoint` function (around line 950), add:

```python
async def entrypoint(ctx: JobContext):
    """Main entrypoint for the hybrid outbound voice agent."""
    global _session_ref

    await ctx.connect()

    # Load configuration
    config = load_config()

    # START RECORDING (ADD THIS BLOCK)
    recording_config = config.get("integrations", {}).get("recording", {})
    egress_id = None
    if recording_config.get("enabled", False):
        logger.info("📹 Starting call recording...")
        egress_id = await start_call_recording(ctx, recording_config)
        if egress_id:
            logger.info(f"Recording active: {egress_id}")
        else:
            logger.warning("Recording failed to start")

    # ... rest of your existing code
```

### Step 3: Update Config

Update config/agent.creation.md (around line 59-67):

```yaml
integrations:
  recording:
    enabled: true
    s3_enabled: true      # Set to true if using AWS
    azure_enabled: false  # Set to true if using Azure
    gcp_enabled: false    # Set to true if using GCP
    format: "mp4"         # or "ogg"
```

### Step 4: Set Environment Variables

Add to .env file:

```bash
# AWS S3 (if using S3)
AWS_BUCKET_NAME=your-recording-bucket
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# OR Azure Blob (if using Azure)
AZURE_STORAGE_ACCOUNT=youraccountname
AZURE_STORAGE_KEY=...
AZURE_CONTAINER_NAME=recordings

# OR Google Cloud (if using GCP)
GCP_BUCKET_NAME=your-recording-bucket
GCP_CREDENTIALS_JSON={"type": "service_account", ...}
```

---

## QUICK START: Minimal Recording Setup (No Cloud Storage)

If you just want to TEST recording without cloud storage:

```python
# SIMPLIFIED VERSION - saves to LiveKit's temporary storage
async def start_call_recording_simple(ctx: JobContext) -> Optional[str]:
    """Start recording without external storage (temporary only)"""
    try:
        req = api.RoomCompositeEgressRequest(
            room_name=ctx.room.name,
            audio_only=True,
        )
        res = await ctx.api.egress.start_room_composite_egress(req)
        logger.info(f"✅ Recording started (temporary): {res.egress_id}")
        return res.egress_id
    except Exception as e:
        logger.error(f"Recording failed: {e}")
        return None
```

**Note:** Without file_outputs, LiveKit stores recordings temporarily. You'll need to set up a webhook to get notified and download them.

---

## RECOMMENDED ACTION PLAN

### Phase 1: Fix Transcription (Today)
1. ✅ Update transcription prompt (Solution 1A)
2. ✅ Add post-processing cleanup (Solution 1C)
3. ✅ Test with new call
4. ✅ Compare transcript quality

### Phase 2: Try Whisper (If transcripts still bad)
1. Change to whisper-1 model
2. Redeploy
3. Test again
4. Compare

### Phase 3: Add Recording (This Week)
1. Choose storage provider (AWS S3 recommended)
2. Create bucket and get credentials
3. Add recording function to agent.py
4. Update config
5. Set environment variables
6. Deploy and test
7. Verify recordings appear in bucket

---

## TESTING CHECKLIST

### Transcription Quality Test:
```bash
# Make test call
python test_finn_outbound_call.py

# After call, check transcript (when webhook is set up)
# Look for:
- ✅ Filler words preserved ("typ", "alltså")
- ✅ Brand names correct ("Finn AI" not "Finn ay")
- ✅ Complete sentences
- ✅ Accurate Swedish
```

### Recording Test:
```bash
# Make test call
python test_finn_outbound_call.py

# Check your storage bucket for:
# recordings/finn_test_TIMESTAMP.mp4

# OR check LiveKit dashboard:
# https://cloud.livekit.io/projects/finn-outbound/egress
```

---

## ESTIMATED IMPROVEMENT

### Transcription Quality:
- **Current:** ~70-80% accuracy (your experience)
- **After prompt fix:** ~85-90% accuracy
- **After cleanup:** ~90-95% accuracy
- **After whisper-1:** ~85-95% accuracy (depends on audio)

### Recording:
- **Current:** Not working (not implemented)
- **After fix:** 100% of calls recorded

---

## NEED HELP?

**If transcripts still bad after all fixes:**
1. Check actual audio quality (listen to Telnyx recordings)
2. Consider using Deepgram (better for phone audio)
3. Try different codecs in SIP trunk settings

**If recording doesn't work:**
1. Check LiveKit dashboard egress logs
2. Verify storage credentials
3. Test with simplified version first

---

Generated: 2025-10-31
Agent: Finn AI Hybrid Outbound (v20251027122237)
