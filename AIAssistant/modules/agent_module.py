"""Legacy re-export of agent_module."""
import ai_assistant.legacy.agent_module as _impl
from ai_assistant.legacy.agent_module import *


def __getattr__(name):
    return getattr(_impl, name)
