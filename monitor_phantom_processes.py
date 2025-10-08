#!/usr/bin/env python3
"""
Monitor for phantom agent processes
Runs every 10 seconds and logs any agent.py processes found
"""

import subprocess
import time
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('phantom_processes.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def check_for_agents():
    """Check for running agent.py processes"""
    try:
        result = subprocess.run(
            ['wmic', 'process', 'where', "commandline like '%agent.py%'", 'get', 'processid,commandline,creationdate'],
            capture_output=True,
            text=True,
            timeout=5
        )

        lines = [line.strip() for line in result.stdout.split('\n') if 'agent.py' in line and 'wmic' not in line]

        if lines:
            logger.warning(f"⚠️ FOUND {len(lines)} PHANTOM AGENT PROCESSES:")
            for line in lines:
                logger.warning(f"  → {line}")
            return lines
        else:
            logger.info("✅ No phantom processes found")
            return []

    except Exception as e:
        logger.error(f"Error checking processes: {e}")
        return []

def main():
    logger.info("=" * 60)
    logger.info("Starting Phantom Process Monitor")
    logger.info("Checking every 10 seconds...")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)

    try:
        while True:
            agents = check_for_agents()
            time.sleep(10)

    except KeyboardInterrupt:
        logger.info("\n👋 Monitor stopped by user")

if __name__ == "__main__":
    main()
