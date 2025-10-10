FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY pyproject.toml ./
RUN pip install -e .

# Copy the rest of the application
COPY . .

# Default command
CMD ["python", "agent.py", "start"]