"""Legacy re-export of coding_agent."""
import ai_assistant.legacy.coding_agent as _impl
from ai_assistant.legacy.coding_agent import *


def __getattr__(name):
    return getattr(_impl, name)
