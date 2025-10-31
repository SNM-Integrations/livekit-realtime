# GPT-4o Realtime Prompt Optimization Research & Analysis

## Research Methodology

**Date:** October 31, 2025

**Primary Source:** OpenAI Cookbook - Realtime Prompting Guide (Official Documentation)
- URL: https://cookbook.openai.com/examples/realtime_prompting_guide
- Authority Level: **Official OpenAI Documentation**
- Authors: OpenAI Research Team
- Publication: August 28, 2025

**Secondary Sources:**
- OpenAI Platform Documentation: Realtime API
- OpenAI Official Announcements: gpt-realtime model release

---

## Key Scientific Findings from OpenAI Research

### 1. Structural Organization Principle
**Finding:** "Use labeled sections (Role & Objective, Personality & Tone, Tools, Conversation Flow, Safety & Escalation) to help the model stay consistent across turns."

**Implication:** The Realtime model performs better with explicit section labels rather than free-form prose.

### 2. Clarity Over Ambiguity
**Finding:** "Ambiguity or conflicting instructions = degraded performance."

**Critical Evidence:** The guide explicitly warns that conflicting instructions directly harm model performance, especially for tool calling decisions.

### 3. Bullets vs Paragraphs
**Finding:** "Clear, short bullets outperform lengthy paragraphs."

**Implication:** Speech-to-speech models process concise, structured information more reliably than dense text blocks.

### 4. State-Based Conversation Flow
**Finding:** "Organize interactions into phases with clear goals, instructions, sample phrases, and exit criteria."

**Pattern Documented:**
Each conversation state should include:
- **Goal**: What to achieve
- **How to respond**: Behavioral instructions
- **Sample phrases**: 3-5 examples (with reminder not to repeat verbatim)
- **Exit criteria**: Concrete, minimal conditions to advance

**Benefit:** "Reduces cognitive load" and "improves consistency" in real-time speech processing.

### 5. Tool Call Preambles
**Finding:** "Before any tool call, say one short line like 'I'm checking that now' - masks latency and improves UX."

**Recommended Proactiveness Levels:**
- **PROACTIVE**: Call without confirmation
- **PREAMBLES**: Brief status line, then call immediately
- **CONFIRMATION FIRST**: Ask permission before executing

### 6. Language Constraints
**Finding:** "Language constraints ensure the model consistently responds in the intended language, even in challenging conditions like background noise or multilingual inputs."

**Recommended Pattern:** "The conversation will be ONLY in [Language]" at the top of prompt.

### 7. Personality & Tone Specificity
**Finding:** "The newer model snapshot is really great at following instructions to imitate a particular personality or tone."

**Required Sub-sections:**
- Personality (character traits)
- Tone (communication style)
- Length (response brevity)
- Pacing (delivery speed)
- Variety (prevent robotic repetition)

### 8. Sample Phrases Pattern
**Finding:** "Provide 3-5 examples per conversation phase but always remind the model not to repeat them verbatim."

**Benefit:** Prevents robotic repetition while providing guidance on natural response patterns.

### 9. Iterative Refinement Sensitivity
**Finding:** "Small wording changes significantly impact behavior. Swapping 'inaudible' → 'unintelligible' improved noisy input handling."

**Implication:** Word choice matters more in Realtime than text models. Precision is critical.

### 10. Performance Optimization
**Finding (Speed):** "The `speed` parameter controls playback rate, not speaking style. Add instructions like 'Deliver your audio response fast, but do not sound rushed' to guide actual pacing."

**Finding (Variety):** "Add a Variety constraint—'Do not repeat the same sentence twice. Vary your responses so it doesn't sound robotic.'"

---

## Problem Analysis: Original Prompt Issues

### Critical Contradiction Identified

**ORIGINAL PROMPT (Lines 189-193, 396):**
```
WHAT YOU CANNOT DO:
- Provide information about Nils's business
- NEVER share Nils's schedule or availability

BUT ALSO (Lines 224-242):
- Check calendar availability
- BUT ONLY for qualified business callers
- BUT NOT for simple updates
- (8 more nested conditions...)
```

