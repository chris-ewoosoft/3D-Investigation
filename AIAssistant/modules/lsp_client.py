"""Legacy re-export of lsp_client."""
import ai_assistant.legacy.lsp_client as _impl
from ai_assistant.legacy.lsp_client import *


def __getattr__(name):
    return getattr(_impl, name)
