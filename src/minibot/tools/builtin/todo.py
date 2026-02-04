"""Todo management tool."""

from typing import Any

from ..base import BaseTool


class TodoManager:
    """Task list manager with constraints."""

    def __init__(self):
        self.items: list[dict] = []

    def update(self, items: list[dict]) -> str:
        """Update the todo list."""
        validated = []
        in_progress = 0

        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            active = str(item.get("activeForm", "")).strip()

            if not content or not active:
                raise ValueError(f"Item {i}: content and activeForm required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status")
            if status == "in_progress":
                in_progress += 1

            validated.append({
                "content": content,
                "status": status,
                "activeForm": active,
            })

        if in_progress > 1:
            raise ValueError("Only one task can be in_progress")

        self.items = validated[:20]
        return self.render()

    def render(self) -> str:
        """Render the todo list as text."""
        if not self.items:
            return "No todos."

        lines = []
        for t in self.items:
            if t["status"] == "completed":
                mark = "[x]"
            elif t["status"] == "in_progress":
                mark = "[>]"
            else:
                mark = "[ ]"
            lines.append(f"{mark} {t['content']}")

        done = sum(1 for t in self.items if t["status"] == "completed")
        return "\n".join(lines) + f"\n({done}/{len(self.items)} done)"

    def get_active_task(self) -> dict | None:
        """Get the currently active task."""
        for item in self.items:
            if item["status"] == "in_progress":
                return item
        return None


class TodoWriteTool(BaseTool):
    """Update task list."""

    name = "TodoWrite"
    description = "Update task list."

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {"type": "string"},
                        },
                        "required": ["content", "status", "activeForm"],
                    },
                }
            },
            "required": ["items"],
        }

    async def execute(self, items: list[dict]) -> str:
        """Update the todo list."""
        try:
            return self.todo_manager.update(items)
        except Exception as e:
            return f"Error: {e}"
