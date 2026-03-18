"""Tool plugin loading for the Minibot server.

Security note: this executes local Python code. This is intended for the
default "localhost single user" mode.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


def minibot_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    setattr(func, "__minibot_tool__", True)
    return func


def _load_module_from_path(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"minibot_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load plugin module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tools_from_plugin(path: str | Path) -> list[Callable[..., Any]]:
    fp = Path(path).expanduser().resolve()
    if not fp.exists():
        raise FileNotFoundError(str(fp))
    if fp.suffix != ".py":
        raise ValueError("Plugin path must be a .py file")

    module = _load_module_from_path(fp)

    tools: list[Callable[..., Any]] = []

    if hasattr(module, "TOOLS"):
        raw = getattr(module, "TOOLS")
        if not isinstance(raw, list):
            raise TypeError("TOOLS must be a list")
        for item in raw:
            if callable(item):
                tools.append(item)
        return tools

    if hasattr(module, "get_tools"):
        getter = getattr(module, "get_tools")
        if callable(getter):
            raw = getter()
            if isinstance(raw, list):
                tools.extend([t for t in raw if callable(t)])
            return tools

    for value in module.__dict__.values():
        if callable(value) and getattr(value, "__minibot_tool__", False):
            tools.append(value)

    return tools

