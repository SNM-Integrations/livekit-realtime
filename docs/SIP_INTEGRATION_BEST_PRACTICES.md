# SIP Integration Best Practices
## Proper Setup for LiveKit + Telnyx Voice Agents

This guide ensures your LiveKit voice agents properly connect to SIP calls, handle conversations, and **critically important** - cleanly terminate both the LiveKit room AND the SIP trunk connection to prevent phantom billing.

---

## The Problem: Stuck SIP Calls

### Symptoms
- Telnyx billing dashboard shows calls lasting hours when actual conversation was only minutes
- LiveKit agent session minutes match Telnyx SIP duration (both showing excessive hours)
- No active rooms visible in `lk room list`, but billing continues
- Example: 2 calls totaling 5 minutes → Telnyx shows 11+ hours of billing

### Root Cause
When a LiveKit agent ends a call by deleting the room, **the SIP connection may not properly hang up**. This leaves the Telnyx SIP trunk open for hours/days, causing massive billing waste.

**Why it happens:**
- Using `room.delete_room()` doesn't always send proper SIP BYE signal
- Missing timeout protections allow calls to run indefinitely
- SIP participant not fully connected before greeting starts
- No hard time limits on call duration

---

## The Solution: Triple-Layer Protection

Your agent needs **three independent safeguards** to ensure calls ALWAYS end properly:

### 1. Proper Shutdown Method
✅ **Use `ctx.shutdown()` instead of `delete_room()`**

```python
# ❌ WRONG - May not send SIP BYE
await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))

# ✅ CORRECT - Proper SIP termination
await ctx.shutdown(reason="Call ended")
```

### 2. Silence Timeout (Inactivity Detection)
End calls automatically after 45 seconds of silence:

```python
SILENCE_TIMEOUT = 45  # seconds

async def check_timeouts():
    """Monitor silence and end call if inactive"""
    if silence_duration >= SILENCE_TIMEOUT:
        logger.info(f"Ending call due to {SILENCE_TIMEOUT}s silence")
        await ctx.shutdown(reason="Inactivity timeout")
```

### 3. Absolute Time Limit (Hard Cutoff)
Force-end calls after 10 minutes regardless of activity:

```python
MAX_CALL_DURATION = 600  # 10 minutes in seconds

async def check_timeouts():
    """Monitor total call duration"""
    if total_duration >= MAX_CALL_DURATION:
        logger.info(f"Ending call due to {MAX_CALL_DURATION}s duration limit")
        await ctx.shutdown(reason="Maximum duration reached")
```

---

## Complete Implementation Pattern

### Core Timeout Protection

```python
from livekit import JobContext
import asyncio
import time

# Safety timeouts
SILENCE_TIMEOUT = 45  # End after 45 seconds of silence
MAX_CALL_DURATION = 600  # Hard cutoff at 10 minutes

async def entrypoint(ctx: JobContext):
    # Track timing
    call_start_time = time.time()
    last_activity_time = time.time()

    # Force end call helper
    async def force_end_call(reason: str):
        """Properly terminate both LiveKit room and SIP connection"""
        logger.info(f"Force ending call: {reason}")
        await ctx.shutdown(reason=reason)

    # Timeout monitoring loop
    async def check_timeouts():
        """Monitor both silence and absolute time limits"""
        while True:
            try:
                current_time = time.time()
                total_duration = current_time - call_start_time
                silence_duration = current_time - last_activity_time

                # Check absolute time limit (10 minutes)
                if total_duration >= MAX_CALL_DURATION:
                    await force_end_call(f"Maximum duration {MAX_CALL_DURATION}s reached")
                    break

                # Check silence timeout (45 seconds)
                if silence_duration >= SILENCE_TIMEOUT:
                    await force_end_call(f"Silence timeout {SILENCE_TIMEOUT}s reached")
                    break

                # Check every 5 seconds
                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Timeout monitor error: {e}")
                # Even if monitoring fails, end the call
                await force_end_call("Monitoring error")
                break

    # Start timeout monitoring in background
    timeout_task = asyncio.create_task(check_timeouts())

    # Update activity timestamp on conversation events
    @session.on("conversation_item_added")
    def on_conversation_item(item):
        nonlocal last_activity_time
        last_activity_time = time.time()  # Reset silence timer

    # ... rest of your agent code ...
```