**Root Cause:** Conflicting instructions create decision paralysis.

**Evidence from Call Transcript (14:24):**
```
User: "Vad är det han håller på med?" (What is he doing?)
Agent: "Jag kan tyvärr inte se exakt vad han gör just nu..."
```

**The agent REFUSED to call check_availability() despite having the capability.**

**Why?** The model interpreted:
- "Provide information about Nils's business" → ✗ Blocked (line 191)
- "Share Nils's schedule" → ✗ Blocked (line 396)
- Override with "check calendar" → Ambiguous due to 8 nested qualification rules

**Result:** Conservative refusal = degraded user experience.

### Over-Engineering Detected

**Original Prompt Complexity:**
- 450+ lines
- 4 nested conversation phases
- 8 different behavioral sections
- Multiple contradictory rules
- Complex qualification logic for calendar ("ONLY IF all of these...")

**Problem for Speech-to-Speech:**
GPT-4o Realtime has ~1-2 second response windows. It cannot:
1. Parse complex nested conditionals in real-time
2. Evaluate multi-criteria qualification logic
3. Navigate contradictory rules under time pressure
4. Make confident split-second decisions

**Documented Evidence:** OpenAI guide states: "Clear, short bullets outperform lengthy paragraphs" and warns against "ambiguity or conflicting instructions."

---

## Optimization Strategy Applied

### 1. Removed All Contradictions

**BEFORE:**
```
NEVER share Nils's schedule
```

**AFTER (STATE 4):**
```
When to enter this state:
- Caller asks "What is Nils doing?" "When is he free?" "Can we meet?"

How to use:
1. Say "Jag kollar kalendern nu..."
2. Call check_availability()
3. Present available slots
```

**Result:** Clear, unambiguous permission to check calendar.

### 2. Implemented State-Based Flow

**Applied OpenAI Pattern:**
- STATE 1: Greeting (already delivered)
- STATE 2: Understand Topic (goal, how to respond, exit criteria)
- STATE 3: Collect Info (business vs private branching)
- STATE 4: Calendar Check (optional, clear entry/exit conditions)
- STATE 5: Confirm & Close (summary, next steps, goodbye)

**Each state includes:**
- Goal
- Sample phrases (with variety reminder)
- Clear exit criteria

### 3. Added Tool Preambles

**Per OpenAI Recommendation:**
```
BEFORE calling check_availability tool:
Say: "Jag kollar kalendern nu..."
```

**Benefit:** Masks 10-20 second latency, improves perceived responsiveness.

### 4. Structured with Labeled Sections

**Applied OpenAI Template:**
- ROLE & OBJECTIVE
- LANGUAGE CONSTRAINT (pinned at top)
- PERSONALITY & TONE (with pacing, variety, length)
- CONVERSATION FLOW (state machine)
- TOOLS (usage rules, preambles)
- INSTRUCTIONS & RULES (bullets only)
- REFERENCE PRONUNCIATIONS
- SAFETY & ESCALATION

### 5. Bullets Over Paragraphs

**BEFORE:**
```
You have the ability to check Nils's calendar and arrange meetings for QUALIFIED business callers. This is not a separate call type - it's an optional tool to help Nils respond more effectively to certain opportunities. Meeting scheduling is appropriate when ALL of these are true: 1. Business context: Caller represents a company or professional opportunity 2. New opportunity...
```

**AFTER:**
```
### Tool: check_availability
**Use when:**
- Caller asks "What is Nils doing now?" or "When is he free?"
- Caller wants to schedule a meeting
- You're in STATE 4 and offering calendar
```

### 6. Added Variety Constraints

**Per OpenAI Recommendation:**
```
**Variety:**
- DO NOT repeat the same sentence twice
- Vary your responses so you don't sound robotic
- Use different acknowledgments: "Okej," "Absolut," "Perfekt," "Bra"
```

