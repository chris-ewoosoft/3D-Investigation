"""Legacy re-export of rag_module."""
import ai_assistant.legacy.rag_module as _impl
from ai_assistant.legacy.rag_module import *


def __getattr__(name):
    return getattr(_impl, name)
