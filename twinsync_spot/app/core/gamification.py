"""Gamification system for TwinSync Spot.

Adds XP, levels, achievements, and daily challenges to make tidying fun!
"""

from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field


# XP rewards for different actions
XP_REWARDS = {
    "sorted": 50,           # Spot was already sorted
    "quick_reset": 75,      # Reset within 30 minutes of check  
    "reset": 50,            # Normal reset
    "streak_day": 25,       # Bonus per streak day
    "first_check": 100,     # First check of the day
    "perfect_day": 200,     # All spots sorted at end of day
}


# Level definitions with XP thresholds
LEVELS = [
    {"level": 1, "name": "Tidy Novice", "emoji": "🌱", "xp_required": 0},
    {"level": 2, "name": "Clutter Buster", "emoji": "🧹", "xp_required": 250},
    {"level": 3, "name": "Order Apprentice", "emoji": "📚", "xp_required": 600},
    {"level": 4, "name": "Chaos Tamer", "emoji": "⚔️", "xp_required": 1000},
    {"level": 5, "name": "Space Organizer", "emoji": "🗂️", "xp_required": 1500},
    {"level": 6, "name": "Zen Master", "emoji": "🧘", "xp_required": 2200},
    {"level": 7, "name": "Tidiness Sage", "emoji": "🧙", "xp_required": 3000},
    {"level": 8, "name": "Order Keeper", "emoji": "🛡️", "xp_required": 4000},
    {"level": 9, "name": "Harmony Champion", "emoji": "🏆", "xp_required": 5500},
    {"level": 10, "name": "Tidiness Transcended ✨", "emoji": "✨", "xp_required": 7500},
]


# Achievements with unlock conditions
ACHIEVEMENTS = {
    "first_blood": {
        "name": "First Blood",
        "description": "Complete your first spot reset",
        "emoji": "🩸",
        "condition": "first_reset",
        "xp_bonus": 50,
    },
    "streak_3": {
        "name": "Hat Trick",
        "description": "Maintain a 3-day streak",
        "emoji": "🎩",
        "condition": "streak_3",
        "xp_bonus": 75,
    },
    "streak_7": {
        "name": "Week Warrior",
        "description": "Maintain a 7-day streak",
        "emoji": "⚔️",
        "condition": "streak_7",
        "xp_bonus": 150,
    },
    "streak_30": {
        "name": "Monthly Master",
        "description": "Maintain a 30-day streak",
        "emoji": "📅",
        "condition": "streak_30",
        "xp_bonus": 500,
    },
    "early_bird": {
        "name": "Early Bird",
        "description": "Complete a reset before 7 AM",
        "emoji": "🐦",
        "condition": "reset_before_7am",
        "xp_bonus": 50,
    },
    "night_owl": {
        "name": "Night Owl",
        "description": "Complete a reset after 11 PM",
        "emoji": "🦉",
        "condition": "reset_after_11pm",
        "xp_bonus": 50,
    },
    "speed_demon": {
        "name": "Speed Demon",
        "description": "Reset a spot within 5 minutes of check",
        "emoji": "⚡",
        "condition": "reset_under_5min",
        "xp_bonus": 100,
    },
    "perfectionist": {
        "name": "Perfectionist",
        "description": "Have all spots sorted for a full day",
        "emoji": "💎",
        "condition": "perfect_day",
        "xp_bonus": 200,
    },
    "centurion": {
        "name": "Centurion",
        "description": "Complete 100 resets",
        "emoji": "💯",
        "condition": "100_resets",
        "xp_bonus": 300,
    },
    "usual_suspect": {
        "name": "Usual Suspect Hunter",
        "description": "Address an item that appeared 10+ times",
        "emoji": "🔍",
        "condition": "recurring_10",
        "xp_bonus": 75,
    },
    "multi_spot": {
        "name": "Multi-Tasker",
        "description": "Reset 3 spots in one session",
        "emoji": "🤹",
        "condition": "reset_3_spots",
        "xp_bonus": 100,
    },
    "comeback": {
        "name": "Comeback Kid",
        "description": "Start a streak after losing one of 5+ days",
        "emoji": "🔥",
        "condition": "comeback",
        "xp_bonus": 100,
    },
}


# Daily challenges (rotated daily)
DAILY_CHALLENGES = [
    {
        "id": "speed_run",
        "name": "Speed Run",
        "description": "Reset any spot within 10 minutes of check",
        "emoji": "⏱️",
        "xp_reward": 100,
    },
    {
        "id": "morning_routine",
        "name": "Morning Routine",
        "description": "Check all spots before noon",
        "emoji": "☀️",
        "xp_reward": 150,
    },
    {
        "id": "zero_items",
        "name": "Spot Zero",
        "description": "Have any spot with 0 items to sort",
        "emoji": "🎯",
        "xp_reward": 75,
    },
    {
        "id": "triple_check",
        "name": "Triple Check",
        "description": "Check the same spot 3 times today",
        "emoji": "3️⃣",
        "xp_reward": 100,
    },
    {
        "id": "reset_all",
        "name": "Clean Sweep",
        "description": "Reset all spots in one day",
        "emoji": "🧹",
        "xp_reward": 200,
    },
    {
        "id": "quick_fix",
        "name": "Quick Fix",
        "description": "Address a recurring item",
        "emoji": "🔧",
        "xp_reward": 75,
    },
    {
        "id": "streak_saver",
        "name": "Streak Saver",
        "description": "Check and reset before breaking streak",
        "emoji": "🛡️",
        "xp_reward": 100,
    },
]


