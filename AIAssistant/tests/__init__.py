"""AIAssistant Test Suite."""
import os
import sys

# Ensure AIAssistant root and src are on sys.path for test discovery
_AI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_AI_ROOT, "src")

for _p in (_AI_ROOT, _SRC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
