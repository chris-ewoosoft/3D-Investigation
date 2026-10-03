"""Legacy re-export of task_coordinator."""
import ai_assistant.legacy.task_coordinator as _impl
from ai_assistant.legacy.task_coordinator import *


def __getattr__(name):
    return getattr(_impl, name)
