"""Spot analyzer using Gemini Vision API."""
import json
import os
import re
import time
import base64
from datetime import datetime
from typing import Optional, List

import aiohttp
from pydantic import BaseModel, Field

from app.core.models import CheckResult, SpotMemory, RichAnalysis
from app.core.voices import get_voice_prompt
from app.core.memory import MemoryEngine
from app.core.config import ConfigManager
from app.core.personalities import get_personality_prompt


class ToSortItemResponse(BaseModel):
    """Pydantic model for items that need sorting."""
    item: str = Field(description="Specific item name")
    location: Optional[str] = Field(default=None, description="Where the item is now")
    priority: str = Field(default="normal", description="Priority level: high, normal, or low")
    quick_fix: Optional[str] = Field(default=None, description="10-second action to fix it")


class QuickWinResponse(BaseModel):
    """Pydantic model for quick win suggestions."""
    action: str = Field(description="Specific quick action")
    time_estimate: str = Field(default="1 min", description="Time estimate for the action")
    impact: str = Field(default="medium", description="Impact level: high, medium, or low")


class NotesResponse(BaseModel):
    """Pydantic model for notes section."""
    main: str = Field(description="Main observation")
    pattern: Optional[str] = Field(default=None, description="Any pattern noticed from history")
    encouragement: Optional[str] = Field(default=None, description="Encouragement message")


class AnalysisResponse(BaseModel):
    """Pydantic model for the complete analysis response from Gemini."""
    status: str = Field(description="Either 'sorted' or 'needs_attention'")
    items_out_of_place: List[ToSortItemResponse] = Field(default_factory=list, description="Items that are out of place")
    looking_good: List[str] = Field(default_factory=list, description="Items in correct place")
    quick_wins: List[QuickWinResponse] = Field(default_factory=list, description="Quick win suggestions")
    time_estimate: str = Field(default="5 min", description="Total time to fix everything")
    one_thing_focus: Optional[str] = Field(default=None, description="Single most important action")
    personality_message: Optional[str] = Field(default=None, description="Main observation in personality voice")
    notes: NotesResponse = Field(default_factory=lambda: NotesResponse(main=""))


