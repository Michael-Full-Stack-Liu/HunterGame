import os
import sys
import asyncio
import signal

# Allow both `python3 harness_engine/main.py` and `python3 -m harness_engine.main`.
if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from harness_engine.core.logger import logger
from harness_engine.core.agent import init_job_hunter
from harness_engine.core.scheduler import create_scheduler
from harness_engine.core.runtime_db import ensure_runtime_db
from harness_engine.channels.telegram import create_telegram_channel

async def main():
    """Main entry for the Job Hunter v2 (DeepAgents powered)."""
    logger.info("Initializing Job Hunter Engine v2 (DeepAgents + SQLite)...")
    
    # 1. Start the Live Dashboard
    logger.start_dashboard()
    
    try:
        await ensure_runtime_db()
        # 2. Init persistent agent
        agent = await init_job_hunter()
        scheduler = create_scheduler(agent)
        
        # 3. Create Telegram Bot
        tg_channel = create_telegram_channel(agent=agent, scheduler=scheduler)
        if not tg_channel:
            logger.error("Failed to initialize Telegram.")
            sys.exit(1)
        
        # 4. Concurrent execution
        await asyncio.gather(
            tg_channel.run_polling(),
            scheduler.start()
        )
    except Exception as e:
        logger.error(f"Fatal execution error: {e}")
        raise e
    finally:
        # 6. Safety stop
        logger.stop_dashboard()
        logger.info("Harness Engine shutdown.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warn("Manual interrupt.")
    except Exception as e:
        logger.error(f"Engine failure: {e}")
        sys.exit(1)
