"""
Audio Components for LiveKit Voice Agents
==========================================

Custom components for optimizing voice agents for SIP telephony.
"""

from .telephony_tts import TelephonyOptimizedTTS, create_telephony_tts

__all__ = ["TelephonyOptimizedTTS", "create_telephony_tts"]
