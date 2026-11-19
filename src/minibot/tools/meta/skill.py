"""Skill tool for loading domain knowledge."""

from typing import Any, TYPE_CHECKING

from ..base import ResultTool
from ..result import ToolOutcome

if TYPE_CHECKING:
    from ...skills.loader import SkillLoader


class SkillTool(ResultTool):
    """Load a skill to gain specialized knowledge for a task."""

    name = "Skill"

    def __init__(self, skill_loader: "SkillLoader"):
        self.skill_loader = skill_loader

    @property
    def description(self) -> str:
        skills_desc = self.skill_loader.get_descriptions()
        return f"""Load a skill to gain specialized knowledge for a task.

Available skills:
{skills_desc}

When to use:
- IMMEDIATELY when user task matches a skill description
- Before attempting domain-specific work (PDF, MCP, etc.)

The skill content will be injected into the conversation, giving you
detailed instructions and access to resources."""

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Name of the skill to load",
                }
            },
            "required": ["skill"],
        }

    async def execute_result(self, skill: str) -> ToolOutcome:
        """Load a skill and inject it into the conversation."""
        content = self.skill_loader.get_skill_content(skill)

        if content is None:
            available = ", ".join(self.skill_loader.list_skills()) or "none"
            return ToolOutcome(f"Error: Unknown skill '{skill}'. Available: {available}", "error")

        return ToolOutcome(f"""<skill-loaded name="{skill}">
{content}
</skill-loaded>

Follow the instructions in the skill above to complete the user's task.""", "success")
