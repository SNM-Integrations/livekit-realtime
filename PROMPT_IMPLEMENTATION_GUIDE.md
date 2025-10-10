# Prompt Implementation Guide - What You Need to Complete

## Blueprint Files Created
- `system_prompt_blueprint.md` - Professional system prompt architecture
- `conversation_workflow_blueprint.md` - State machine conversation flow
- This implementation guide

## Variables YOU Need to Fill Out

### 1. Conversation Examples (Critical - Need Your Call Experience)

**Identity Uncertainty Patterns** - Fill based on actual calls:
```
{UNCERTAINTY_PATTERNS} = [
  "Maybe, who's this?",
  "I don't know what this is about",
  "Did I request this?",
  // ADD MORE from your actual call experience
]
```

**Adaptive Response Patterns** - Your preferred responses:
```
User: "Maybe, who's this?"
Agent: "{ADAPTIVE_IDENTITY_RESPONSE_PATTERN}"
// FILL: How should Elsa respond naturally while giving context?

User: "I didn't request this"
Agent: "{CONTEXT_CLARIFICATION_RESPONSE_PATTERN}"
// FILL: How to clarify the form submission context?
```

### 2. Elsa's Voice Boundaries (Define Her Personality)

**Personality Traits** - Define what makes Elsa "Elsa":
```
{PERSONALITY_TRAIT_1}: "Professional Directness"
// EXPAND: How direct can she be? What's too direct?

{PERSONALITY_TRAIT_2}: "Balanced Enthusiasm"
// EXPAND: How excited? When to dial it up/down?

{PERSONALITY_TRAIT_3}: "Collaborative Approach"
// EXPAND: How to show partnership vs just selling?
```

**Language Boundaries**:
```
{ACCEPTABLE_VARIATIONS} = // What can she improvise?
{FORBIDDEN_DEVIATIONS} = // What must she never say?
{FORMALITY_RANGE} = // How formal/informal?
```

### 3. Objection Handling Scripts (From Your Sales Experience)

**Common Objections** - What do people actually say?:
```
Price: "{PRICE_OBJECTION_PATTERNS}"
// FILL: "It's too expensive", "We don't have budget", etc.

Timing: "{TIMING_OBJECTION_PATTERNS}"
// FILL: "Not now", "Call back later", etc.

Trust: "{TRUST_OBJECTION_PATTERNS}"
// FILL: "Sounds too good to be true", "Who are you really?", etc.
```

**Your Response Frameworks**:
```
{PRICE_OBJECTION_RESPONSE_TEMPLATE} =
// HOW do you handle price objections?

{TIMING_OBJECTION_RESPONSE_TEMPLATE} =
// HOW do you create urgency respectfully?
```

### 4. Discovery Questions (Your Sales Process)

**What You Ask to Qualify**:
```
{BUSINESS_CHALLENGE_QUESTION} =
// Your go-to question to understand their pain?

{CURRENT_STATE_QUESTION} =
// How do you find out what they're doing now?

{DECISION_MAKER_QUESTION} =
// How do you identify who makes decisions?
```

### 5. Value Demonstration Scripts

**Three-Option Presentation** - How you present solutions:
```
{INBOUND_SOLUTION_DESCRIPTION} =
// How do you explain the missed calls solution?

{OUTBOUND_SOLUTION_DESCRIPTION} =
// How do you explain the lead follow-up solution?

{HYBRID_SOLUTION_DESCRIPTION} =
// How do you explain the full package?
```

### 6. Conversation Recovery (When Things Go Wrong)

**When Elsa Gets Off Track**:
```
{RECOVERY_STRATEGY} =
// How should she get back to booking the demo?

{CLARIFICATION_PROTOCOL} =
// What does she say when confused?

{SCRIPT_RETURN_TEMPLATE} =
// How does she smoothly return to the main script?
```

### 7. Success Metrics (Your Goals)

**What Defines Success**:
```
{DEMO_BOOKING_CONFIRMATION} =
// What confirms a successful booking?

{CONTACT_VERIFICATION} =
// What contact info do you need?

{NEXT_STEPS_TEMPLATE} =
// What happens after booking?
```

## Implementation Priority

### Phase 1 (Essential - Do First):
1. **Conversation Examples** - Fill uncertainty patterns and responses
2. **Elsa's Voice Boundaries** - Define her personality limits
3. **Discovery Questions** - Your core qualification process

### Phase 2 (Important):
4. **Objection Handling** - Your standard responses to pushback
5. **Value Demonstration** - How you present the three solutions
6. **Conversation Recovery** - Getting back on track

### Phase 3 (Polish):
7. **Success Metrics** - Defining and measuring success
8. **Advanced Workflows** - Edge cases and special scenarios

## Notes for Implementation

**Swedish Cultural Elements** (Already Filled):
- Direct but polite communication style
- "Du" usage, first names immediately
- Lagom principle - balanced approach
- Consensus-driven decision making

**Business Intelligence** (Already Filled):
- 18% missed call statistic
- 2999 SEK pricing starting point
- Three core solutions (inbound, outbound, hybrid)
- ROI calculation framework

**Technical Framework** (Already Built):
- State management system
- Adaptive response engine
- Quality assurance checkpoints
- Brand consistency validation

## Ready to Implement

Once you fill these variables, we can:
1. Update the agent.py with the new prompt structure
2. Test the improved conversation flow
3. Deploy and measure results
4. Iterate based on actual call performance

**The blueprints provide the professional architecture. Your domain expertise fills the content that makes it effective.**