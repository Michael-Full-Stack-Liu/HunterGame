import asyncio
import aiosqlite
import json
import sys
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

async def inspect_db(db_path: str = "data/harness.db", thread_id: str = None):
    console = Console()
    conn = await aiosqlite.connect(db_path)

    async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") as cursor:
        tables = [row[0] for row in await cursor.fetchall()]

    console.print(Panel("🩺 Job Hunter Database Diagnostics", style="bold cyan"))
    console.print(f"Tables: {', '.join(tables) if tables else 'None'}")

    if "applications" in tables:
        async with conn.execute("SELECT status, COUNT(*) FROM applications GROUP BY status ORDER BY status") as cursor:
            rows = await cursor.fetchall()
        app_table = Table(title="Applications Status Snapshot")
        app_table.add_column("Status", style="yellow")
        app_table.add_column("Count", justify="right")
        for status, count in rows:
            app_table.add_row(status or "NULL", str(count))
        console.print(app_table)

    if "checkpoints" in tables:
        if not thread_id:
            console.print(Panel("📋 Active Threads in Job Hunter", style="bold cyan"))
            async with conn.execute("SELECT DISTINCT thread_id FROM checkpoints") as cursor:
                threads = await cursor.fetchall()
                if not threads:
                    console.print("No threads found.")
                else:
                    table = Table()
                    table.add_column("Thread ID", style="yellow")
                    for t in threads:
                        table.add_row(t[0])
                    console.print(table)
                    console.print("\nUsage: python3 job_hunter_doctor.py <thread_id>")
        else:
            console.print(Panel(f"🧵 Thread Diagnostics: {thread_id}", style="bold yellow"))
            query = "SELECT checkpoint FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1"
            async with conn.execute(query, (thread_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    console.print(f"Thread '{thread_id}' not found.")
                else:
                    state = json.loads(row[0])
                    channel_values = state.get("v", {}).get("channel_values", {})

                    todos = channel_values.get("todos", [])
                    todo_table = Table(title="Mission Plan (Todos)")
                    todo_table.add_column("Status")
                    todo_table.add_column("Task")
                    for t in todos:
                        icon = "✅" if t.get("status") == "completed" else "⏳"
                        todo_table.add_row(icon, t.get("task", "Unknown"))
                    console.print(todo_table)

                    messages = channel_values.get("messages", [])
                    if messages:
                        last_msg = messages[-1]
                        msg_content = last_msg.get("content", "No content") if isinstance(last_msg, dict) else str(last_msg)
                        console.print(Panel(msg_content, title="Last Agent Thought", border_style="green"))
    elif thread_id:
        console.print("Checkpoint state is unavailable in this database; only application-level diagnostics can be shown.")

    await conn.close()

if __name__ == "__main__":
    t_id = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(inspect_db(thread_id=t_id))
