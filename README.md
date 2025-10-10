# 🤖 LiveKit Agent Template System

An **enterprise-grade template system** for creating sophisticated AI voice agents with LiveKit, featuring multi-agent workflows, structured tasks, and comprehensive integrations.

## 🚀 Features

### 🎯 **Multi-Workflow Support**
- **Single Agent**: Simple conversational agents
- **Multi-Agent**: Specialist handoffs (Technical, Billing, Sales)
- **Task-Based**: Structured data collection workflows
- **Hybrid**: Combined task and multi-agent workflows

### 🏗️ **Enterprise Architecture**
- **Agent Orchestration**: Sophisticated workflow management
- **State Management**: Conversation state tracking across agents
- **Context Preservation**: Seamless agent transitions
- **Task Composition**: Reusable workflow components

### 🔗 **Comprehensive Integrations**
- **Webhooks**: Real-time event notifications
- **Calendar**: Google Calendar, Outlook integration
- **CRM**: Salesforce, HubSpot connectivity
- **Telephony**: Telnyx, Twilio, LiveKit support
- **Email**: SMTP, SendGrid, Mailgun

### 🌍 **Multi-Language Support**
- **Swedish (Svenska)**: Full localization
- **English**: Complete implementation
- **Extensible**: Easy to add more languages

## 📁 Template Structure

```
template/
├── config/
│   └── agent.creation.md          # Master configuration template
├── src/
│   ├── agents/
│   │   ├── base_agent.py          # Foundation for all agents
│   │   ├── primary_agent.py       # Main entry point agent
│   │   └── specialist_agent.py    # Domain specialists
│   ├── tasks/
│   │   ├── consent_task.py        # Recording consent collection
│   │   └── information_task.py    # Structured data gathering
│   └── workflows/
│       └── main_workflow.py       # Orchestration engine
├── scripts/
│   └── create_agent.py            # Agent generation script
└── assets/greetings/              # Pre-recorded audio files
```

## 🚀 Quick Start

### 1. **Create Your First Agent**

```bash
# Interactive creation (recommended)
make create-agent

# Quick demo agent
make demo

# Using the script directly
python scripts/create_agent.py --output-dir my-agent
```

### 2. **Configure Your Agent**

All agent configuration is done in the `config/agent.creation.md` file (not environment variables). Use the interactive wizard or edit directly:

```yaml
# Essential Configuration
agent_name: "CustomerService"
owner_name: "Acme Corp"
language: "English"
voice: "cedar"
workflow_type: "multi_agent"
business_type: "retail"
use_case: "customer_support"
```

### 3. **Deploy and Run**

```bash
cd my-agent
cp .env.example .env
# Edit .env with ONLY your API keys (LiveKit + OpenAI)
# All agent config is in config/{agent_name}.md
pip install -e .
python agent.py dev
```

## 🏗️ Architecture Patterns

### **Single Agent Pattern**
Perfect for simple use cases:
```python
# Generated agent handles everything
class SimpleAssistant(BaseAgent):
    # Conversational agent with basic tools
```

### **Multi-Agent Pattern**
For complex customer service scenarios:
```python
class PrimaryAgent(BaseAgent):     # Initial contact & routing
class TechnicalAgent(BaseAgent):   # Technical support specialist
class BillingAgent(BaseAgent):     # Billing inquiries
class EscalationAgent(BaseAgent):  # Management escalation
```

### **Task-Based Pattern**
For structured data collection:
```python
class ConsentTask(AgentTask[bool]):        # Recording consent
class ContactInfoTask(AgentTask[Contact]): # Gather contact details
class PurposeTask(AgentTask[Purpose]):     # Understand call reason
```

### **Hybrid Pattern**
Combines structured tasks with agent handoffs:
```python
# Start with tasks for data collection
consent_result = await ConsentTask()
contact_info = await ContactInfoTask()

# Continue with multi-agent workflow
primary_agent = PrimaryAgent()
# Handoffs available based on needs
```

## 🎛️ Configuration Examples