### Proper SIP Participant Greeting

Wait for SIP participant to fully connect before sending greeting (prevents audio cut-off):

```python
from livekit import rtc

async def entrypoint(ctx: JobContext):
    # Event to signal when SIP caller connects
    sip_participant_connected = asyncio.Event()

    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        """Wait for actual SIP caller to join"""
        # Only trigger for SIP participants (the actual phone caller)
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            logger.info("📞 SIP participant connected - ready to greet!")
            sip_participant_connected.set()

    # Start session
    await session.start(room=ctx.room, agent=agent)

    # Wait for SIP participant to join (max 10 seconds)
    try:
        await asyncio.wait_for(sip_participant_connected.wait(), timeout=10.0)
        logger.info("SIP participant confirmed - sending greeting")
    except asyncio.TimeoutError:
        logger.warning("SIP participant didn't connect within 10s, proceeding anyway")

    # Small delay to ensure audio pipeline is ready
    await asyncio.sleep(0.5)

    # NOW send greeting - caller is fully connected
    greeting_message = config.get("first_message", "Hello, how can I help you?")
    await session.generate_reply(
        instructions=f"Say this greeting: '{greeting_message}' and wait for response."
    )
```

### SIP Participant Creation with max_call_duration

When creating SIP participants (outbound calls), add safety timeout:

```python
from google.protobuf.duration_pb2 import Duration
from livekit import api

# Create duration object for 10-minute limit
max_duration = Duration()
max_duration.seconds = 600  # 10 minutes

# Create SIP participant with timeout
sip_request = api.CreateSIPParticipantRequest(
    sip_trunk_id="your_trunk_id",
    sip_call_to="phone_number",
    room_name="room_name",
    participant_identity="caller",
    participant_name="Phone Caller",
    max_call_duration=max_duration  # Force SIP disconnect after 10 minutes
)

await livekit_api.sip.create_sip_participant(sip_request)
```

