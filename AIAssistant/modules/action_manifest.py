"""Legacy re-export of action_manifest."""
import ai_assistant.legacy.action_manifest as _impl
from ai_assistant.legacy.action_manifest import *


def __getattr__(name):
    return getattr(_impl, name)
