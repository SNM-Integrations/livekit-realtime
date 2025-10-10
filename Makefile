# LiveKit Agent Template Makefile

.PHONY: help install dev test create-agent demo clean

help:  ## Show this help message
	@echo "LiveKit Agent Template System"
	@echo "=============================="
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install template system dependencies
	python -m pip install -U pip
	pip install -e .

dev:  ## Install development dependencies
	pip install -e .[dev]

test:  ## Run tests (when implemented)
	python -m pytest tests/ -v

create-agent:  ## Create a new agent (interactive)
	@echo "🤖 Creating new agent..."
	@read -p "Output directory name: " OUTPUT_DIR; \
	python scripts/create_agent.py --output-dir "$$OUTPUT_DIR"

demo:  ## Create a demo agent quickly
	python scripts/create_agent.py \
		--output-dir "./demo-agent" \
		--non-interactive

clean:  ## Clean up generated files and cache
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/ dist/

# Advanced usage examples
samuel-agent:  ## Create Samuel-style missed call agent
	@echo "Creating Samuel-style agent..."
	@mkdir -p examples/samuel-agent
	@cp config/agent.creation.md examples/samuel-agent/config.md
	@echo "Edit examples/samuel-agent/config.md and run:"
	@echo "python scripts/create_agent.py --config examples/samuel-agent/config.md --output-dir examples/samuel-agent"

customer-service:  ## Create customer service agent template
	@echo "Creating customer service agent..."
	@python scripts/create_agent.py \
		--output-dir "./customer-service-agent" \
		--template-dir "."

healthcare-agent:  ## Create healthcare triage agent
	@echo "Creating healthcare agent..."
	@python scripts/create_agent.py \
		--output-dir "./healthcare-agent" \
		--template-dir "."
