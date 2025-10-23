# OpenAI Realtime API TTS-Only Implementation - Executive Summary

## Quick Overview

This document summarizes the research findings and feasibility assessment for implementing a custom TTS component using OpenAI's Realtime API with Marin/Cedar voices for LiveKit SIP phone calls.

---

## Key Findings

### 1. Technical Feasibility: POSSIBLE (with caveats)

The OpenAI Realtime API **can** be used for TTS-only purposes, but it was designed for speech-to-speech conversations, not dedicated TTS. We would need to:

- Establish WebSocket connection to `wss://api.openai.com/v1/realtime`
- Configure session with `modalities: ["text", "audio"]`
- Send text via `response.create` events
- Receive base64-encoded PCM16 audio (24kHz mono)
- Convert to LiveKit AudioFrames
- LiveKit automatically resamples 24kHz → 8kHz for SIP

**Technical Complexity**: Medium-High

### 2. Audio Quality: HIGH

**Marin and Cedar voices** are exclusive to the Realtime API and represent OpenAI's highest quality TTS:
- Most natural-sounding speech
- Better prosody and emotion
- Improved pacing and tone control
- Can follow style instructions ("snappy and professional", "kind and empathetic")

**Quality Comparison**:
- Realtime API (Marin/Cedar): ⭐⭐⭐⭐⭐ (Best)
- Standard TTS (Nova, tts-1-hd): ⭐⭐⭐⭐ (Very Good)
- Standard TTS (Alloy, tts-1): ⭐⭐⭐ (Good)

### 3. Cost Impact: CRITICAL CONCERN

**Per 1M tokens**:
- Realtime API audio output: $200
- Standard TTS API: $15

**Per minute of audio**:
- Realtime API: ~$0.24/min
- Standard TTS: ~$0.015/min

**Cost Ratio**: **16x more expensive**

For a 2-minute call with 1 minute of agent speech:
- Realtime API: $0.24
- Standard TTS: $0.015

For 1000 calls/month: $240 vs $15 = **$225/month difference**

### 4. Reliability Concerns: MEDIUM RISK

Community reports indicate potential issues:

**Issue #1**: Manual conversation items may cause API to stop generating audio
- **Severity**: High
- **Workaround**: Use `response.create` with instructions instead
- **Status**: Testable workaround available

**Issue #2**: WebSocket connection stability
- **Severity**: Medium
- **Mitigation**: Retry logic, timeouts, connection pooling
- **Status**: Standard engineering practice

**Issue #3**: Latency unknown
- **Expected**: 300-400ms (needs benchmarking)
- **Comparison**: Standard TTS is 200-300ms
- **Status**: Needs real-world testing

### 5. Integration Complexity: MEDIUM

**Implementation Requirements**:
1. Create custom `TTS` class inheriting from `livekit.agents.tts.TTS`
2. Implement `ChunkedStream` with WebSocket management
3. Handle session initialization and audio streaming
4. Error handling and retry logic
5. Integration testing with SIP calls

**Estimated Development Time**: 2-3 days for core implementation + 1-2 days testing

---

## Comparison: Realtime TTS vs Standard TTS

| Factor | Realtime API (Marin/Cedar) | Standard TTS (Nova, tts-1-hd) |
|--------|---------------------------|------------------------------|
| **Voice Quality** | ⭐⭐⭐⭐⭐ Highest | ⭐⭐⭐⭐ Very Good |
| **Naturalness** | Most natural, expressive | Natural, professional |
| **Cost per min** | $0.24 | $0.015 |
| **Latency** | 300-400ms (estimated) | 200-300ms |
| **Reliability** | Medium (needs testing) | High (proven) |
| **Complexity** | Medium-High | Low (built-in) |
| **Implementation** | Custom WebSocket TTS | One line: `openai.TTS()` |
| **Maintenance** | Higher | Lower |

---

## Recommendation

### RECOMMENDATION: Start with Standard TTS, Evaluate Later

