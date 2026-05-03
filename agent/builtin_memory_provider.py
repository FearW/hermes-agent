"""Minimal built-in memory provider compatibility module."""

from __future__ import annotations

import json

from agent.memory_provider import MemoryProvider


class BuiltinMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self.session_id = session_id
        self.init_kwargs = dict(kwargs)

    def get_tool_schemas(self):
        return []

    def handle_tool_call(self, tool_name, args, **kwargs):
        return json.dumps({"error": f"Unknown built-in memory tool: {tool_name}"})
