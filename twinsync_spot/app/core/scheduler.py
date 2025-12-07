"""Background scheduler for TwinSync Spot.

Uses APScheduler to run scheduled spot checks.
"""
import json
import logging
from datetime import datetime
from typing import Optional

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    AsyncIOScheduler = None
    CronTrigger = None

logger = logging.getLogger(__name__)

# Day name to cron day mapping
DAY_TO_CRON = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


class SpotScheduler:
    """Manages scheduled spot checks using APScheduler."""
    
    def __init__(self):
        if not APSCHEDULER_AVAILABLE:
            logger.warning("APScheduler not installed. Scheduled checks will not run.")
            self.scheduler = None
        else:
            self.scheduler = AsyncIOScheduler()
        self.db = None
        self._check_callback = None
    
    async def start(self, db, check_callback=None):
        """Start the scheduler and load all spot schedules.
        
        Args:
            db: Database instance
            check_callback: Async function to call for checking a spot (spot_id) -> CheckResult
        """
        if not self.scheduler:
            logger.warning("Scheduler not available - skipping start")
            return
        
        self.db = db
        self._check_callback = check_callback
        
        # Load all spots with schedules
        spots = await db.get_all_spots()
        
        for spot in spots:
            if spot.check_schedule:
                try:
                    schedule = json.loads(spot.check_schedule)
                    if schedule:
                        await self._add_jobs_for_spot(spot.id, schedule)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error(f"Invalid schedule for spot {spot.id}: {e}")
        
        self.scheduler.start()
        logger.info("Scheduler started")
    
    async def stop(self):
        """Stop the scheduler."""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
    
    async def update_spot_schedule(self, spot_id: int, schedule: list):
        """Update the schedule for a spot.
        
        Args:
            spot_id: The spot ID
            schedule: List of schedule items [{"time": "09:00", "days": ["mon", "tue"]}]
        """
        if not self.scheduler:
            return
        
        # Remove existing jobs for this spot
        await self._remove_jobs_for_spot(spot_id)
        
        # Add new jobs if schedule is not empty
        if schedule:
            await self._add_jobs_for_spot(spot_id, schedule)
    
    async def clear_spot_schedule(self, spot_id: int):
        """Remove all scheduled jobs for a spot.
        
        Args:
            spot_id: The spot ID
        """
        if not self.scheduler:
            return
        
        await self._remove_jobs_for_spot(spot_id)
    
    async def _add_jobs_for_spot(self, spot_id: int, schedule: list):
        """Add scheduler jobs for a spot's schedule.
        
        Args:
            spot_id: The spot ID
            schedule: List of schedule items
        """
        if not self.scheduler:
            return
        
        for idx, item in enumerate(schedule):
            time_str = item.get("time", "09:00")
            days = item.get("days", [])
            
            if not days:
                continue
            
            # Parse time
            try:
                parts = time_str.split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                logger.error(f"Invalid time format '{time_str}' for spot {spot_id}")
                continue
            
            # Convert day names to cron format - only use valid days
            valid_days = [str(DAY_TO_CRON[d.lower()]) for d in days if d.lower() in DAY_TO_CRON]
            
            # Log warning for invalid days
            invalid_days = [d for d in days if d.lower() not in DAY_TO_CRON]
            if invalid_days:
                logger.warning(f"Invalid day names for spot {spot_id}: {invalid_days}")
            
            if not valid_days:
                logger.warning(f"No valid days for spot {spot_id} schedule item {idx}")
                continue
            
            day_of_week = ",".join(valid_days)
            
            # Create job ID
            job_id = f"spot_{spot_id}_schedule_{idx}"
            
            # Add the job
            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                day_of_week=day_of_week
            )
            
            self.scheduler.add_job(
                self._run_spot_check,
                trigger,
                id=job_id,
                args=[spot_id],
                replace_existing=True,
                misfire_grace_time=300,  # 5 minutes
            )
            
            logger.info(f"Added schedule job {job_id}: {time_str} on days {days}")
    
    async def _remove_jobs_for_spot(self, spot_id: int):
        """Remove all jobs for a spot.
        
        Args:
            spot_id: The spot ID
        """
        if not self.scheduler:
            return
        
        prefix = f"spot_{spot_id}_schedule_"
        jobs_to_remove = [job.id for job in self.scheduler.get_jobs() if job.id.startswith(prefix)]
        
        for job_id in jobs_to_remove:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed schedule job {job_id}")
    
    async def _run_spot_check(self, spot_id: int):
        """Run a scheduled check for a spot.
        
        Args:
            spot_id: The spot ID to check
        """
        logger.info(f"Running scheduled check for spot {spot_id}")
        
        if self._check_callback:
            try:
                await self._check_callback(spot_id)
                logger.info(f"Scheduled check completed for spot {spot_id}")
            except Exception as e:
                logger.error(f"Scheduled check failed for spot {spot_id}: {e}")
        else:
            logger.warning(f"No check callback configured for spot {spot_id}")
    
    def get_next_run_time(self, spot_id: int) -> Optional[datetime]:
        """Get the next scheduled run time for a spot.
        
        Args:
            spot_id: The spot ID
            
        Returns:
            The next run time, or None if not scheduled
        """
        if not self.scheduler:
            return None
        
        prefix = f"spot_{spot_id}_schedule_"
        next_time = None
        
        for job in self.scheduler.get_jobs():
            if job.id.startswith(prefix):
                job_next = job.next_run_time
                if job_next and (next_time is None or job_next < next_time):
                    next_time = job_next
        
        return next_time
    
    def get_scheduled_spots(self) -> list[int]:
        """Get list of spot IDs that have scheduled jobs.
        
        Returns:
            List of spot IDs
        """
        if not self.scheduler:
            return []
        
        spot_ids = set()
        for job in self.scheduler.get_jobs():
            if job.id.startswith("spot_"):
                parts = job.id.split("_")
                if len(parts) >= 2:
                    try:
                        spot_ids.add(int(parts[1]))
                    except ValueError:
                        pass
        
        return list(spot_ids)


# Global scheduler instance
_scheduler: Optional[SpotScheduler] = None


def get_scheduler() -> SpotScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SpotScheduler()
    return _scheduler