**Phase 1: Optimize Current Setup (Week 1)**
```python
# Use best standard TTS configuration
tts = openai.TTS(
    voice="nova",        # Highest quality standard voice
    model="tts-1-hd",    # HD quality model
    speed=1.0,
)
```

**Why Nova?**
- Highest quality of standard voices
- Proven reliability
- 16x cheaper than Realtime
- Already integrated with LiveKit

**Phase 2: Collect Baseline Metrics (Week 2-3)**
- User satisfaction scores
- Call completion rate
- Audio quality feedback
- Cost per successful call
- Latency measurements

**Phase 3: A/B Test Realtime TTS (Week 4-5)** *(Optional)*

**IF** baseline metrics show room for improvement:
1. Implement Realtime TTS with fallback (using implementation plan)
2. Route 10% of calls to Realtime TTS
3. Compare metrics side-by-side

**Decision Criteria**:
- User satisfaction improvement > 20%? → Consider rollout
- Marginal difference? → Stay with standard
- Cost concerns outweigh benefits? → Stay with standard

**Phase 4: Make Final Decision**

Only implement Realtime TTS if:
- [ ] Quality improvement is measurable and significant
- [ ] Users consistently prefer Realtime TTS voice
- [ ] Cost increase is justified by business value
- [ ] No reliability issues discovered during testing

---

## Alternative Solutions

### Option 1: Wait for Standard API Update

OpenAI may eventually add Marin/Cedar to standard TTS API. This would give you:
- Best voice quality
- Standard API pricing
- Proven reliability
- No custom implementation

**Action**: Monitor OpenAI announcements

### Option 2: Optimize Current Nova Voice

Before investing in Realtime TTS:
1. Ensure using `tts-1-hd` model (not `tts-1`)
2. Test speed settings (0.9-1.1) for optimal naturalness
3. Optimize text prompts for better prosody
4. Test other voices (Shimmer, Alloy) if Nova not optimal

Example optimization:
```python
tts = openai.TTS(
    voice="nova",
    model="tts-1-hd",  # High-definition quality
    speed=1.0,         # Natural pacing
)
```

### Option 3: Try ElevenLabs for Comparison

ElevenLabs offers very high-quality voices with LiveKit integration:

```python
from livekit.plugins import elevenlabs

tts = elevenlabs.TTS(
    model_id="eleven_turbo_v2",
    voice="your-voice-id"
)
```

**Pros**:
- Extremely natural voices
- Fine-grained control
- Proven LiveKit integration

**Cons**:
- Costs vary by plan
- Requires separate API key

---

## Cost-Benefit Analysis

### Scenario: 1000 calls/month, 1.5 min agent speech per call

**Current (Standard TTS)**:
- Cost: 1000 calls × 1.5 min × $0.015/min = **$22.50/month**
- Quality: Very Good (⭐⭐⭐⭐)

**Realtime TTS**:
- Cost: 1000 calls × 1.5 min × $0.24/min = **$360/month**
- Quality: Excellent (⭐⭐⭐⭐⭐)
- Difference: **+$337.50/month** (+1400%)

**Break-even question**:
Is the quality improvement worth $337.50/month?

### Value Calculation

If improved voice quality:
- Reduces hang-ups by 10% → Saves ~100 calls
- Increases customer satisfaction → Better retention
- Enhances brand perception → Premium positioning

Then the cost may be justified.

**However**, this requires data to validate.

---

## Implementation Risk Matrix

| Risk | Likelihood | Impact | Mitigation | Priority |
|------|-----------|--------|-----------|----------|
| Audio output issues | Medium | High | Use response.create, testing | High |
| High costs | Certain | High | A/B test, monitoring | Critical |
| Latency degradation | Medium | Medium | Benchmarking | Medium |
| WebSocket instability | Low | Medium | Retry logic | Low |
| Rate limiting | Low | High | Monitoring, fallback | Medium |

**Overall Risk Assessment**: MEDIUM-HIGH

Risks are manageable but require careful implementation and testing.

---

## Quick Decision Framework

