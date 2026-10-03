"""Legacy re-export of checkpointing."""
import ai_assistant.legacy.checkpointing as _impl
from ai_assistant.legacy.checkpointing import *


def __getattr__(name):
    return getattr(_impl, name)
