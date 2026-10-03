"""Legacy re-export of tool_contract."""
import ai_assistant.legacy.tool_contract as _impl
from ai_assistant.legacy.tool_contract import *


def __getattr__(name):
    return getattr(_impl, name)