### Implement Realtime TTS NOW if:
- [ ] Budget allows for 16x TTS cost increase
- [ ] Voice quality is critical brand differentiator
- [ ] Current TTS quality is causing measurable issues
- [ ] Team has bandwidth for 3-5 days implementation + testing

### Implement Realtime TTS LATER if:
- [ ] Want to optimize current setup first
- [ ] Need to establish baseline metrics
- [ ] Prefer proven, low-risk approach
- [ ] Want to wait for A/B test data

### DON'T Implement Realtime TTS if:
- [ ] Cost sensitivity is high
- [ ] Current TTS quality is acceptable
- [ ] Team prefers simpler, proven solutions
- [ ] Can't justify 16x cost increase

---

## Next Steps

### If Proceeding with Realtime TTS:

**Step 1**: Review full implementation plan
- File: `docs/REALTIME_TTS_IMPLEMENTATION_PLAN.md`

**Step 2**: Set up development environment
- Create `src/custom_tts/` directory
- Add websockets dependency
- Configure test environment

**Step 3**: Implement core TTS class
- Follow code examples in Section 4
- Start with basic WebSocket connection
- Add error handling incrementally

**Step 4**: Integration testing
- Test with LiveKit locally (if possible)
- Deploy to cloud for SIP testing
- Monitor logs carefully

**Step 5**: A/B testing
- Route 10% traffic to Realtime TTS
- Collect metrics for 1-2 weeks
- Make data-driven decision

### If Optimizing Current Setup Instead:

**Step 1**: Verify current TTS configuration
```python
# In agent.py, ensure using:
tts=openai.TTS(
    voice="nova",      # Or test "shimmer", "alloy"
    model="tts-1-hd",  # HD quality
    speed=1.0,         # Adjust 0.9-1.1 if needed
)
```

**Step 2**: Collect quality metrics
- Call completion rate
- User feedback
- Audio clarity issues
- Cost tracking

**Step 3**: Optimize prompts
- Ensure LLM generates natural text
- Avoid robotic phrasing
- Test punctuation impact

**Step 4**: Benchmark alternatives
- Test different standard voices
- Consider ElevenLabs
- Compare quality subjectively

---

## Final Recommendation Summary

**PRIMARY RECOMMENDATION**:
Start with optimized standard TTS (Nova voice, tts-1-hd model). Collect baseline metrics for 2-3 weeks. Only implement Realtime TTS if data shows clear need and cost is justified.

**RATIONALE**:
1. Standard TTS quality is very good (⭐⭐⭐⭐)
2. 16x cost increase requires strong justification
3. Implementation complexity and risks are non-trivial
4. Optimization of current setup may close quality gap
5. Data-driven decision is better than speculation

**ALTERNATIVE PATH**:
If voice quality is critical and budget allows, implement Realtime TTS with fallback strategy. Test with 10% of calls. Use data to decide on full rollout.

---

## Resources

- **Full Implementation Plan**: `docs/REALTIME_TTS_IMPLEMENTATION_PLAN.md`
- **OpenAI Realtime API Docs**: https://platform.openai.com/docs/guides/realtime
- **LiveKit TTS Plugin Docs**: https://docs.livekit.io/agents/models/tts/
- **Current Agent Code**: `src/agent.py` (lines 489-529)

---

## Questions to Answer Before Proceeding

1. What is our monthly call volume? (affects cost calculation)
2. What % of users complain about current voice quality?
3. What is our budget for TTS costs?
4. Do we have metrics on call completion vs hangup rates?
5. Is voice quality a key brand differentiator?
6. Can we A/B test with small % of users?
7. Do we have resources for 3-5 days implementation?

**Answering these will clarify whether Realtime TTS is the right investment.**

---

**Document Status**: Complete
**Recommendation**: Optimize standard TTS first, evaluate Realtime TTS later with data
**Risk Level**: Medium-High
**Cost Impact**: +1400% ($22.50 → $360/month for 1000 calls)
**Implementation Time**: 3-5 days
**Priority**: Low-Medium (optimize current setup first)
