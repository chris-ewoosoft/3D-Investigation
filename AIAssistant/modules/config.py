"""Legacy re-export of config."""
import ai_assistant.legacy.config as _impl
from ai_assistant.legacy.config import *


def __getattr__(name):
    return getattr(_impl, name)
