#!/usr/bin/env python3
"""Log tool calls hook - logs all tool executions to a file."""

import json
import os
from datetime import datetime
from pathlib import Path


def main():
    workdir = os.environ.get("HOOK_WORKDIR", ".")
    event = os.environ.get("HOOK_EVENT", "")
    tool_name = os.environ.get("HOOK_TOOL_NAME", "")
    tool_args = os.environ.get("HOOK_TOOL_ARGS", "{}")

    log_file = Path(workdir) / ".minibot" / "tool_calls.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().isoformat()

    try:
        args = json.loads(tool_args)
        # Truncate long values
        for key, value in args.items():
            if isinstance(value, str) and len(value) > 200:
                args[key] = value[:200] + "..."
    except json.JSONDecodeError:
        args = {"raw": tool_args}

    log_entry = {
        "timestamp": timestamp,
        "event": event,
        "tool": tool_name,
        "args": args,
    }

    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    print(json.dumps({"success": True}))


if __name__ == "__main__":
    main()
