"""Human controls for file checkpoints."""

import asyncio
from collections.abc import Awaitable, Callable
import re

from ..file_checkpoints import FileCheckpointError, GitFileCheckpoints, finish_file_operation
from ..utils.output import print_system


def _terminal_text(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", lambda match: f"\\x{ord(match[0]):02x}", text)


async def handle_checkpoint_cmd(
    args: list[str],
    checkpoints: GitFileCheckpoints | None,
    confirm: Callable[[str], Awaitable[bool]],
) -> None:
    """List, inspect or restore one file checkpoint."""
    if checkpoints is None:
        print_system("File checkpoints are disabled.")
        return
    try:
        if not args or args == ["list"]:
            records = await asyncio.to_thread(checkpoints.list)
            if not records:
                print_system("No file checkpoints.")
            for record in records:
                state = record.status.replace("_", " ")
                print(_terminal_text(f"{record.id}  {record.path}  {record.created_at}  ({state})"))
        elif len(args) == 2 and args[0] == "diff":
            print(_terminal_text(await asyncio.to_thread(checkpoints.diff, args[1])))
        elif len(args) == 2 and args[0] == "rollback":
            if not await confirm("Restore the selected file from this checkpoint?"):
                print_system("Rollback canceled.")
                return
            restored = await finish_file_operation(lambda: checkpoints.rollback(args[1]))
            print_system("File restored." if restored else "Checkpoint already consumed.")
        else:
            print_system("Usage: /checkpoint [list|diff ID|rollback ID]")
    except (FileCheckpointError, OSError) as exc:
        print_system(_terminal_text(str(exc)))
