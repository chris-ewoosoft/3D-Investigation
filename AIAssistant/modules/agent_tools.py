"""Legacy re-export of agent_tools."""
import ai_assistant.legacy.agent_tools as _impl
from ai_assistant.legacy.agent_tools import *


def __getattr__(name):
    return getattr(_impl, name)
