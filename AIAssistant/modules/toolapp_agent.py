"""Legacy re-export of toolapp_agent."""
import ai_assistant.legacy.toolapp_agent as _impl
from ai_assistant.legacy.toolapp_agent import *


def __getattr__(name):
    return getattr(_impl, name)
