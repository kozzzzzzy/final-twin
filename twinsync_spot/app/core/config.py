"""Configuration manager for TwinSync Spot.

Stores all settings in SQLite database instead of environment variables.
"""
import logging
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration settings stored in SQLite database."""
    
    # Setting keys
    HA_URL = "ha_url"
    HA_TOKEN = "ha_token"
    GEMINI_API_KEY = "gemini_api_key"
    HUGGINGFACE_API_KEY = "huggingface_api_key"
    DEEPAI_API_KEY = "deepai_api_key"
    REPLICATE_API_KEY = "replicate_api_key"
    
    # New gamification and personality settings
    PERSONALITY = "personality"
    ENERGY_RHYTHM = "energy_rhythm"
    CRASH_TIMES = "crash_times"
    LOW_ENERGY_MODE = "low_energy_mode"
    
    # Default values
    DEFAULT_PERSONALITY = "polish_grandma"
    DEFAULT_ENERGY_RHYTHM = "normal"  # early_bird, normal, night_owl
    DEFAULT_LOW_ENERGY_MODE = "gentle"  # push, gentle, skip
    
    def __init__(self, db_path: str = "/data/twinsync.db"):
        self.db_path = db_path
    
    async def _ensure_table(self, conn: aiosqlite.Connection):
        """Ensure the settings table exists."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)
        await conn.commit()
    
    async def get(self, key: str) -> Optional[str]:
        """Get a setting value by key.
        
        Args:
            key: The setting key to retrieve
            
        Returns:
            The setting value, or None if not found
        """
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await self._ensure_table(conn)
                cursor = await conn.execute(
                    "SELECT value FROM settings WHERE key = ?",
                    (key,)
                )
                row = await cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"Error getting setting '{key}': {e}")
            return None
    
    async def set(self, key: str, value: str):
        """Set a setting value.
        
        Args:
            key: The setting key
            value: The value to store
        """
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await self._ensure_table(conn)
                now = datetime.utcnow().isoformat()
                await conn.execute(
                    """INSERT OR REPLACE INTO settings (key, value, updated_at)
                       VALUES (?, ?, ?)""",
                    (key, value, now)
                )
                await conn.commit()
                logger.info(f"Setting '{key}' updated")
        except Exception as e:
            logger.error(f"Error setting '{key}': {e}")
            raise
    
    async def delete(self, key: str) -> bool:
        """Delete a setting.
        
        Args:
            key: The setting key to delete
            
        Returns:
            True if a setting was deleted, False otherwise
        """
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await self._ensure_table(conn)
                cursor = await conn.execute(
                    "DELETE FROM settings WHERE key = ?",
                    (key,)
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting setting '{key}': {e}")
            return False
    
    async def get_all(self) -> dict:
        """Get all settings.
        
        Returns:
            Dictionary of all settings {key: value}
        """
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await self._ensure_table(conn)
                cursor = await conn.execute(
                    "SELECT key, value FROM settings"
                )
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.error(f"Error getting all settings: {e}")
            return {}
    
    async def has_setting(self, key: str) -> bool:
        """Check if a setting exists and has a non-empty value.
        
        Args:
            key: The setting key to check
            
        Returns:
            True if the setting exists and has a value
        """
        value = await self.get(key)
        return bool(value and len(value.strip()) > 0)
    
    # Convenience methods for common settings
    async def get_ha_url(self) -> Optional[str]:
        """Get Home Assistant URL."""
        return await self.get(self.HA_URL)
    
    async def get_ha_token(self) -> Optional[str]:
        """Get Home Assistant access token."""
        return await self.get(self.HA_TOKEN)
    
    async def get_gemini_api_key(self) -> Optional[str]:
        """Get Gemini API key."""
        return await self.get(self.GEMINI_API_KEY)
    
    async def set_ha_url(self, url: str):
        """Set Home Assistant URL."""
        # Normalize URL (remove trailing slash)
        url = url.rstrip("/")
        await self.set(self.HA_URL, url)
    
    async def set_ha_token(self, token: str):
        """Set Home Assistant access token."""
        await self.set(self.HA_TOKEN, token.strip())
    
    async def set_gemini_api_key(self, key: str):
        """Set Gemini API key."""
        await self.set(self.GEMINI_API_KEY, key.strip())
    
    async def get_huggingface_api_key(self) -> Optional[str]:
        """Get Hugging Face API key."""
        return await self.get(self.HUGGINGFACE_API_KEY)
    
    async def set_huggingface_api_key(self, key: str):
        """Set Hugging Face API key."""
        await self.set(self.HUGGINGFACE_API_KEY, key.strip())
    
    async def get_deepai_api_key(self) -> Optional[str]:
        """Get DeepAI API key."""
        return await self.get(self.DEEPAI_API_KEY)
    
    async def set_deepai_api_key(self, key: str):
        """Set DeepAI API key."""
        await self.set(self.DEEPAI_API_KEY, key.strip())
    
    async def get_replicate_api_key(self) -> Optional[str]:
        """Get Replicate API key."""
        return await self.get(self.REPLICATE_API_KEY)
    
    async def set_replicate_api_key(self, key: str):
        """Set Replicate API key."""
        await self.set(self.REPLICATE_API_KEY, key.strip())
    
    # New personality and energy settings
    async def get_personality(self) -> str:
        """Get active personality."""
        value = await self.get(self.PERSONALITY)
        return value if value else self.DEFAULT_PERSONALITY
    
    async def set_personality(self, personality: str):
        """Set active personality."""
        await self.set(self.PERSONALITY, personality.strip())
    
    async def get_energy_rhythm(self) -> str:
        """Get energy rhythm (early_bird, normal, night_owl)."""
        value = await self.get(self.ENERGY_RHYTHM)
        return value if value else self.DEFAULT_ENERGY_RHYTHM
    
    async def set_energy_rhythm(self, rhythm: str):
        """Set energy rhythm."""
        if rhythm in ("early_bird", "normal", "night_owl"):
            await self.set(self.ENERGY_RHYTHM, rhythm)
    
    async def get_crash_times(self) -> list[str]:
        """Get crash times as list of hour strings (e.g., ['14', '15'])."""
        value = await self.get(self.CRASH_TIMES)
        if value:
            return [t.strip() for t in value.split(",") if t.strip()]
        return []
    
    async def set_crash_times(self, times: list[str]):
        """Set crash times as list of hour strings."""
        await self.set(self.CRASH_TIMES, ",".join(times))
    
    async def get_low_energy_mode(self) -> str:
        """Get low energy mode (push, gentle, skip)."""
        value = await self.get(self.LOW_ENERGY_MODE)
        return value if value else self.DEFAULT_LOW_ENERGY_MODE
    
    async def set_low_energy_mode(self, mode: str):
        """Set low energy mode."""
        if mode in ("push", "gentle", "skip"):
            await self.set(self.LOW_ENERGY_MODE, mode)
    
    def is_crash_time(self, hour: int, crash_times: list[str]) -> bool:
        """Check if current hour is a crash time."""
        return str(hour) in crash_times
    
    # Alias methods for clarity in API routes
    async def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value by key. Alias for get()."""
        return await self.get(key)
    
    async def set_setting(self, key: str, value: str):
        """Set a setting value. Alias for set()."""
        await self.set(key, value)
