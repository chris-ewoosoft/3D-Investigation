"""Legacy re-export of approval_manager."""
import ai_assistant.legacy.approval_manager as _impl
from ai_assistant.legacy.approval_manager import *


def __getattr__(name):
    return getattr(_impl, name)
