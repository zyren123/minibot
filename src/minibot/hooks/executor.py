"""Hook executor for running hook handlers."""

import asyncio
import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .events import HookContext, HookResult


class HookExecutor:
    """Executes hook handlers."""

    def __init__(self, workdir: Path, default_timeout: int = 30):
        self.workdir = workdir
        self.default_timeout = default_timeout

    async def execute(
        self,
        handler: str,
        context: HookContext,
        timeout: int | None = None,
    ) -> HookResult:
        """Execute a hook handler."""
        timeout = timeout or self.default_timeout

        try:
            if ":" in handler and not handler.endswith(".py"):
                # Python function reference: module:function
                return await self._execute_function(handler, context, timeout)
            elif handler.endswith(".py"):
                # Python script
                return await self._execute_python_script(handler, context, timeout)
            elif handler.endswith(".sh"):
                # Shell script
                return await self._execute_shell_script(handler, context, timeout)
            else:
                return HookResult(
                    success=False,
                    error=f"Unknown handler type: {handler}",
                )
        except asyncio.TimeoutError:
            return HookResult(
                success=False,
                error=f"Hook timed out after {timeout}s",
            )
        except Exception as e:
            return HookResult(
                success=False,
                error=str(e),
            )

    async def _execute_function(
        self,
        handler: str,
        context: HookContext,
        timeout: int,
    ) -> HookResult:
        """Execute a Python function handler."""
        module_name, func_name = handler.rsplit(":", 1)

        try:
            module = importlib.import_module(module_name)
            func: Callable = getattr(module, func_name)
        except (ImportError, AttributeError) as e:
            return HookResult(success=False, error=f"Failed to load handler: {e}")

        # Run the function
        loop = asyncio.get_event_loop()
        if asyncio.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(context), timeout=timeout)
        else:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, func, context),
                timeout=timeout,
            )

        return self._parse_function_result(result)

    async def _execute_python_script(
        self,
        handler: str,
        context: HookContext,
        timeout: int,
    ) -> HookResult:
        """Execute a Python script handler."""
        script_path = self._resolve_path(handler)
        if not script_path.exists():
            return HookResult(success=False, error=f"Script not found: {handler}")

        # Prepare environment with context
        env = self._prepare_env(context)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            cwd=self.workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise

        if proc.returncode != 0:
            return HookResult(
                success=False,
                error=stderr.decode().strip() or f"Exit code: {proc.returncode}",
            )

        return self._parse_script_output(stdout.decode())

    async def _execute_shell_script(
        self,
        handler: str,
        context: HookContext,
        timeout: int,
    ) -> HookResult:
        """Execute a shell script handler."""
        script_path = self._resolve_path(handler)
        if not script_path.exists():
            return HookResult(success=False, error=f"Script not found: {handler}")

        env = self._prepare_env(context)

        proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            str(script_path),
            cwd=self.workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise

        if proc.returncode != 0:
            return HookResult(
                success=False,
                error=stderr.decode().strip() or f"Exit code: {proc.returncode}",
            )

        return self._parse_script_output(stdout.decode())

    def _resolve_path(self, handler: str) -> Path:
        """Resolve handler path relative to workdir."""
        path = Path(handler)
        if not path.is_absolute():
            path = self.workdir / path
        return path

    def _prepare_env(self, context: HookContext) -> dict[str, str]:
        """Prepare environment variables for script execution."""
        import os
        import json

        env = dict(os.environ)
        env["HOOK_EVENT"] = context.event.value
        env["HOOK_WORKDIR"] = context.workdir
        env["HOOK_CONTEXT"] = json.dumps(context.metadata)

        # Add specific context fields
        if hasattr(context, "tool_name"):
            env["HOOK_TOOL_NAME"] = context.tool_name
        if hasattr(context, "tool_args"):
            env["HOOK_TOOL_ARGS"] = json.dumps(context.tool_args)
        if hasattr(context, "skill_name"):
            env["HOOK_SKILL_NAME"] = context.skill_name

        return env

    def _parse_function_result(self, result: Any) -> HookResult:
        """Parse result from a function handler."""
        if result is None:
            return HookResult(success=True)
        if isinstance(result, HookResult):
            return result
        if isinstance(result, dict):
            return HookResult(
                success=result.get("success", True),
                output=result.get("output"),
                error=result.get("error"),
                blocked=result.get("blocked", False),
                block_reason=result.get("block_reason"),
                modified_context=result.get("modified_context"),
            )
        if isinstance(result, str):
            return HookResult(success=True, output=result)
        return HookResult(success=True, output=str(result))

    def _parse_script_output(self, output: str) -> HookResult:
        """Parse output from a script handler."""
        import json

        output = output.strip()

        # Try to parse as JSON
        if output.startswith("{"):
            try:
                data = json.loads(output)
                return HookResult(
                    success=data.get("success", True),
                    output=data.get("output"),
                    error=data.get("error"),
                    blocked=data.get("blocked", False),
                    block_reason=data.get("block_reason"),
                    modified_context=data.get("modified_context"),
                )
            except json.JSONDecodeError:
                pass

        # Check for special prefixes
        if output.startswith("BLOCKED:"):
            return HookResult(
                success=True,
                blocked=True,
                block_reason=output[8:].strip(),
            )

        return HookResult(success=True, output=output if output else None)
