"""Legacy re-export of data_governance."""
import ai_assistant.legacy.data_governance as _impl
from ai_assistant.legacy.data_governance import *


def __getattr__(name):
    return getattr(_impl, name)
