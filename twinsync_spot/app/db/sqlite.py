"""SQLite database for TwinSync Spot."""
import json
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional
import aiosqlite

from app.core.models import Spot, SpotStatus, CheckResult, SpotMemory
from app.core.memory import MemoryEngine


class Database:
    """SQLite database handler."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
    
    async def init(self):
        """Initialize database and create tables."""
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS spots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                camera_entity TEXT NOT NULL,
                definition TEXT NOT NULL,
                spot_type TEXT NOT NULL DEFAULT 'custom',
                voice TEXT NOT NULL DEFAULT 'supportive',
                custom_voice_prompt TEXT,
                personality TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown',
                last_check TEXT,
                current_streak INTEGER NOT NULL DEFAULT 0,
                longest_streak INTEGER NOT NULL DEFAULT 0,
                snoozed_until TEXT,
                total_resets INTEGER NOT NULL DEFAULT 0,
                last_reset TEXT,
                check_schedule TEXT
            );
            
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spot_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                to_sort_json TEXT,
                looking_good_json TEXT,
                notes_main TEXT,
                notes_pattern TEXT,
                notes_encouragement TEXT,
                error_message TEXT,
                api_response_time REAL,
                rich_analysis_json TEXT,
                xp_earned INTEGER DEFAULT 0,
                FOREIGN KEY (spot_id) REFERENCES spots(id) ON DELETE CASCADE
            );
            
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            );
            
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                camera_type TEXT NOT NULL,
                url TEXT,
                username TEXT,
                password TEXT,
                entity_id TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS gamification (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                xp_total INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                achievements_json TEXT,
                daily_challenge_completed TEXT,
                last_reset_session TEXT,
                spots_reset_in_session INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            
            CREATE INDEX IF NOT EXISTS idx_checks_spot_id ON checks(spot_id);
            CREATE INDEX IF NOT EXISTS idx_checks_timestamp ON checks(timestamp);
            CREATE INDEX IF NOT EXISTS idx_api_tokens_token ON api_tokens(token);
        """)
        await self.conn.commit()
        
        # Add check_schedule column if it doesn't exist (migration for existing DBs)
        try:
            await self.conn.execute("ALTER TABLE spots ADD COLUMN check_schedule TEXT")
            await self.conn.commit()
        except Exception:
            pass  # Column already exists
        
        # Add personality column to spots if it doesn't exist (for per-spot personality)
        try:
            await self.conn.execute("ALTER TABLE spots ADD COLUMN personality TEXT")
            await self.conn.commit()
        except Exception:
            pass  # Column already exists
        
        # Add rich_analysis_json and xp_earned columns to checks if they don't exist
        try:
            await self.conn.execute("ALTER TABLE checks ADD COLUMN rich_analysis_json TEXT")
            await self.conn.commit()
        except Exception:
            pass  # Column already exists
        
        try:
            await self.conn.execute("ALTER TABLE checks ADD COLUMN xp_earned INTEGER DEFAULT 0")
            await self.conn.commit()
        except Exception:
            pass  # Column already exists
        
        # Add dream_state_image column to spots if it doesn't exist
        try:
            await self.conn.execute("ALTER TABLE spots ADD COLUMN dream_state_image TEXT")
            await self.conn.commit()
        except Exception:
            pass  # Column already exists
        
        # Add dream_state_generating column to spots if it doesn't exist
        try:
            await self.conn.execute("ALTER TABLE spots ADD COLUMN dream_state_generating INTEGER DEFAULT 0")
            await self.conn.commit()
        except Exception:
            pass  # Column already exists
        
        # Add dream_state_error column to spots if it doesn't exist
        try:
            await self.conn.execute("ALTER TABLE spots ADD COLUMN dream_state_error TEXT")
            await self.conn.commit()
        except Exception:
            pass  # Column already exists
        
        # Initialize gamification row if not exists
        await self._ensure_gamification_row()
    
    async def close(self):
        """Close database connection."""
        if self.conn:
            await self.conn.close()
    
    # Spot operations
    async def create_spot(self, name: str, camera_entity: str, definition: str,
                          spot_type: str = "custom", voice: str = "supportive",
                          custom_voice_prompt: str = None, personality: str = None) -> int:
        """Create a new spot."""
        now = datetime.utcnow().isoformat()
        cursor = await self.conn.execute(
            """INSERT INTO spots (name, camera_entity, definition, spot_type, voice, 
               custom_voice_prompt, personality, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, camera_entity, definition, spot_type, voice, custom_voice_prompt, personality, now, "unknown")
        )
        await self.conn.commit()
        return cursor.lastrowid
    
    async def get_spot(self, spot_id: int) -> Optional[Spot]:
        """Get a spot by ID."""
        cursor = await self.conn.execute(
            "SELECT * FROM spots WHERE id = ?", (spot_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_spot(row)
    
    async def get_all_spots(self) -> list[Spot]:
        """Get all spots."""
        cursor = await self.conn.execute("SELECT * FROM spots ORDER BY name")
        rows = await cursor.fetchall()
        return [self._row_to_spot(row) for row in rows]
    
    async def update_spot(self, spot_id: int, **kwargs) -> bool:
        """Update a spot."""
        if not kwargs:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [spot_id]
        
        cursor = await self.conn.execute(
            f"UPDATE spots SET {set_clause} WHERE id = ?", values
        )
        await self.conn.commit()
        return cursor.rowcount > 0
    
    async def delete_spot(self, spot_id: int) -> bool:
        """Delete a spot."""
        cursor = await self.conn.execute(
            "DELETE FROM spots WHERE id = ?", (spot_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0
    
    # Check operations
    async def save_check(self, spot_id: int, result: CheckResult) -> int:
        """Save a check result."""
        now = datetime.utcnow().isoformat()
        
        # Serialize to_sort items properly - handle both dict and dataclass
        to_sort_items = []
        for item in (result.to_sort or []):
            if hasattr(item, '__dataclass_fields__'):
                to_sort_items.append(asdict(item))
            elif isinstance(item, dict):
                to_sort_items.append(item)
            else:
                to_sort_items.append({"item": str(item)})
        to_sort_json = json.dumps(to_sort_items)
        looking_good_json = json.dumps(result.looking_good or [])
        
        # Serialize rich_analysis if present
        rich_analysis_json = None
        if result.rich_analysis:
            rich_analysis_json = json.dumps({
                "items_out_of_place": result.rich_analysis.items_out_of_place,
                "quick_wins": result.rich_analysis.quick_wins,
                "time_estimate": result.rich_analysis.time_estimate,
                "one_thing_focus": result.rich_analysis.one_thing_focus,
                "personality_message": result.rich_analysis.personality_message,
                "energy_adjusted": result.rich_analysis.energy_adjusted,
            })
        
        cursor = await self.conn.execute(
            """INSERT INTO checks (spot_id, timestamp, status, to_sort_json, looking_good_json,
               notes_main, notes_pattern, notes_encouragement, error_message, api_response_time,
               rich_analysis_json, xp_earned)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (spot_id, now, result.status, to_sort_json, looking_good_json,
             result.notes.get("main") if result.notes else None,
             result.notes.get("pattern") if result.notes else None,
             result.notes.get("encouragement") if result.notes else None,
             result.error_message, result.api_response_time,
             rich_analysis_json, result.xp_earned)
        )
        await self.conn.commit()
        
        # Update spot status
        await self.update_spot(spot_id, status=result.status, last_check=now)
        
        return cursor.lastrowid
    
    async def get_recent_checks(self, spot_id: int, limit: int = 10) -> list[dict]:
        """Get recent checks for a spot."""
        cursor = await self.conn.execute(
            """SELECT * FROM checks WHERE spot_id = ? 
               ORDER BY timestamp DESC LIMIT ?""",
            (spot_id, limit)
        )
        rows = await cursor.fetchall()
        return [self._row_to_check(row) for row in rows]
    
    async def get_checks_paginated(self, spot_id: int, page: int = 1, per_page: int = 20) -> tuple[list[dict], int]:
        """Get paginated check history for a spot.
        
        Returns (checks, total_count) tuple.
        """
        offset = (page - 1) * per_page
        
        # Get total count
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM checks WHERE spot_id = ?",
            (spot_id,)
        )
        row = await cursor.fetchone()
        total = row[0] if row else 0
        
        # Get paginated results
        cursor = await self.conn.execute(
            """SELECT * FROM checks WHERE spot_id = ? 
               ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
            (spot_id, per_page, offset)
        )
        rows = await cursor.fetchall()
        checks = [self._row_to_check(row) for row in rows]
        
        return checks, total
    
    async def get_check(self, check_id: int) -> Optional[dict]:
        """Get a single check by ID."""
        cursor = await self.conn.execute(
            "SELECT * FROM checks WHERE id = ?",
            (check_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_check(row)
    
    async def delete_check(self, check_id: int) -> bool:
        """Delete a check entry."""
        cursor = await self.conn.execute(
            "DELETE FROM checks WHERE id = ?",
            (check_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0
    
    async def update_check_notes(self, check_id: int, notes_main: Optional[str] = None) -> bool:
        """Update notes on a check entry."""
        cursor = await self.conn.execute(
            "UPDATE checks SET notes_main = ? WHERE id = ?",
            (notes_main, check_id)
        )
        await self.conn.commit()
        return cursor.rowcount > 0
    
    async def clear_spot_history(self, spot_id: int) -> int:
        """Clear all check history for a spot. Returns number of deleted checks."""
        cursor = await self.conn.execute(
            "DELETE FROM checks WHERE spot_id = ?",
            (spot_id,)
        )
        await self.conn.commit()
        return cursor.rowcount
    
    async def get_checks_for_graph(self, spot_id: int, days: int = 30) -> list[dict]:
        """Get check data aggregated for graphing purposes.
        
        Returns list of dicts with date, sorted_count, needs_attention_count.
        """
        since = datetime.utcnow() - timedelta(days=days)
        cursor = await self.conn.execute(
            """SELECT 
                DATE(timestamp) as check_date,
                SUM(CASE WHEN status = 'sorted' THEN 1 ELSE 0 END) as sorted_count,
                SUM(CASE WHEN status = 'needs_attention' THEN 1 ELSE 0 END) as needs_attention_count,
                COUNT(*) as total_count
               FROM checks 
               WHERE spot_id = ? AND timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY check_date ASC""",
            (spot_id, since.isoformat())
        )
        rows = await cursor.fetchall()
        return [
            {
                "date": row["check_date"],
                "sorted": row["sorted_count"],
                "needs_attention": row["needs_attention_count"],
                "total": row["total_count"]
            }
            for row in rows
        ]
    
    async def get_checks_since(self, spot_id: int, since: datetime) -> list[dict]:
        """Get checks since a certain date."""
        cursor = await self.conn.execute(
            """SELECT * FROM checks WHERE spot_id = ? AND timestamp >= ?
               ORDER BY timestamp ASC""",
            (spot_id, since.isoformat())
        )
        rows = await cursor.fetchall()
        return [self._row_to_check(row) for row in rows]
    
    async def get_spot_memory(self, spot_id: int) -> SpotMemory:
        """Get memory/patterns for a spot."""
        # Get checks from last 30 days
        since = datetime.utcnow() - timedelta(days=30)
        checks = await self.get_checks_since(spot_id, since)
        
        spot = await self.get_spot(spot_id)
        
        # Use MemoryEngine to calculate patterns
        engine = MemoryEngine()
        return engine.calculate_memory(spot_id, checks, spot)
    
    async def record_reset(self, spot_id: int):
        """Record a reset (user marked spot as fixed)."""
        spot = await self.get_spot(spot_id)
        if not spot:
            return
        
        now = datetime.utcnow().isoformat()
        new_streak = spot.current_streak + 1
        longest = max(spot.longest_streak, new_streak)
        
        await self.update_spot(
            spot_id,
            status="sorted",
            current_streak=new_streak,
            longest_streak=longest,
            total_resets=spot.total_resets + 1,
            last_reset=now
        )
    
    def _row_to_spot(self, row) -> Spot:
        """Convert database row to Spot object."""
        # Handle check_schedule which may not exist in old DBs before migration
        # aiosqlite.Row doesn't support .get(), so we use try/except
        check_schedule = None
        try:
            check_schedule = row["check_schedule"]
        except (KeyError, IndexError):
            pass  # Column doesn't exist yet - that's okay
        
        personality = None
        try:
            personality = row["personality"]
        except (KeyError, IndexError):
            pass  # Column doesn't exist yet - that's okay
        
        dream_state_image = None
        try:
            dream_state_image = row["dream_state_image"]
        except (KeyError, IndexError):
            pass  # Column doesn't exist yet - that's okay
        
        dream_state_generating = False
        try:
            dream_state_generating = bool(row["dream_state_generating"])
        except (KeyError, IndexError):
            pass  # Column doesn't exist yet - that's okay
        
        dream_state_error = None
        try:
            dream_state_error = row["dream_state_error"]
        except (KeyError, IndexError):
            pass  # Column doesn't exist yet - that's okay
        
        return Spot(
            id=row["id"],
            name=row["name"],
            camera_entity=row["camera_entity"],
            definition=row["definition"],
            spot_type=row["spot_type"],
            voice=row["voice"],
            custom_voice_prompt=row["custom_voice_prompt"],
            personality=personality,
            created_at=row["created_at"],
            status=SpotStatus(row["status"]) if row["status"] else SpotStatus.UNKNOWN,
            last_check=row["last_check"],
            current_streak=row["current_streak"],
            longest_streak=row["longest_streak"],
            snoozed_until=row["snoozed_until"],
            total_resets=row["total_resets"],
            last_reset=row["last_reset"],
            check_schedule=check_schedule,
            dream_state_image=dream_state_image,
            dream_state_generating=dream_state_generating,
            dream_state_error=dream_state_error,
        )
    
    def _row_to_check(self, row) -> dict:
        """Convert database row to check dict."""
        # Parse rich_analysis if present
        rich_analysis = None
        try:
            if row["rich_analysis_json"]:
                rich_analysis = json.loads(row["rich_analysis_json"])
        except (KeyError, json.JSONDecodeError):
            pass
        
        # Handle xp_earned which may not exist in old rows
        xp_earned = 0
        try:
            xp_earned = row["xp_earned"] or 0
        except (KeyError, IndexError):
            pass
        
        return {
            "id": row["id"],
            "spot_id": row["spot_id"],
            "timestamp": row["timestamp"],
            "status": row["status"],
            "to_sort": json.loads(row["to_sort_json"]) if row["to_sort_json"] else [],
            "looking_good": json.loads(row["looking_good_json"]) if row["looking_good_json"] else [],
            "notes": {
                "main": row["notes_main"],
                "pattern": row["notes_pattern"],
                "encouragement": row["notes_encouragement"],
            },
            "error_message": row["error_message"],
            "api_response_time": row["api_response_time"],
            "rich_analysis": rich_analysis,
            "xp_earned": xp_earned,
        }
    
    # API Token operations
    async def create_api_token(self, name: str) -> str:
        """Create a new API token for mobile app access.
        
        Args:
            name: A friendly name for the token
            
        Returns:
            The generated token string
        """
        token = secrets.token_urlsafe(32)
        now = datetime.utcnow().isoformat()
        
        await self.conn.execute(
            """INSERT INTO api_tokens (token, name, created_at, is_active)
               VALUES (?, ?, ?, 1)""",
            (token, name, now)
        )
        await self.conn.commit()
        return token
    
    async def verify_api_token(self, token: str) -> bool:
        """Verify an API token is valid and active.
        
        Args:
            token: The token to verify
            
        Returns:
            True if the token is valid and active
        """
        cursor = await self.conn.execute(
            """SELECT id, is_active FROM api_tokens WHERE token = ?""",
            (token,)
        )
        row = await cursor.fetchone()
        if not row or not row["is_active"]:
            return False
        
        # Update last_used timestamp
        now = datetime.utcnow().isoformat()
        await self.conn.execute(
            "UPDATE api_tokens SET last_used = ? WHERE id = ?",
            (now, row["id"])
        )
        await self.conn.commit()
        return True
    
    async def list_api_tokens(self) -> list[dict]:
        """List all API tokens (without exposing token values).
        
        Returns:
            List of token info dicts (id, name, created_at, last_used, is_active)
        """
        cursor = await self.conn.execute(
            """SELECT id, name, created_at, last_used, is_active 
               FROM api_tokens ORDER BY created_at DESC"""
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "last_used": row["last_used"],
                "is_active": bool(row["is_active"])
            }
            for row in rows
        ]
    
    async def revoke_api_token(self, token_id: int) -> bool:
        """Revoke an API token.
        
        Args:
            token_id: The token ID to revoke
            
        Returns:
            True if the token was revoked
        """
        cursor = await self.conn.execute(
            "UPDATE api_tokens SET is_active = 0 WHERE id = ?",
            (token_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0
    
    async def delete_api_token(self, token_id: int) -> bool:
        """Delete an API token permanently.
        
        Args:
            token_id: The token ID to delete
            
        Returns:
            True if the token was deleted
        """
        cursor = await self.conn.execute(
            "DELETE FROM api_tokens WHERE id = ?",
            (token_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0
    
    # Gamification operations
    async def _ensure_gamification_row(self):
        """Ensure there's a gamification row in the database."""
        cursor = await self.conn.execute("SELECT COUNT(*) FROM gamification")
        row = await cursor.fetchone()
        if row[0] == 0:
            now = datetime.utcnow().isoformat()
            await self.conn.execute(
                """INSERT INTO gamification (xp_total, level, achievements_json, updated_at)
                   VALUES (0, 1, '[]', ?)""",
                (now,)
            )
            await self.conn.commit()
    
    async def get_gamification_state(self) -> dict:
        """Get the current gamification state."""
        from app.core.gamification import calculate_level
        
        cursor = await self.conn.execute(
            """SELECT xp_total, level, achievements_json, daily_challenge_completed,
                      spots_reset_in_session, updated_at FROM gamification LIMIT 1"""
        )
        row = await cursor.fetchone()
        
        if not row:
            return {
                "xp_total": 0,
                "level": 1,
                "level_name": "Tidy Novice",
                "level_emoji": "🌱",
                "xp_to_next_level": 250,
                "xp_progress_percent": 0,
                "achievements_unlocked": [],
            }
        
        xp_total = row["xp_total"]
        level_info = calculate_level(xp_total)
        
        achievements = []
        if row["achievements_json"]:
            try:
                achievements = json.loads(row["achievements_json"])
            except json.JSONDecodeError:
                pass
        
        return {
            "xp_total": xp_total,
            "level": level_info["level"],
            "level_name": level_info["name"],
            "level_emoji": level_info["emoji"],
            "xp_to_next_level": level_info["xp_to_next_level"],
            "xp_progress_percent": level_info["xp_progress_percent"],
            "achievements_unlocked": achievements,
            "daily_challenge_completed": row["daily_challenge_completed"],
            "spots_reset_in_session": row["spots_reset_in_session"] or 0,
        }
    
    async def add_xp(self, amount: int) -> dict:
        """Add XP and return updated gamification state."""
        from app.core.gamification import calculate_level
        
        now = datetime.utcnow().isoformat()
        
        # Update XP
        await self.conn.execute(
            "UPDATE gamification SET xp_total = xp_total + ?, updated_at = ?",
            (amount, now)
        )
        await self.conn.commit()
        
        return await self.get_gamification_state()
    
    async def unlock_achievement(self, achievement_id: str) -> bool:
        """Unlock an achievement. Returns True if newly unlocked."""
        state = await self.get_gamification_state()
        achievements = state["achievements_unlocked"]
        
        if achievement_id in achievements:
            return False  # Already unlocked
        
        achievements.append(achievement_id)
        now = datetime.utcnow().isoformat()
        
        await self.conn.execute(
            "UPDATE gamification SET achievements_json = ?, updated_at = ?",
            (json.dumps(achievements), now)
        )
        await self.conn.commit()
        
        return True
    
    async def increment_session_resets(self) -> int:
        """Increment spots reset in current session. Returns new count."""
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().date().isoformat()
        
        # Check if we need to reset the session (new day)
        cursor = await self.conn.execute(
            "SELECT last_reset_session, spots_reset_in_session FROM gamification LIMIT 1"
        )
        row = await cursor.fetchone()
        
        last_session = row["last_reset_session"] if row else None
        current_count = row["spots_reset_in_session"] or 0 if row else 0
        
        if last_session != today:
            # New day, reset counter
            current_count = 0
        
        current_count += 1
        
        await self.conn.execute(
            """UPDATE gamification 
               SET spots_reset_in_session = ?, last_reset_session = ?, updated_at = ?""",
            (current_count, today, now)
        )
        await self.conn.commit()
        
        return current_count
    
    async def complete_daily_challenge(self) -> bool:
        """Mark today's daily challenge as completed."""
        today = datetime.utcnow().date().isoformat()
        now = datetime.utcnow().isoformat()
        
        # Check if already completed today
        cursor = await self.conn.execute(
            "SELECT daily_challenge_completed FROM gamification LIMIT 1"
        )
        row = await cursor.fetchone()
        
        if row and row["daily_challenge_completed"] == today:
            return False  # Already completed
        
        await self.conn.execute(
            "UPDATE gamification SET daily_challenge_completed = ?, updated_at = ?",
            (today, now)
        )
        await self.conn.commit()
        
        return True
