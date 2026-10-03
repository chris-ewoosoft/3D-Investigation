"""Legacy re-export of mcp_server."""
import ai_assistant.legacy.mcp_server as _impl
from ai_assistant.legacy.mcp_server import *


def __getattr__(name):
    return getattr(_impl, name)
