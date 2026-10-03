"""Legacy re-export of agent_logging."""
import ai_assistant.legacy.agent_logging as _impl
from ai_assistant.legacy.agent_logging import *


def __getattr__(name):
    return getattr(_impl, name)
