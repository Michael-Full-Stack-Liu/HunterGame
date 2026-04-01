import logging
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.traceback import install

# Install Rich traceback for beautiful error reporting
install(show_locals=True)

class HarnessLogger:
    """Industrial-grade observability with real-time performance tracking and error diagnostics."""
    
    def __init__(self, trace_file: str = "data/traces.jsonl"):
        # Dynamic daily log file
        log_dir = "data/logs"
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"engine_{datetime.now().strftime('%Y_%m_%d')}.log")
        
        # Standard logging configuration
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.FileHandler(log_file)]
        )
        self.logger = logging.getLogger("harness")
        self.trace_file = trace_file
        trace_dir = os.path.dirname(self.trace_file)
        if trace_dir:
            os.makedirs(trace_dir, exist_ok=True)
        
        # Dashboard state
        self.console = Console(stderr=True)
        self.current_tasks = []
        self.current_node = "Idle"
        self.last_thought = ""
        self.performance_metrics = {} # {tool_name: [count, avg_time, last_time]}
        self.tool_start_times = {}
        self.live = None
        
    def _render_dashboard(self) -> Group:
        # 1. Header with Status
        header = Panel(
            Text(f"🚀 Job Hunter Engine v2 | Node: {self.current_node} | {datetime.now().strftime('%H:%M:%S')}", 
                 justify="center", style="bold cyan"),
            border_style="bright_blue"
        )
        
        # 2. Planner Table (TODOs)
        todo_table = Table(expand=True, border_style="yellow")
        todo_table.add_column("Stat", width=4)
        todo_table.add_column("Mission Task")
        for t in self.current_tasks:
            if isinstance(t, str):
                todo_table.add_row("⚪", t)
                continue
            if not isinstance(t, dict):
                todo_table.add_row("⚪", str(t))
                continue
            status = t.get("status", "pending")
            icon = "✅" if status == "completed" else "⏳" if status == "in_progress" else "⚪"
            task_text = (
                t.get("task")
                or t.get("content")
                or t.get("title")
                or t.get("name")
                or "Unknown"
            )
            todo_table.add_row(icon, str(task_text))
        
        # 3. Performance Metrics Table
        perf_table = Table(expand=True, title="⏱ Performance Metrics", border_style="magenta")
        perf_table.add_column("Tool", style="cyan")
        perf_table.add_column("Calls", justify="right")
        perf_table.add_column("Last (s)", justify="right", style="green")
        perf_table.add_column("Avg (s)", justify="right", style="bold yellow")
        
        for tool, metrics in sorted(self.performance_metrics.items()):
            count, avg, last = metrics
            perf_table.add_row(tool, str(count), f"{last:.2f}", f"{avg:.2f}")
            
        # 4. Main Activity Area
        activity_text = Text(f"Latest Thought Process:\n{self.last_thought[:400]}...", style="italic green")
        if self.current_node == "Error":
             activity_text = Text(f"Critical Failure Detected:\n{self.last_thought}", style="bold red")

        activity_panel = Panel(activity_text, title="🤖 Reasoning & Execution", border_style="green" if self.current_node != "Error" else "red")
        
        # Layout Assembly
        side_panel = Group(Panel(todo_table, title="📋 Plan"), perf_table)
        main_layout = Table.grid(expand=True)
        main_layout.add_column(ratio=1)
        main_layout.add_column(ratio=2)
        main_layout.add_row(side_panel, activity_panel)
        
        footer = Panel(Text("Press Ctrl+C to disconnect | Logs: data/logs/engine_YYYY_MM_DD.log", justify="center", style="dim"))
        
        return Group(header, main_layout, footer)

    def start_dashboard(self):
        if self.live is not None:
            return
        if not sys.stderr.isatty():
            return
        self.live = Live(
            self._render_dashboard(),
            console=self.console,
            refresh_per_second=1,
            screen=False,
            auto_refresh=False,
        )
        self.live.start()

    def stop_dashboard(self):
        if self.live:
            self.live.stop()
            self.live = None

    def update_state(self, node: Optional[str] = None, tasks: Optional[List] = None, thought: Optional[str] = None):
        if node: self.current_node = node
        if tasks is not None: self.current_tasks = tasks
        if thought is not None: self.last_thought = thought
        if self.live:
            self.live.update(self._render_dashboard(), refresh=True)

    def tool_start(self, name: str):
        self.tool_start_times[name] = time.time()
        self.update_state(node=f"Tool:{name}")

    def tool_end(self, name: str, success: bool = True):
        start = self.tool_start_times.pop(name, time.time())
        duration = time.time() - start
        
        # Update metrics
        if name not in self.performance_metrics:
            self.performance_metrics[name] = [1, duration, duration] # count, avg, last
        else:
            count, avg, last = self.performance_metrics[name]
            new_avg = (avg * count + duration) / (count + 1)
            self.performance_metrics[name] = [count + 1, new_avg, duration]
        
        self.update_state(node="Agent")
        self.trace({"event": "tool_complete", "tool": name, "duration": duration, "success": success})

    def info(self, msg: str):
        self.logger.info(msg)
        self.update_state(thought=msg)

    def error(self, msg: str):
        self.logger.error(msg)
        self.update_state(node="Error", thought=msg)

    def warn(self, msg: str):
        self.logger.warning(msg)
        self.update_state(thought=msg)

    def tool_call(self, name: str, args: Dict):
        self.logger.info(f"Tool Call: {name}({args})")
        self.trace({"event": "tool_call", "tool": name, "args": args})

    def trace(self, event: Dict):
        event["timestamp"] = datetime.now().isoformat()
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

def tool_monitor(func):
    """Decorator to automatically log and time tool execution."""
    from functools import wraps
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.tool_start(tool_name)
            try:
                result = await func(*args, **kwargs)
                logger.tool_end(tool_name, success=True)
                return result
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                logger.tool_end(tool_name, success=False)
                raise e
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.tool_start(tool_name)
            try:
                result = func(*args, **kwargs)
                logger.tool_end(tool_name, success=True)
                return result
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                logger.tool_end(tool_name, success=False)
                raise e
        return sync_wrapper

# Singleton
logger = HarnessLogger()
