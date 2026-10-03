"""Legacy re-export of sandbox."""
import ai_assistant.legacy.sandbox as _impl
from ai_assistant.legacy.sandbox import *


def __getattr__(name):
    return getattr(_impl, name)
