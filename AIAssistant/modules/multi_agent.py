"""Legacy re-export of multi_agent."""
import ai_assistant.legacy.multi_agent as _impl
from ai_assistant.legacy.multi_agent import *


def __getattr__(name):
    return getattr(_impl, name)