### **Samuel-Style Missed Call Agent**
```yaml
agent_name: "Jim"
owner_name: "Samuel"
language: "Svenska"
use_case: "missed_calls"
workflow_type: "task_based"
tools:
  - name: "calendar_booking"
    enabled: true
  - name: "log_note"
    enabled: true
```

### **Healthcare Triage Agent**
```yaml
agent_name: "HealthAssistant"
business_type: "healthcare"
workflow_type: "hybrid"
tasks:
  consent_collection:
    enabled: true
    required: true
  information_gathering:
    enabled: true
    required_fields: "name,symptoms,urgency"
```

### **E-commerce Support Agent**
```yaml
agent_name: "ShopAssistant"
business_type: "retail"
workflow_type: "multi_agent"
agents:
  secondary:
    enabled: true
    specialization: "order_support"
  escalation:
    enabled: true
    specialization: "complaints"
```

## 🔧 Advanced Features

### **Custom Tools Integration**
```yaml
tools:
  - name: "order_lookup"
    enabled: true
    type: "search"
    description: "Look up customer orders"
    parameters: "order_id,email"
```

### **Webhook Events**
```yaml
integrations:
  webhook:
    enabled: true
    url: "https://your-api.com/agent-events"
    events: "call_start,call_end,handoff,booking"
    auth_header: "Bearer your-token"
```

### **Calendar Integration**
```yaml
integrations:
  calendar:
    enabled: true
    provider: "google"
    timezone: "Europe/Stockholm"
    auto_suggest_times: true
    buffer_minutes: 15
```

## 📊 Use Cases

### **🏢 Business Applications**
- **Customer Support**: Multi-tier support with specialist routing
- **Sales Qualification**: Lead capture and routing
- **Appointment Booking**: Calendar integration with availability
- **Order Support**: E-commerce order assistance

### **🏥 Healthcare**
- **Patient Triage**: Symptom assessment and routing
- **Appointment Scheduling**: Provider calendar management
- **Insurance Verification**: Coverage confirmation

### **🏠 Personal Use**
- **Missed Call Assistant**: Personal message taking
- **Appointment Management**: Personal calendar coordination
- **Information Gathering**: Contact collection and organization

## 🚀 Generated Agent Features

Every generated agent includes:

- ✅ **Professional Voice Interface**: Natural conversation flow
- ✅ **Automatic Transcription**: Real-time speech-to-text
- ✅ **Context Awareness**: Maintains conversation state
- ✅ **Error Handling**: Graceful failure recovery
- ✅ **Analytics Integration**: Comprehensive logging
- ✅ **Multi-Language Support**: Localized responses
- ✅ **Security Compliance**: Data protection features
- ✅ **Testing Framework**: Built-in test suite
- ✅ **Documentation**: Complete usage guide

## 🛠️ Development Commands

```bash
# Install template system
make install

# Create new agent interactively
make create-agent

# Create specific agent types
make samuel-agent          # Missed call assistant
make customer-service      # Support agent
make healthcare-agent      # Medical triage

# Development tools
make dev                   # Install dev dependencies
make test                  # Run test suite
make clean                 # Clean up generated files
```

## 📚 Documentation

- **[Agent Configuration Guide](docs/configuration.md)**: Complete config reference
- **[Workflow Patterns](docs/workflows.md)**: Architecture patterns
- **[Integration Setup](docs/integrations.md)**: External service setup
- **[Deployment Guide](docs/deployment.md)**: Production deployment
- **[API Reference](docs/api.md)**: Function and class reference

## 🤝 Contributing

This template system is designed to be extensible:

1. **Add New Agent Types**: Extend `BaseAgent` class
2. **Create Custom Tasks**: Implement `AgentTask[T]` interface
3. **Add Integrations**: Extend integration configuration
4. **Support New Languages**: Add localization files

## 📄 License

MIT License - Use this template system to create amazing voice agents!

## 🙋 Support

- **Documentation**: Complete guides in `/docs`
- **Examples**: Reference implementations in `/examples`
- **Issues**: Report bugs and request features

---

**Created with LiveKit Agent Template System** - The professional way to build voice agents! 🎉
