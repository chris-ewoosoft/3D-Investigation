"""Legacy re-export of a2a_protocol."""
import ai_assistant.legacy.a2a_protocol as _impl
from ai_assistant.legacy.a2a_protocol import *


def __getattr__(name):
    return getattr(_impl, name)