class SpotAnalyzer:
    """Analyzes spots using Gemini Vision API."""
    
    GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    
    def __init__(self, db_path: str = "/data/twinsync.db"):
        self.config = ConfigManager(db_path)
        self.memory_engine = MemoryEngine()
        self._cached_api_key: Optional[str] = None
        self._api_key_loaded = False
    
    async def _get_api_key(self) -> Optional[str]:
        """Get Gemini API key from ConfigManager with fallback to environment."""
        if self._api_key_loaded:
            return self._cached_api_key
        
        # Try ConfigManager first (new way)
        self._cached_api_key = await self.config.get_gemini_api_key()
        
        # Fallback to environment for backwards compatibility
        if not self._cached_api_key:
            self._cached_api_key = os.environ.get("GEMINI_API_KEY", "")
        
        self._api_key_loaded = True
        return self._cached_api_key
    
    def invalidate_api_key_cache(self):
        """Invalidate the API key cache (call after settings are updated)."""
        self._api_key_loaded = False
        self._cached_api_key = None
    
    async def analyze(
        self,
        image_bytes: bytes,
        spot_name: str,
        definition: str,
        voice: str = "supportive",
        custom_voice_prompt: str = None,
        memory: SpotMemory = None,
        personality: str = None,
        is_low_energy: bool = False,
    ) -> CheckResult:
        """Analyze a spot image."""
        api_key = await self._get_api_key()
        
        if not api_key:
            return CheckResult(
                status="error",
                error_message="Gemini API key not configured. Please configure in Settings."
            )
        
        start_time = time.time()
        
        try:
            # Get personality prompt if specified
            personality_prompt = None
            if personality:
                personality_prompt = get_personality_prompt(personality)
            
            # Fall back to voice system if no personality
            if not personality_prompt:
                personality_prompt = get_voice_prompt(voice, custom_voice_prompt)
            
            memory_context = self.memory_engine.build_memory_context(memory) if memory else "First check."
            prompt = self._build_prompt(
                spot_name, 
                definition, 
                personality_prompt, 
                memory_context,
                is_low_energy=is_low_energy
            )
            
            # Encode image
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            
            # Make API request
            async with aiohttp.ClientSession() as session:
                payload = {
                    "contents": [{
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": image_b64
                                }
                            }
                        ]
                    }],
                    "generationConfig": {
                        "temperature": 0.4,
                        "maxOutputTokens": 1024,
                    }
                }
                
                url = f"{self.GEMINI_API_URL}?key={api_key}"
                
                async with session.post(url, json=payload) as response:
                    elapsed = time.time() - start_time
                    
                    if response.status == 429:
                        return CheckResult(
                            status="error",
                            error_message="API quota exceeded. Please try again later.",
                            api_response_time=elapsed
                        )
                    
                    if response.status != 200:
                        text = await response.text()
                        return CheckResult(
                            status="error",
                            error_message=f"API error: {response.status} - {text[:200]}",
                            api_response_time=elapsed
                        )
                    
                    data = await response.json()
            
            # Parse response
            result = self._parse_response(data)
            result.api_response_time = elapsed
            
            # Enrich with recurring info from memory
            if memory and memory.patterns.recurring_items:
                result.to_sort = self.memory_engine.enrich_items_with_recurring(
                    result.to_sort, memory.patterns.recurring_items
                )
            
            return result
            
        except aiohttp.ClientError as e:
            return CheckResult(
                status="error",
                error_message=f"Network error: {str(e)}",
                api_response_time=time.time() - start_time
            )
        except Exception as e:
            return CheckResult(
                status="error",
                error_message=f"Unexpected error: {str(e)}",
                api_response_time=time.time() - start_time
            )
    
    def _build_prompt(self, spot_name: str, definition: str, voice_prompt: str, memory_context: str, is_low_energy: bool = False) -> str:
        """Build the analysis prompt with personality and rich analysis support."""
        energy_note = ""
        if is_low_energy:
            energy_note = """
ENERGY NOTE: The user is in a low-energy period. Be extra gentle and focus on just ONE 
quick win they can do right now. Keep everything shorter and less overwhelming.
"""
        
        # Check if definition is minimal/empty and add common sense guidance
        common_sense_note = ""
        definition_text = definition.strip() if definition else ""
        if not definition_text or len(definition_text) < 50:
            common_sense_note = """
IMPORTANT: The user hasn't provided detailed criteria. Use your COMMON SENSE to assess:
- General cleanliness and organization
- Clutter and items out of place
- Surfaces that should be clear (desks, counters, tables)
- Things that don't belong (dishes, trash, clothes in wrong areas)
- Overall tidiness appropriate for this type of space

Be helpful and identify REAL issues you can see, not just what the user mentioned.
"""
        
        return f'''You are checking if "{spot_name}" matches its Ready State.

THE USER'S NOTES (may be minimal - use common sense if so):
{definition_text if definition_text else "(No specific notes provided - use common sense for this type of space)"}
{common_sense_note}
HISTORY:
{memory_context}

YOUR PERSONALITY/VOICE:
{voice_prompt}
{energy_note}
TASK:
Analyze this space and provide rich, detailed feedback in your personality voice.

REQUIRED OUTPUT - Return ONLY valid JSON (no markdown, no backticks):
{{
    "status": "sorted" or "needs_attention",
    "items_out_of_place": [
        {{
            "item": "specific item name",
            "location": "where it is now",
            "priority": "high" or "normal" or "low",
            "quick_fix": "10-second action to fix it"
        }}
    ],
    "looking_good": ["item1 in correct place", "item2 properly arranged"],
    "quick_wins": [
        {{
            "action": "specific quick action",
            "time_estimate": "1 min",
            "impact": "high" or "medium" or "low"
        }}
    ],
    "time_estimate": "total time to fix everything (e.g., '5 min')",
    "one_thing_focus": "THE single most important thing to do right now",
    "personality_message": "Your main observation in your full personality voice - be creative, funny, and memorable!",
    "notes": {{
        "main": "Your detailed observation",
        "pattern": "Any pattern you noticed from history (or null)",
        "encouragement": "Encouragement in your voice (or null)"
    }}
}}

RULES:
- Be SPECIFIC. "Coffee mug on left side of desk" not "items out of place"
- STAY IN CHARACTER with your personality voice throughout
- If user provided notes, reference them; otherwise use common sense
- Reference history patterns if relevant
- Make personality_message memorable and delightful - this is what users see first!
- For quick_wins, suggest immediate actions with real time estimates
- one_thing_focus should be THE most impactful single action
- NO generic phrases. NO clichés. 
- NEVER say "AI" or mention being an AI
- Do NOT include "recurring" field - that will be calculated separately'''
    
    def _parse_response(self, data: dict) -> CheckResult:
        """Parse Gemini API response with rich analysis support using Pydantic validation.
        
        This method uses Pydantic models to validate and parse the JSON response from
        the Gemini API. The instructor library (added to requirements) provides the 
        foundation for structured LLM output handling, while Pydantic provides the 
        actual validation logic. Falls back to legacy parsing for backwards compatibility.
        """
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                return CheckResult(status="error", error_message="No response from API")
            
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                return CheckResult(status="error", error_message="Empty response from API")
            
            text = parts[0].get("text", "")
            
            # Clean up markdown if present
            text = text.strip()
            text = re.sub(r'^```json\s*', '', text)
            text = re.sub(r'^```\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            
            # Parse JSON
            result_data = json.loads(text)
            
            # Use Pydantic model for validation
            try:
                analysis = AnalysisResponse.model_validate(result_data)
                
                # Convert Pydantic models to dicts for compatibility
                items_out_of_place = [item.model_dump() for item in analysis.items_out_of_place]
                quick_wins = [win.model_dump() for win in analysis.quick_wins]
                notes = analysis.notes.model_dump()
                
                # Validate status
                status = analysis.status
                if status not in ("sorted", "needs_attention"):
                    status = "needs_attention"
                
                # Build rich analysis
                rich_analysis = RichAnalysis(
                    items_out_of_place=items_out_of_place,
                    quick_wins=quick_wins,
                    time_estimate=analysis.time_estimate,
                    one_thing_focus=analysis.one_thing_focus,
                    personality_message=analysis.personality_message,
                )
                
                return CheckResult(
                    status=status,
                    to_sort=items_out_of_place,
                    looking_good=analysis.looking_good,
                    notes=notes,
                    rich_analysis=rich_analysis
                )
            except Exception:
                # Fall back to legacy parsing if Pydantic validation fails
                return self._parse_response_legacy(result_data)
            
        except json.JSONDecodeError as e:
            return CheckResult(
                status="error",
                error_message=f"Failed to parse API response: {str(e)}"
            )
    
    def _parse_response_legacy(self, result_data: dict) -> CheckResult:
        """Legacy parsing method for backwards compatibility."""
        # Validate and extract
        status = result_data.get("status", "needs_attention")
        if status not in ("sorted", "needs_attention"):
            status = "needs_attention"
        
        # Handle both old format (to_sort) and new format (items_out_of_place)
        items_out_of_place = result_data.get("items_out_of_place", [])
        to_sort = result_data.get("to_sort", items_out_of_place)
        
        looking_good = result_data.get("looking_good", [])
        notes = result_data.get("notes", {})
        
        # Clean and validate to_sort items
        to_sort = self._validate_to_sort(to_sort)
        
        # Build rich analysis
        rich_analysis = RichAnalysis(
            items_out_of_place=items_out_of_place,
            quick_wins=result_data.get("quick_wins", []),
            time_estimate=result_data.get("time_estimate", "5 min"),
            one_thing_focus=result_data.get("one_thing_focus"),
            personality_message=result_data.get("personality_message"),
        )
        
        return CheckResult(
            status=status,
            to_sort=to_sort,
            looking_good=looking_good,
            notes=notes if isinstance(notes, dict) else {"main": str(notes)},
            rich_analysis=rich_analysis
        )
    
    def _validate_to_sort(self, items: list) -> list:
        """Validate and clean to_sort items with priority support."""
        valid_priorities = {"high", "normal", "low"}
        cleaned = []
        for item in items:
            if isinstance(item, dict):
                # Validate priority value
                priority = item.get("priority", "normal")
                if priority not in valid_priorities:
                    priority = "normal"
                
                # Remove recurring field - we calculate it ourselves
                cleaned_item = {
                    "item": item.get("item", "Unknown item"),
                    "location": item.get("location"),
                    "priority": priority,
                    "quick_fix": item.get("quick_fix"),
                }
                cleaned.append(cleaned_item)
            elif isinstance(item, str):
                cleaned.append({"item": item, "priority": "normal"})
        return cleaned
    
    async def validate_api_key(self) -> bool:
        """Validate that the API key works."""
        api_key = await self._get_api_key()
        
        if not api_key:
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                async with session.get(url) as response:
                    return response.status == 200
        except Exception:
            return False
