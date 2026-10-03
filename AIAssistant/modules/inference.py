"""Legacy re-export of inference."""
import ai_assistant.legacy.inference as _impl
from ai_assistant.legacy.inference import *


def __getattr__(name):
    return getattr(_impl, name)
