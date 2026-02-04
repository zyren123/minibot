#!/usr/bin/env python3
"""Security check hook - blocks dangerous commands."""

import json
import os
import sys

DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "sudo rm",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",
    "chmod -R 777 /",
    "wget | sh",
    "curl | sh",
]


def main():
    tool_name = os.environ.get("HOOK_TOOL_NAME", "")
    tool_args = os.environ.get("HOOK_TOOL_ARGS", "{}")

    if tool_name != "bash":
        # Only check bash commands
        print(json.dumps({"success": True}))
        return

    try:
        args = json.loads(tool_args)
        command = args.get("command", "")
    except json.JSONDecodeError:
        command = ""

    for pattern in DANGEROUS_PATTERNS:
        if pattern in command:
            print(json.dumps({
                "success": True,
                "blocked": True,
                "block_reason": f"Dangerous command pattern detected: {pattern}",
            }))
            return

    print(json.dumps({"success": True}))


if __name__ == "__main__":
    main()
