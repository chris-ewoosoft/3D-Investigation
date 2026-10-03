"""Legacy re-export of llm_module."""
import ai_assistant.legacy.llm_module as _impl
from ai_assistant.legacy.llm_module import *


def __getattr__(name):
    return getattr(_impl, name)
