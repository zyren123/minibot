#!/usr/bin/env python3
"""Restrict file access to working directory only."""

import json
import os
import re
import shlex
from pathlib import Path


def extract_paths_from_bash(command: str) -> list[str]:
    """Extract potential file paths from a bash command."""
    paths = []

    # Try to tokenize the command
    try:
        tokens = shlex.split(command)
    except ValueError:
        # If shlex fails, fall back to simple split
        tokens = command.split()

    for token in tokens:
        # Skip flags
        if token.startswith("-"):
            continue
        # Absolute paths
        if token.startswith("/"):
            paths.append(token)
        # Relative paths with parent directory traversal
        elif ".." in token:
            paths.append(token)
        # Home directory expansion
        elif token.startswith("~"):
            paths.append(os.path.expanduser(token))

    # Also check for redirections: > /path, >> /path, < /path
    redirect_pattern = r'[<>]{1,2}\s*([/~][^\s;|&]*|[^\s;|&]*\.\.+[^\s;|&]*)'
    for match in re.finditer(redirect_pattern, command):
        path = match.group(1).strip()
        if path.startswith("~"):
            path = os.path.expanduser(path)
        paths.append(path)

    return paths


def is_path_within_workdir(file_path: str, workdir: str) -> tuple[bool, str]:
    """Check if a path is within the working directory."""
    workdir_path = Path(workdir).resolve()

    # Handle relative paths
    if not Path(file_path).is_absolute():
        target_path = (workdir_path / file_path).resolve()
    else:
        target_path = Path(file_path).resolve()

    try:
        target_path.relative_to(workdir_path)
        return True, ""
    except ValueError:
        return False, str(target_path)


def main():
    tool_name = os.environ.get("HOOK_TOOL_NAME", "")
    tool_args_raw = os.environ.get("HOOK_TOOL_ARGS", "{}")
    workdir = os.environ.get("HOOK_WORKDIR", "")

    # Tools that need file access checking
    file_tools = {"read_file", "write_file", "edit_file", "bash"}
    if tool_name not in file_tools:
        print(json.dumps({"success": True}))
        return

    try:
        args = json.loads(tool_args_raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": True}))
        return

    paths_to_check = []

    # Determine the file path(s) to check
    if tool_name in ("read_file", "write_file", "edit_file"):
        file_path = args.get("path") or args.get("file_path", "")
        if file_path:
            paths_to_check.append(file_path)
    elif tool_name == "bash":
        command = args.get("command", "")
        if command:
            paths_to_check = extract_paths_from_bash(command)

    if not paths_to_check:
        print(json.dumps({"success": True}))
        return

    # Check all paths
    for path in paths_to_check:
        is_valid, resolved_path = is_path_within_workdir(path, workdir)
        if not is_valid:
            print(json.dumps({
                "success": True,
                "blocked": True,
                "block_reason": f"Access to files outside working directory is not allowed: {path} (resolves to {resolved_path})",
            }))
            return

    print(json.dumps({"success": True}))


if __name__ == "__main__":
    main()