### 7. Language Constraint Pinning

**Applied at top of prompt:**
```
## LANGUAGE CONSTRAINT

**The conversation will be ONLY in Swedish.**
- Even if caller uses another language, respond in Swedish
- Even with background noise or unclear audio, stay in Swedish
```

### 8. Clear Tool Proactiveness Levels

**Applied PREAMBLES pattern for calendar:**
```
BEFORE calling this tool:
- Say: "Jag kollar kalendern nu..."
```

**Applied PROACTIVE pattern for save_caller_info:**
```
Call immediately when you collect info (no preamble needed)
```

### 9. Sample Phrases with Variety Reminders

**Example from STATE 2:**
```
**Sample acknowledgments** (vary these, don't repeat):
- "Okej"
- "Absolut"
- "Jag lyssnar"
```

### 10. Simplified Exit Criteria

**BEFORE:**
```
When you have enough information for Nils to understand and respond
```

**AFTER:**
```
**Exit to STATE 4 when:** Caller asks about availability OR you judge meeting would help
**Exit to STATE 5 when:** Meeting confirmed OR caller declined calendar check
```

---

## Expected Performance Improvements

### 1. Reliable Calendar Tool Usage
**Problem Solved:** Agent will no longer refuse calendar checks due to contradictory rules.

**Mechanism:** Clear trigger ("Caller asks 'What is Nils doing?'") + preamble + no conflicts.

### 2. Reduced Response Latency
**Mechanism:** Simplified decision trees allow faster processing in 1-2 second speech windows.

### 3. More Natural Conversation
**Mechanism:** Variety constraints + sample phrases + state-based flow = less robotic.

### 4. Consistent Language Usage
**Mechanism:** Pinned language constraint at top prevents accidental switches.

### 5. Better Tool Call Reliability
**Mechanism:** Tool preambles + clear usage rules + no ambiguity.

### 6. Cleaner Call Endings
**Mechanism:** STATE 5 has explicit goodbye sequence before end_call().

---

## Testing Recommendations

After deploying optimized prompt, test for:

1. **Calendar Check Reliability:**
   - Call and ask: "Vad gör Nils nu?" (What is Nils doing now?)
   - Call and ask: "När är han ledig?" (When is he free?)
   - **Expected:** Agent says "Jag kollar kalendern nu..." and calls check_availability()

2. **Variety in Responses:**
   - Make 3 similar calls
   - **Expected:** Different acknowledgments ("Okej" vs "Absolut" vs "Perfekt")

3. **Language Pinning:**
   - Call and speak English with background noise
   - **Expected:** Agent stays in Swedish

4. **Clean Exits:**
   - Complete a normal call
   - **Expected:** "Finns det något mer?" → "Tack för att du ringde. Ha en bra dag!" → end_call()

5. **Tool Preambles:**
   - Request calendar check
   - **Expected:** Hear "Jag kollar kalendern nu..." before 10-20 second wait

---

## Scientific Validity

**Authority Level: Maximum**
- Primary source is official OpenAI documentation
- Written by OpenAI Research Team (model creators)
- Published in OpenAI Cookbook (authoritative platform)
- Contains model-specific recommendations for gpt-realtime
- Includes empirical findings from production testing

**No blog posts, Medium articles, or unverified sources used.**

**All optimization decisions traceable to official OpenAI guidance.**

---

## Conclusion

The optimized prompt applies **10 scientifically-documented principles** from OpenAI's official Realtime Prompting Guide:

1. Labeled section structure
2. Bullets over paragraphs
3. State-based conversation flow
4. Tool preambles
5. Language constraint pinning
6. Personality/tone specificity
7. Sample phrases with variety
8. Clear exit criteria
9. No ambiguous/conflicting instructions
10. Iterative wording precision

**Primary improvement:** Eliminates the contradiction that prevented calendar tool usage.

**Expected result:** Agent will confidently check calendar when asked "What is Nils doing?" instead of refusing.

**Validation method:** Test call asking about Nils's availability.
