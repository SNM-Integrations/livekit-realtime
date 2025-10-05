# LiveKit Voice Agent Template - Makefile

.PHONY: help install clean deploy logs rooms

help:  ## Show this help message
	@echo "LiveKit Voice Agent Template"
	@echo "============================"
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "⚠️  NEVER run 'python src/agent.py dev' - always use 'make deploy'"

install:  ## Install dependencies
	python -m pip install -U pip
	pip install -r requirements.txt

deploy:  ## Deploy agent to LiveKit Cloud (REQUIRED for testing)
	@echo "🚀 Deploying agent to LiveKit Cloud..."
	lk agent deploy

logs:  ## View agent logs (requires AGENT_ID env var)
	@if [ -z "$$AGENT_ID" ]; then \
		echo "❌ Error: AGENT_ID not set"; \
		echo "Usage: AGENT_ID=CA_xxxxx make logs"; \
		exit 1; \
	fi
	lk agent logs $$AGENT_ID

rooms:  ## List active rooms (should be empty when no calls)
	@echo "Checking for active rooms..."
	@lk room list || echo "No active rooms (good!)"

clean:  ## Clean up generated files and cache
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/

check-billing:  ## Remind to check LiveKit billing dashboard
	@echo "💰 Billing Check:"
	@echo "  1. Go to: https://cloud.livekit.io"
	@echo "  2. Check 'Agent Session Minutes' usage"
	@echo "  3. Verify it matches your actual call time"
	@echo "  4. If excessive: check for stuck rooms with 'make rooms'"

project:  ## List LiveKit projects (to get subdomain for livekit.toml)
	lk project list
