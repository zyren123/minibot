#!/usr/bin/env python3
"""
流式输出技术原理验证和测试脚本
"""

import asyncio
import time
import sys
from typing import AsyncGenerator, Generator
from rich.console import Console
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown


def test_basic_rich_streaming():
    """测试Rich库的基本流式输出能力"""
    console = Console()
    
    print("=== 测试1: Rich基本流式输出 ===")
    
    # 1. 逐字符输出（打字机效果）
    console.print("[bold cyan]1. 打字机效果测试:[/]")
    text = "这是一个测试打字机效果的示例文本，每个字符会依次显示。"
    
    for i, char in enumerate(text):
        console.print(char, end="")
        sys.stdout.flush()
        time.sleep(0.05)
    console.print()
    
    # 2. 使用Live进行实时更新
    console.print("\n[bold cyan]2. Live更新测试:[/]")
    status_lines = ["正在处理...", "步骤1: 分析需求", "步骤2: 设计方案", "步骤3: 实现代码", "完成!"]
    
    with Live(console=console, refresh_per_second=4) as live:
        for line in status_lines:
            live.update(Panel(line, title="进度", border_style="green"))
            time.sleep(1)
    
    # 3. 进度条
    console.print("\n[bold cyan]3. 进度条测试:[/]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("处理中...", total=100)
        for i in range(100):
            progress.update(task, advance=1)
            time.sleep(0.02)


async def test_async_streaming():
    """测试异步流式输出"""
    console = Console()
    
    print("\n=== 测试2: 异步流式输出 ===")
    
    async def stream_generator() -> AsyncGenerator[str, None]:
        """模拟异步数据流"""
        messages = [
            "正在连接服务器...",
            "验证身份...",
            "获取数据...",
            "处理响应...",
            "渲染结果...",
            "完成!"
        ]
        for msg in messages:
            yield msg
            await asyncio.sleep(0.5)
    
    # 使用Rich Live显示异步流
    with Live(console=console, refresh_per_second=2) as live:
        async for message in stream_generator():
            live.update(Panel(message, title="异步处理", border_style="blue"))


def test_markdown_streaming():
    """测试Markdown流式渲染"""
    console = Console()
    
    print("\n=== 测试3: Markdown流式渲染 ===")
    
    markdown_content = """# 流式输出研究报告

## 技术原理

1. **实时更新机制**
   - 使用终端控制序列
   - 光标定位和清除
   - 帧率控制

2. **缓冲策略**
   - 行缓冲 vs 字符缓冲
   - 双缓冲技术
   - 刷新时机控制

## 实现方案

### Rich库方案
- Live组件
- Progress组件  
- Console控制

### Prompt Toolkit方案
- 异步输入处理
- 状态管理
- 事件循环集成
"""
    
    # 分块渲染Markdown
    lines = markdown_content.split('\n')
    current_content = ""
    
    with Live(console=console, refresh_per_second=10) as live:
        for line in lines:
            current_content += line + '\n'
            try:
                md = Markdown(current_content)
                live.update(Panel(md, title="Markdown流式渲染", border_style="yellow"))
            except Exception:
                # 如果Markdown解析失败，显示纯文本
                live.update(Panel(current_content, title="纯文本显示", border_style="yellow"))
            time.sleep(0.2)


def test_prompt_toolkit_integration():
    """测试Prompt Toolkit集成"""
    print("\n=== 测试4: Prompt Toolkit集成测试 ===")
    
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.formatted_text import HTML
        
        print("Prompt Toolkit可用，测试异步输入与流式输出结合...")
        
        async def async_prompt_demo():
            session = PromptSession()
            
            with patch_stdout():
                # 模拟后台处理时的流式输出
                for i in range(5):
                    print(f"后台处理进度: {i+1}/5")
                    await asyncio.sleep(0.5)
                
                # 获取用户输入
                result = await session.prompt_async(HTML('<b>请输入内容:</b> '))
                print(f"用户输入: {result}")
        
        # 注意：实际运行需要事件循环
        print("需要在异步环境中运行此测试")
        
    except ImportError:
        print("Prompt Toolkit未安装或不可用")


def test_buffering_strategies():
    """测试不同的缓冲策略"""
    console = Console()
    
    print("\n=== 测试5: 缓冲策略测试 ===")
    
    # 1. 字符级缓冲
    console.print("[bold]1. 字符级缓冲:[/]")
    text = "字符级缓冲提供最流畅的体验，但可能影响性能。"
    for char in text:
        console.print(char, end="")
        sys.stdout.flush()
        time.sleep(0.03)
    console.print()
    
    # 2. 行级缓冲
    console.print("\n[bold]2. 行级缓冲:[/]")
    lines = [
        "行级缓冲平衡了性能和体验，",
        "适合大多数文本输出场景，",
        "减少了系统调用次数。"
    ]
    for line in lines:
        console.print(line)
        time.sleep(0.5)
    
    # 3. 块级缓冲
    console.print("\n[bold]3. 块级缓冲:[/]")
    blocks = [
        "块级缓冲适合大量数据的批量输出，",
        "例如文件内容、API响应等。",
        "可以显著提高性能。"
    ]
    with Live(console=console, refresh_per_second=1) as live:
        for i, block in enumerate(blocks):
            live.update(Panel(f"块 {i+1}: {block}", title="块级缓冲", border_style="magenta"))
            time.sleep(1)


def test_ux_enhancements():
    """测试用户体验增强功能"""
    console = Console()
    
    print("\n=== 测试6: 用户体验增强 ===")
    
    # 1. 打字机效果（不同速度）
    console.print("[bold]1. 可调速打字机效果:[/]")
    speeds = [("快速", 0.02), ("中速", 0.05), ("慢速", 0.1)]
    
    for speed_name, delay in speeds:
        console.print(f"[cyan]{speed_name}:[/]", end=" ")
        text = "这是一段测试文本。"
        for char in text:
            console.print(char, end="")
            sys.stdout.flush()
            time.sleep(delay)
        console.print()
    
    # 2. 渐变效果
    console.print("\n[bold]2. 颜色渐变效果:[/]")
    colors = ["red", "yellow", "green", "cyan", "blue", "magenta"]
    text = "颜色渐变效果展示"
    
    for i, char in enumerate(text):
        color = colors[i % len(colors)]
        console.print(f"[{color}]{char}[/{color}]", end="")
        sys.stdout.flush()
        time.sleep(0.1)
    console.print()
    
    # 3. 动态进度指示
    console.print("\n[bold]3. 动态进度指示:[/]")
    spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    with Live(console=console, refresh_per_second=10) as live:
        for i in range(30):
            spinner = spinner_chars[i % len(spinner_chars)]
            progress = f"[{spinner}] 处理中... {i+1}/30"
            live.update(Panel(progress, title="动态指示器", border_style="cyan"))
            time.sleep(0.1)


if __name__ == "__main__":
    console = Console()
    
    console.print(Panel("🚀 流式输出技术测试", style="bold blue"))
    console.print("本脚本测试各种流式输出技术和用户体验增强功能\n")
    
    # 运行所有测试
    test_basic_rich_streaming()
    
    # 异步测试需要在事件循环中运行
    try:
        asyncio.run(test_async_streaming())
    except Exception as e:
        print(f"异步测试跳过: {e}")
    
    test_markdown_streaming()
    test_prompt_toolkit_integration()
    test_buffering_strategies()
    test_ux_enhancements()
    
    console.print("\n✅ 所有测试完成!")