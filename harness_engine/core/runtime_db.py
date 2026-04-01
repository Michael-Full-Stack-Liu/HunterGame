import aiosqlite


APPLICATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT,
    job_title TEXT,
    contact_name TEXT,
    contact_info TEXT,
    status TEXT,
    last_contact_date DATE,
    notes TEXT
)
"""

COMPANY_PROGRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS company_progress (
    company TEXT PRIMARY KEY,
    status TEXT,
    last_action TEXT,
    next_action TEXT,
    notes TEXT,
    source_thread TEXT,
    is_new_target INTEGER DEFAULT 0,
    last_cycle_started_at TEXT,
    last_updated TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


async def ensure_runtime_db(db_path: str = "data/harness.db") -> None:
    """Ensure runtime tables required by scheduler/auditor exist."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(APPLICATIONS_SCHEMA)
        await conn.execute(COMPANY_PROGRESS_SCHEMA)
        await conn.commit()