**Note:** LiveKit has a known bug (Issue #353) where `max_call_duration` doesn't always work reliably. This is why agent-side timeout monitoring is **essential** as a backup.

---

## Complete End-of-Call Flow

```python
@function_tool
async def end_call():
    """
    End call tool - only called when conversation naturally concludes.

    IMPORTANT: Only call this AFTER:
    - All information has been collected
    - Information confirmed with caller
    - Proper goodbye said
    """
    ctx = get_job_context()
    if ctx is None:
        return "Could not end call - no context"

    logger.info("End call tool invoked - terminating session")

    # Use ctx.shutdown() for proper SIP termination
    await ctx.shutdown(reason="Conversation completed")

    return "Call ended successfully"
```

---

## Testing & Verification

### After Deploying Agent

1. **Make a test call**
2. **Have a normal conversation** (1-2 minutes)
3. **Let it end naturally** (say goodbye, let agent trigger end_call)
4. **Immediately check call ended:**
   ```bash
   lk room list
   ```
   Should show: `No rooms found` or empty list

5. **Check Telnyx dashboard** after 5 minutes:
   - Call duration should match actual conversation time (1-2 minutes)
   - NOT hours of duration

### Test Timeout Protections

**Test silence timeout:**
1. Call agent
2. Say hello, then go completely silent
3. After 45 seconds → agent should auto-end call
4. Verify in Telnyx: call duration ≈ 45 seconds

**Test max duration:**
1. Call agent
2. Keep conversation going for 10+ minutes
3. At exactly 10 minutes → agent should force-end call
4. Verify in Telnyx: call duration ≈ 600 seconds (10 min)

### Emergency Cleanup Script

If you discover stuck calls, create this cleanup script:

```python
# cleanup_stuck_calls.py
from livekit import api
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def cleanup_all_rooms():
    """Delete all active rooms to force-close stuck SIP calls"""
    livekit_api = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL"),
        os.getenv("LIVEKIT_API_KEY"),
        os.getenv("LIVEKIT_API_SECRET")
    )

    rooms = await livekit_api.room.list_rooms(api.ListRoomsRequest())

    if not rooms:
        print("✅ No active rooms found")
        return

    print(f"Found {len(rooms)} active rooms. Deleting...")

    for room in rooms:
        print(f"  Deleting room: {room.name}")
        await livekit_api.room.delete_room(
            api.DeleteRoomRequest(room=room.name)
        )

    print("✅ All rooms deleted")

if __name__ == "__main__":
    asyncio.run(cleanup_all_rooms())
```

Run when needed:
```bash
python cleanup_stuck_calls.py
```

---

## Common Issues & Solutions

### Issue: Calls still lasting hours after implementing timeouts

**Possible causes:**
1. Timeout monitoring task is crashing silently
2. `ctx.shutdown()` isn't being awaited properly
3. LiveKit SIP BYE signaling bug (known Issue #353)

**Solutions:**
- Add extensive logging to timeout monitoring
- Wrap `ctx.shutdown()` in try/except with logging
- Contact LiveKit support about SIP integration
- Consider adding external monitoring that calls cleanup script

### Issue: Greeting cut off (caller can't hear first few seconds)

**Cause:** Greeting sent before SIP participant audio pipeline ready

**Solution:** Implement participant_connected event pattern (see above)

### Issue: Agent ends calls too early

**Cause:** Silence timeout too aggressive OR end_call tool triggered prematurely

**Solutions:**
- Increase `SILENCE_TIMEOUT` from 45 to 60 seconds
- Update system prompt to clarify when end_call should be used
- Add explicit "Are we all set?" confirmation before ending

### Issue: No greeting at all

**Cause:** Multiple possible:
1. Timeout waiting for participant connection
2. Exception in greeting code
3. Session not started before greeting

**Solutions:**
- Check logs for timeout or exception messages
- Ensure `session.start()` completes before greeting
- Add try/except around greeting with fallback

---

## Billing Impact

### Without These Safeguards
- **Example:** 2 calls, 5 minutes actual talk time
- **Telnyx billing:** 11+ hours per call = 22+ hours total
- **LiveKit billing:** 1320+ agent session minutes
- **Cost:** ~$13-20+ wasted

### With Triple-Layer Protection
- **Same 2 calls, 5 minutes talk time**
- **Telnyx billing:** 5 minutes total
- **LiveKit billing:** 5 agent session minutes
- **Cost:** ~$0.05 (correct)

**Savings per month** (assuming 100 calls): **$650-1000+**

---

## Prevention Checklist

When creating a new agent:

- [ ] Use `ctx.shutdown(reason)` for call termination (NOT `delete_room()`)
- [ ] Implement 45-second silence timeout
- [ ] Implement 10-minute absolute timeout
- [ ] Add participant_connected event for greeting synchronization
- [ ] Include `max_call_duration` in SIP participant creation
- [ ] Add extensive logging to timeout monitoring
- [ ] Test all three timeout scenarios after deployment
- [ ] Monitor Telnyx dashboard for first 24 hours
- [ ] Keep emergency cleanup script ready

---

## Reference Implementation

See `src/agent.py` in this template for a complete reference implementation with all safeguards properly configured.

**Key sections:**
- Lines ~175-202: Timeout monitoring setup
- Lines ~242-290: Proper call termination with `ctx.shutdown()`
- Lines ~430-455: SIP participant-aware greeting

---

## Further Reading

- [LiveKit Agents Documentation](https://docs.livekit.io/agents/)
- [LiveKit SIP Integration](https://docs.livekit.io/agents/telephony/)
- [LiveKit Issue #353 - max_call_duration bug](https://github.com/livekit/agents/issues/353)
- [Telnyx SIP Trunking](https://telnyx.com/products/sip-trunking)

---

**Last Updated:** 2025-10-05
**Status:** Production-tested pattern - prevents phantom SIP billing