@dataclass
class GamificationState:
    """Current gamification state for a user."""
    xp_total: int = 0
    level: int = 1
    level_name: str = "Tidy Novice"
    level_emoji: str = "🌱"
    xp_to_next_level: int = 250
    xp_progress_percent: float = 0.0
    achievements_unlocked: list = field(default_factory=list)
    total_resets: int = 0
    current_daily_challenge: Optional[dict] = None
    daily_challenge_completed: bool = False


def calculate_level(xp_total: int) -> dict:
    """Calculate level from total XP."""
    current_level = LEVELS[0]
    next_level = LEVELS[1] if len(LEVELS) > 1 else None
    
    for i, level in enumerate(LEVELS):
        if xp_total >= level["xp_required"]:
            current_level = level
            next_level = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
        else:
            break
    
    xp_to_next = 0
    xp_progress = 100.0
    
    if next_level:
        xp_in_level = xp_total - current_level["xp_required"]
        xp_for_level = next_level["xp_required"] - current_level["xp_required"]
        xp_to_next = next_level["xp_required"] - xp_total
        xp_progress = (xp_in_level / xp_for_level) * 100 if xp_for_level > 0 else 100.0
    
    return {
        "level": current_level["level"],
        "name": current_level["name"],
        "emoji": current_level["emoji"],
        "xp_to_next_level": xp_to_next,
        "xp_progress_percent": min(100.0, xp_progress),
    }


def calculate_xp_for_action(
    action: str,
    streak_days: int = 0,
    minutes_since_check: Optional[int] = None,
) -> int:
    """Calculate XP earned for an action."""
    base_xp = XP_REWARDS.get(action, 0)
    
    # Streak bonus
    if action == "reset" and streak_days > 0:
        base_xp += min(streak_days * XP_REWARDS["streak_day"], 250)  # Cap at 10 days bonus
    
    # Quick reset bonus
    if action == "reset" and minutes_since_check is not None:
        if minutes_since_check <= 5:
            base_xp += 50  # Speed bonus
        elif minutes_since_check <= 30:
            base_xp += 25  # Quick bonus
    
    return base_xp


def check_achievement_unlock(
    achievement_id: str,
    streak_days: int = 0,
    total_resets: int = 0,
    reset_time: Optional[datetime] = None,
    minutes_since_check: Optional[int] = None,
    recurring_item_count: int = 0,
    spots_reset_in_session: int = 0,
    lost_streak_days: int = 0,
) -> bool:
    """Check if an achievement should be unlocked."""
    achievement = ACHIEVEMENTS.get(achievement_id)
    if not achievement:
        return False
    
    condition = achievement["condition"]
    
    if condition == "first_reset":
        return total_resets >= 1
    elif condition == "streak_3":
        return streak_days >= 3
    elif condition == "streak_7":
        return streak_days >= 7
    elif condition == "streak_30":
        return streak_days >= 30
    elif condition == "reset_before_7am":
        return reset_time is not None and reset_time.hour < 7
    elif condition == "reset_after_11pm":
        return reset_time is not None and reset_time.hour >= 23
    elif condition == "reset_under_5min":
        return minutes_since_check is not None and minutes_since_check <= 5
    elif condition == "perfect_day":
        # This would need to be checked externally
        return False
    elif condition == "100_resets":
        return total_resets >= 100
    elif condition == "recurring_10":
        return recurring_item_count >= 10
    elif condition == "reset_3_spots":
        return spots_reset_in_session >= 3
    elif condition == "comeback":
        return lost_streak_days >= 5 and streak_days >= 1
    
    return False


def get_daily_challenge(date: Optional[datetime] = None) -> dict:
    """Get the daily challenge for a given date."""
    if date is None:
        date = datetime.now()
    
    # Use year and day of year to rotate challenges (different challenges each year)
    day_of_year = date.timetuple().tm_yday
    challenge_index = (date.year * 365 + day_of_year) % len(DAILY_CHALLENGES)
    
    return DAILY_CHALLENGES[challenge_index]


def get_all_achievements() -> list[dict]:
    """Get all achievements for display."""
    return [
        {
            "id": key,
            "name": achievement["name"],
            "description": achievement["description"],
            "emoji": achievement["emoji"],
            "xp_bonus": achievement["xp_bonus"],
        }
        for key, achievement in ACHIEVEMENTS.items()
    ]


def get_level_info(level: int) -> Optional[dict]:
    """Get info for a specific level."""
    for lvl in LEVELS:
        if lvl["level"] == level:
            return lvl
    return None


def get_all_levels() -> list[dict]:
    """Get all levels for display."""
    return LEVELS.copy()
