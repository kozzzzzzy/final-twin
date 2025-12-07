"""Voice configurations for TwinSync Spot."""

VOICES = {
    "direct": {
        "name": "Direct",
        "description": "Just the facts, no fluff",
        "prompt": "Be direct and factual. State what you see. No emojis, no encouragement, no filler words.",
        "emoji": "📋"
    },
    "supportive": {
        "name": "Supportive",
        "description": "Encouraging, acknowledges effort",
        "prompt": "Be warm and encouraging. Acknowledge progress and effort. Frame things positively while still being honest about what needs attention.",
        "emoji": "💪"
    },
    "analytical": {
        "name": "Analytical",
        "description": "Spots patterns, references history",
        "prompt": "Focus on patterns and trends. Reference history when relevant. Help the user see recurring issues and improvements over time.",
        "emoji": "📊"
    },
    "minimal": {
        "name": "Minimal",
        "description": "List only, no commentary",
        "prompt": "Provide only the essential list of items. No commentary, no encouragement, no extra words. Just the facts.",
        "emoji": "📝"
    },
    "gentle_nudge": {
        "name": "Gentle Nudge",
        "description": "Soft suggestions for tough days",
        "prompt": "Be extra gentle and understanding. Use soft language like 'maybe' and 'when you're ready'. Low pressure, no judgment.",
        "emoji": "🌸"
    },
    "custom": {
        "name": "Custom",
        "description": "Your own voice",
        "prompt": None,  # User provides their own
        "emoji": "✨"
    }
}


def get_voice_prompt(voice_key: str, custom_prompt: str = None) -> str:
    """Get the prompt for a voice."""
    if voice_key == "custom" and custom_prompt:
        return custom_prompt
    
    voice = VOICES.get(voice_key, VOICES["supportive"])
    return voice["prompt"] or VOICES["supportive"]["prompt"]


def get_all_voices() -> list[dict]:
    """Get all voices as a list."""
    return [
        {
            "key": key,
            "name": voice["name"],
            "description": voice["description"],
            "emoji": voice["emoji"]
        }
        for key, voice in VOICES.items()
    ]
