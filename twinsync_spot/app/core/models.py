"""Core models for TwinSync Spot."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SpotType(Enum):
    """Types of spots with templates."""
    WORK = "work"
    CHILL = "chill"
    SLEEP = "sleep"
    KITCHEN = "kitchen"
    ENTRYWAY = "entryway"
    STORAGE = "storage"
    CUSTOM = "custom"


class SpotStatus(Enum):
    """Status of a spot."""
    SORTED = "sorted"
    NEEDS_ATTENTION = "needs_attention"
    ERROR = "error"
    UNKNOWN = "unknown"
    SNOOZED = "snoozed"


# Templates for each spot type
SPOT_TEMPLATES = {
    "work": """This is my work area. I need a clear surface to focus.

Things that should be here:
- Laptop/monitor
- Keyboard and mouse
- Notepad/pen

Things that shouldn't be here:
- Coffee cups or dishes
- Random papers
- Clutter""",

    "chill": """This is where I relax. Should feel calm and uncluttered.

Things that are fine here:
- Remote controls
- Blankets/pillows
- Books I'm reading

Things that shouldn't pile up:
- Empty cups/plates
- Random items
- Clutter""",

    "sleep": """This is my sleep space. Should be calm and ready for rest.

Ready state:
- Bed made
- No clothes on floor
- Nightstand clear except essentials""",

    "kitchen": """This is my kitchen area. Should be clear and ready to use.

Ready state:
- Counters clear
- Dishes put away
- No food left out""",

    "entryway": """This is my entryway. First thing I see coming home.

Ready state:
- Shoes in rack/organised
- No bags on floor
- Keys in place""",

    "storage": """This is a storage area. Things should be organised.

What belongs here:
- Specific items for this space

Signs it needs sorting:
- Items out of place
- Things piling up""",

    "custom": """Describe this space in your own words.

What is it for?
What should it look like when ready?
What shouldn't be here?"""
}


@dataclass
class ToSortItem:
    """An item that needs sorting."""
    item: str
    location: Optional[str] = None
    recurring: bool = False
    recurrence_count: int = 0
    priority: str = "normal"  # high, normal, low
    quick_fix: Optional[str] = None  # Suggested quick action


@dataclass
class QuickWin:
    """A quick win suggestion for immediate impact."""
    action: str
    item: Optional[str] = None
    time_estimate: str = "1 min"
    impact: str = "medium"  # high, medium, low


@dataclass 
class RichAnalysis:
    """Rich analysis results from AI with detailed breakdown."""
    items_out_of_place: list = field(default_factory=list)  # List of ToSortItem-like dicts
    quick_wins: list = field(default_factory=list)  # List of QuickWin-like dicts
    time_estimate: str = "5 min"
    one_thing_focus: Optional[str] = None  # Single most important action
    personality_message: Optional[str] = None  # Message in selected personality voice
    energy_adjusted: bool = False  # Whether message was adjusted for low energy


@dataclass
class CheckResult:
    """Result of a spot check."""
    status: str
    to_sort: list = field(default_factory=list)
    looking_good: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)
    error_message: Optional[str] = None
    api_response_time: Optional[float] = None
    # New rich analysis fields
    rich_analysis: Optional[RichAnalysis] = None
    xp_earned: int = 0
    achievements_unlocked: list = field(default_factory=list)


@dataclass
class SpotPatterns:
    """Patterns detected for a spot."""
    recurring_items: dict = field(default_factory=dict)  # {"coffee mug": 12}
    usually_sorted_by: Optional[str] = None  # "10:00 AM"
    worst_day: Optional[str] = None  # "Monday"
    best_day: Optional[str] = None  # "Friday"
    # New pattern fields
    items_frequency: dict = field(default_factory=dict)  # {"item": count} for all items
    day_of_week_stats: dict = field(default_factory=dict)  # {"Monday": {"sorted": 5, "needs_attention": 2}}
    time_of_day_stats: dict = field(default_factory=dict)  # {"morning": {"sorted": 10}, "afternoon": {...}}
    pattern_insights: list = field(default_factory=list)  # Generated insights


@dataclass
class SpotMemory:
    """Memory/history for a spot."""
    spot_id: int
    patterns: SpotPatterns = field(default_factory=SpotPatterns)
    current_streak: int = 0
    longest_streak: int = 0
    total_checks: int = 0
    last_check_status: Optional[str] = None
    # New gamification fields
    xp_total: int = 0
    level: int = 1
    level_name: str = "Tidy Novice"
    achievements: list = field(default_factory=list)


@dataclass
class Spot:
    """A spot being tracked."""
    id: int
    name: str
    camera_entity: str
    definition: str
    spot_type: str = "custom"
    voice: str = "supportive"
    custom_voice_prompt: Optional[str] = None
    personality: Optional[str] = None  # Per-spot personality override
    created_at: Optional[str] = None
    status: SpotStatus = SpotStatus.UNKNOWN
    last_check: Optional[str] = None
    current_streak: int = 0
    longest_streak: int = 0
    snoozed_until: Optional[str] = None
    total_resets: int = 0
    last_reset: Optional[str] = None
    check_schedule: Optional[str] = None  # JSON string of schedule config
    dream_state_image: Optional[str] = None  # URL/path to AI-generated dream state image
    dream_state_generating: bool = False  # Whether dream state is currently being generated
    dream_state_error: Optional[str] = None  # Error message if dream state generation failed


@dataclass
class Camera:
    """A camera entity."""
    entity_id: str
    name: str
    state: str = "unknown"
