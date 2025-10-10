#!/usr/bin/env python3
"""
Agent Generation Script - Creates new agents from the template
This script processes the agent.creation.md configuration and generates
a complete agent implementation with all specified features.
"""

import os
import sys
import yaml
import shutil
import argparse
from pathlib import Path
from typing import Dict, Any, List
import re


class AgentGenerator:
    """
    Generates complete agent implementations from template configuration
    """

    def __init__(self, template_dir: str, output_dir: str):
        self.template_dir = Path(template_dir)
        self.output_dir = Path(output_dir)
        self.variables = {}
        self.config = {}

    def load_configuration(self, config_path: str = None) -> Dict[str, Any]:
        """Load and parse agent configuration"""
        if not config_path:
            config_path = self.template_dir / "config" / "agent.creation.md"

        print(f"Loading configuration from: {config_path}")

        try:
            with open(config_path, 'r', encoding='utf-8') as file:
                content = file.read()

            # Extract YAML content (skip markdown comments)
            yaml_lines = []
            in_yaml = False

            for line in content.split('\n'):
                # Skip comment lines that start with #
                if line.strip().startswith('#') and not line.strip().startswith('# ==='):
                    continue

                # Start capturing YAML when we hit non-comment content
                if line.strip() and not line.startswith('#'):
                    in_yaml = True

                if in_yaml:
                    yaml_lines.append(line)

            yaml_content = '\n'.join(yaml_lines)
            self.config = yaml.safe_load(yaml_content) or {}

            print(f"Configuration loaded successfully with {len(self.config)} top-level keys")
            return self.config

        except Exception as e:
            print(f"Error loading configuration: {e}")
            sys.exit(1)

    def collect_variables(self, interactive: bool = True) -> Dict[str, str]:
        """Collect variable values interactively or from config"""
        if interactive:
            return self._collect_interactive_variables()
        else:
            return self._extract_template_variables()

    def _collect_interactive_variables(self) -> Dict[str, str]:
        """Collect variables interactively from user"""
        print("\n🤖 Agent Creation Wizard")
        print("=" * 50)

        variables = {}

        # Essential variables
        essential_vars = [
            ("AGENT_NAME", "What is the agent's name? (e.g., 'Jim', 'CustomerService')", "VoiceAssistant"),
            ("OWNER_NAME", "Who owns this agent? (e.g., 'Samuel', 'Acme Corp')", "Company"),
            ("LANGUAGE", "What language? (English, Svenska)", "English"),
            ("VOICE", "Which voice? (cedar, nova, marin, alloy)", "cedar"),
            ("WORKFLOW_TYPE", "Workflow type? (single_agent, multi_agent, task_based, hybrid)", "single_agent"),
            ("BUSINESS_TYPE", "Business type? (personal, consulting, retail, healthcare)", "consulting"),
            ("USE_CASE", "Primary use case? (missed_calls, customer_support, sales)", "customer_support"),
        ]

        print("\n📋 Essential Configuration:")
        for var, prompt, default in essential_vars:
            value = input(f"{prompt} [{default}]: ").strip()
            variables[var] = value if value else default

        # Workflow configuration
        print("\n⚙️ Workflow Configuration:")
        variables["USE_WORKFLOWS"] = self._ask_bool("Enable multi-agent workflows?", False)
        variables["USE_TASKS"] = self._ask_bool("Enable structured tasks?", False)
        variables["STATE_MANAGEMENT"] = self._ask_bool("Enable state management?", True)

        # Agent configuration
        print("\n👥 Agent Configuration:")
        variables["SECONDARY_ENABLED"] = self._ask_bool("Enable secondary specialist agent?",
                                                      variables["WORKFLOW_TYPE"] != "single_agent")
        variables["ESCALATION_ENABLED"] = self._ask_bool("Enable escalation agent?",
                                                        variables["WORKFLOW_TYPE"] in ["multi_agent", "hybrid"])

        # Task configuration
        if variables["USE_TASKS"]:
            print("\n📋 Task Configuration:")
            variables["CONSENT_ENABLED"] = self._ask_bool("Enable consent collection?", True)
            variables["INFO_GATHERING_ENABLED"] = self._ask_bool("Enable information gathering?", True)
            variables["SCHEDULING_ENABLED"] = self._ask_bool("Enable appointment scheduling?", False)

        # Integration configuration
        print("\n🔗 Integration Configuration:")
        variables["WEBHOOK_ENABLED"] = self._ask_bool("Enable webhook integration?", True)
        variables["CALENDAR_ENABLED"] = self._ask_bool("Enable calendar integration?", False)
        variables["EMAIL_ENABLED"] = self._ask_bool("Enable email integration?", False)

        # Auto-generate related variables
        self._generate_derived_variables(variables)

        return variables

    def _ask_bool(self, prompt: str, default: bool) -> str:
        """Ask a yes/no question and return 'true'/'false'"""
        default_str = "y" if default else "n"
        response = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()

        if not response:
            return "true" if default else "false"

        return "true" if response in ['y', 'yes', 'true', '1'] else "false"

    def _generate_derived_variables(self, variables: Dict[str, str]):
        """Generate derived variables based on main configuration"""
        agent_name = variables.get("AGENT_NAME", "Assistant")
        language = variables.get("LANGUAGE", "English")

        # Generate agent-specific names
        variables["PRIMARY_AGENT_NAME"] = f"Primary{agent_name}"
        variables["SECONDARY_AGENT_NAME"] = f"Technical{agent_name}"
        variables["ESCALATION_AGENT_NAME"] = f"Manager{agent_name}"

        # Generate voices (could be same or different)
        base_voice = variables.get("VOICE", "cedar")
        variables["PRIMARY_VOICE"] = base_voice
        variables["SECONDARY_VOICE"] = base_voice
        variables["ESCALATION_VOICE"] = base_voice
        variables["AUDIO_VOICE"] = base_voice

        # Generate personality traits
        business_type = variables.get("BUSINESS_TYPE", "consulting")
        if business_type == "healthcare":
            variables["PERSONALITY"] = "empathetic, professional, careful"
        elif business_type == "retail":
            variables["PERSONALITY"] = "friendly, helpful, enthusiastic"
        else:
            variables["PERSONALITY"] = "professional, courteous, efficient"

        # Generate greetings based on language
        if language.lower() in ["svenska", "swedish"]:
            variables["FIRST_MESSAGE"] = f"Hej! Du har nått {variables.get('OWNER_NAME', 'företaget')}. Jag är {agent_name} och hjälper gärna till. Vem pratar jag med?"
        else:
            variables["FIRST_MESSAGE"] = f"Hello! You've reached {variables.get('OWNER_NAME', 'our company')}. I'm {agent_name} and I'm here to help. Who am I speaking with?"

        # Set default values for other variables
        defaults = {
            "COMPLEXITY": "intermediate",
            "INDUSTRY": "technology",
            "CALL_PURPOSE": f"{variables.get('USE_CASE', 'general')} assistant",
            "STYLE": "professional",
            "RESPONSE_LENGTH": "medium",
            "GREETING_TONE": "friendly",
            "PRIMARY_PERSONALITY": variables["PERSONALITY"],
            "SECONDARY_PERSONALITY": "knowledgeable, patient, technical",
            "ESCALATION_PERSONALITY": "experienced, diplomatic, solution-focused",
            "PRIMARY_SPEC": "general_support",
            "SECONDARY_SPEC": "technical_support",
            "ESCALATION_SPEC": "complex_issues",
            "HANDOFF_RULES": "intent_based",
            "PRESERVE_CONTEXT": "true",
            "TRACK_STATE": variables.get("STATE_MANAGEMENT", "true"),
            "MAX_HANDOFFS": "5",
            "HANDOFF_KEYWORDS": "technical,support,manager,escalate",
            "AUTO_ESCALATION": "false",
            "CONSENT_REQUIRED": "false",
            "REQUIRED_FIELDS": "name,phone",
            "OPTIONAL_FIELDS": "email",
            "STRICT_VALIDATION": "false",
            "AUTO_SUGGEST": "true",
            "BUFFER_MINUTES": "15",
            "AUTO_SUMMARIZE": "true",
            "URGENCY_DETECTION": "true",
            "CALENDAR_PROVIDER": "google",
            "TIMEZONE": "UTC",
            "CRM_ENABLED": "false",
            "CRM_PROVIDER": "custom",
            "AUTO_CREATE_CRM": "false",
            "WEBHOOK_URL": "https://your-webhook-url.com/agent-events",
            "WEBHOOK_EVENTS": "call_start,call_end,booking,note",
            "WEBHOOK_AUTH": "Bearer your-token",
            "TELEPHONY_PROVIDER": "livekit",
            "RECORDING_ENABLED": "false",
            "TRANSCRIPTION_ENABLED": "true",
            "EMAIL_PROVIDER": "smtp",
            "AUTO_EMAIL": "false",
        }

        for key, default_value in defaults.items():
            if key not in variables:
                variables[key] = default_value

    def _extract_template_variables(self) -> Dict[str, str]:
        """Extract template variables from existing config"""
        variables = {}

        def extract_from_dict(d, prefix=""):
            for key, value in d.items():
                if isinstance(value, dict):
                    extract_from_dict(value, f"{prefix}{key.upper()}_")
                elif isinstance(value, str) and "{{" in value:
                    # Extract variable names from template strings
                    matches = re.findall(r'\{\{(\w+)\}\}', value)
                    for match in matches:
                        if match not in variables:
                            variables[match] = f"PLACEHOLDER_{match}"
                else:
                    var_name = f"{prefix}{key.upper()}"
                    variables[var_name] = str(value)

        extract_from_dict(self.config)
        return variables

    def substitute_template_variables(self, content: str, variables: Dict[str, str]) -> str:
        """Replace template variables in content"""
        for var, value in variables.items():
            placeholder = f"{{{{{var}}}}}"
            content = content.replace(placeholder, str(value))

        return content

    def generate_project_files(self, variables: Dict[str, str]):
        """Generate all project files from templates"""
        print("\n🚀 Generating Agent Files...")

        # Create output directory structure
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Copy and process template files
        self._copy_template_structure()
        self._generate_configuration_file(variables)
        self._generate_main_agent_file(variables)
        self._generate_project_files_from_template(variables)
        self._generate_documentation(variables)

        print(f"\n✅ Agent generated successfully in: {self.output_dir}")
        print("\n📁 Generated files:")
        self._list_generated_files()

    def _copy_template_structure(self):
        """Copy template directory structure"""
        for item in self.template_dir.rglob('*'):
            if item.is_file() and not item.name.endswith('.pyc'):
                relative_path = item.relative_to(self.template_dir)
                target_path = self.output_dir / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target_path)

    def _generate_configuration_file(self, variables: Dict[str, str]):
        """Generate the actual configuration file"""
        config_template_path = self.template_dir / "config" / "agent.creation.md"
        config_output_path = self.output_dir / "config" / f"{variables.get('AGENT_NAME', 'agent')}.md"

        with open(config_template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        # Substitute variables
        final_content = self.substitute_template_variables(template_content, variables)

        # Write final config
        with open(config_output_path, 'w', encoding='utf-8') as f:
            f.write(final_content)

    def _generate_main_agent_file(self, variables: Dict[str, str]):
        """Generate the main agent.py file"""
        agent_content = f'''#!/usr/bin/env python3
"""
{variables.get("AGENT_NAME", "Agent")} - Generated from LiveKit Agent Template
{variables.get("AGENT_DESCRIPTION", "AI Voice Agent for " + variables.get("USE_CASE", "general support"))}

Generated on: {__import__("datetime").datetime.now().isoformat()}
Configuration: {variables.get("WORKFLOW_TYPE", "single_agent")} workflow
"""

import asyncio
import logging
from livekit.agents import JobContext, WorkerOptions, cli

# Import our workflow orchestrator
from src.workflows.main_workflow import entrypoint

logger = logging.getLogger("{variables.get('AGENT_NAME', 'agent').lower()}")

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("Starting {variables.get('AGENT_NAME', 'Agent')} - {variables.get('WORKFLOW_TYPE', 'single_agent')} workflow")

    # Run the agent
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
'''

        agent_path = self.output_dir / "agent.py"
        with open(agent_path, 'w', encoding='utf-8') as f:
            f.write(agent_content)

    def _generate_project_files_from_template(self, variables: Dict[str, str]):
        """Process template files and substitute variables"""
        python_files = list(self.output_dir.rglob('*.py'))

        for py_file in python_files:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Substitute variables
            processed_content = self.substitute_template_variables(content, variables)

            with open(py_file, 'w', encoding='utf-8') as f:
                f.write(processed_content)

        # Generate environment file
        self._generate_env_file(variables)

        # Generate pyproject.toml
        self._generate_pyproject_file(variables)

    def _generate_env_file(self, variables: Dict[str, str]):
        """Generate .env.example file with only essential deployment keys"""
        env_content = '''# LiveKit Configuration
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key

# Note: All agent configuration is loaded from config/{agent_name}.md
# Only essential deployment keys should be in this .env file
'''

        env_path = self.output_dir / ".env.example"
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(env_content)

    def _generate_pyproject_file(self, variables: Dict[str, str]):
        """Generate pyproject.toml file"""
        agent_name = variables.get("AGENT_NAME", "agent").lower().replace(" ", "-")

        pyproject_content = f'''[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{agent_name}-voice-agent"
version = "0.1.0"
description = "{variables.get('AGENT_DESCRIPTION', 'AI Voice Agent')}"
requires-python = ">=3.9"
dependencies = [
    "livekit-agents[openai]~=1.2",
    "python-dotenv~=1.0",
    "pyyaml~=6.0",
    "aiohttp~=3.9",
]

[project.optional-dependencies]
dev = [
    "black",
    "isort",
    "flake8",
    "pytest",
    "pytest-asyncio"
]
'''

        pyproject_path = self.output_dir / "pyproject.toml"
        with open(pyproject_path, 'w', encoding='utf-8') as f:
            f.write(pyproject_content)

    def _generate_documentation(self, variables: Dict[str, str]):
        """Generate README and documentation"""
        readme_content = f'''# {variables.get("AGENT_NAME", "Agent")} - AI Voice Agent

{variables.get("AGENT_DESCRIPTION", "An AI voice agent built with LiveKit")}

## Configuration

- **Workflow Type**: {variables.get("WORKFLOW_TYPE", "single_agent")}
- **Language**: {variables.get("LANGUAGE", "English")}
- **Voice**: {variables.get("VOICE", "cedar")}
- **Business Type**: {variables.get("BUSINESS_TYPE", "consulting")}
- **Use Case**: {variables.get("USE_CASE", "customer_support")}

## Features

{"- ✅ Multi-agent workflows" if variables.get("USE_WORKFLOWS") == "true" else "- ❌ Multi-agent workflows"}
{"- ✅ Structured tasks" if variables.get("USE_TASKS") == "true" else "- ❌ Structured tasks"}
{"- ✅ State management" if variables.get("STATE_MANAGEMENT") == "true" else "- ❌ State management"}
{"- ✅ Webhook integration" if variables.get("WEBHOOK_ENABLED") == "true" else "- ❌ Webhook integration"}
{"- ✅ Calendar integration" if variables.get("CALENDAR_ENABLED") == "true" else "- ❌ Calendar integration"}

## Quick Start

1. Copy `.env.example` to `.env` and configure your API keys
2. Install dependencies: `pip install -e .`
3. Run the agent: `python agent.py dev`

## Deployment

Run in production: `python agent.py start`

Generated with LiveKit Agent Template System
'''

        readme_path = self.output_dir / "README.md"
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)

    def _list_generated_files(self):
        """List all generated files"""
        for item in sorted(self.output_dir.rglob('*')):
            if item.is_file():
                relative_path = item.relative_to(self.output_dir)
                print(f"  📄 {relative_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate LiveKit voice agent from template")
    parser.add_argument("--template-dir", default=".", help="Template directory path")
    parser.add_argument("--output-dir", required=True, help="Output directory for generated agent")
    parser.add_argument("--config", help="Path to agent.creation.md file")
    parser.add_argument("--non-interactive", action="store_true", help="Use template variables without prompting")

    args = parser.parse_args()

    generator = AgentGenerator(args.template_dir, args.output_dir)

    # Load configuration
    generator.load_configuration(args.config)

    # Collect variables
    variables = generator.collect_variables(interactive=not args.non_interactive)

    # Generate agent
    generator.generate_project_files(variables)

    print(f"""
🎉 Agent Generation Complete!

Next Steps:
1. cd {args.output_dir}
2. cp .env.example .env
3. Edit .env with your API keys
4. pip install -e .
5. python agent.py dev

Your {variables.get('AGENT_NAME', 'agent')} is ready to use!
""")


if __name__ == "__main__":
    main()