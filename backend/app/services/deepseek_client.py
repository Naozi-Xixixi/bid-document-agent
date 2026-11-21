from __future__ import annotations

"""Legacy compatibility layer.

Older modules may still import this module name.  The implementation is now
fully delegated to the Qwen client so no runtime path calls any other vendor.
"""

from app.services.qwen_client import LLMResult, Message, chat_completion, stream_chat_completion

__all__ = ["LLMResult", "Message", "chat_completion", "stream_chat_completion"]
