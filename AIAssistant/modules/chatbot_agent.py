"""Legacy re-export of chatbot_agent."""
import ai_assistant.legacy.chatbot_agent as _impl
from ai_assistant.legacy.chatbot_agent import *


def __getattr__(name):
    return getattr(_impl, name)
