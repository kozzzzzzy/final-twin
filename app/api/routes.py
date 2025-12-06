"""API routes for TwinSync Spot."""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import aiohttp
from fastapi import APIRouter, HTTPException, Request, File, UploadFile
from pydantic import BaseModel

from app.core.models import SPOT_TEMPLATES, SpotStatus
from app.core.voices import get_all_voices
from app.core.personalities import get_all_personalities
from app.core.gamification import (
    calculate_xp_for_action, 
    get_daily_challenge, 
    get_all_achievements,
    get_all_levels,
    check_achievement_unlock,
    ACHIEVEMENTS,
)
from app.core.analyzer import SpotAnalyzer
from app.core.config import ConfigManager
from app.camera.ha_adapter import HACamera
from app.camera.manager import CameraManager
from app.version import VERSION


logger = logging.getLogger(__name__)

router = APIRouter()

# Get data directory from environment
DATA_DIR = os.environ.get("DATA_DIR", "/data")

# Maximum upload size for images (10MB)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# Request/Response models
class CreateSpotRequest(BaseModel):
    name: str
    camera_entity: Optional[str] = None  # Now optional - user can use uploads instead
    definition: Optional[str] = ""  # Make definition optional, default to empty
    spot_type: str = "custom"
    voice: str = "supportive"
    custom_voice_prompt: Optional[str] = None
    personality: Optional[str] = None  # Per-spot personality
    check_schedule: Optional[str] = None  # JSON string


class UpdateSpotRequest(BaseModel):
    name: Optional[str] = None
    camera_entity: Optional[str] = None
    definition: Optional[str] = None
    spot_type: Optional[str] = None
    voice: Optional[str] = None
    custom_voice_prompt: Optional[str] = None
    personality: Optional[str] = None  # Per-spot personality
    check_schedule: Optional[str] = None


class ScheduleItem(BaseModel):
    time: str  # HH:MM format
    days: List[str]  # ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class UpdateScheduleRequest(BaseModel):
    schedule: List[ScheduleItem]


class SnoozeRequest(BaseModel):
    minutes: int = 30


class SaveSettingsRequest(BaseModel):
    """Request to save all settings at once."""
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None
    gemini_api_key: Optional[str] = None
    huggingface_api_key: Optional[str] = None
    deepai_api_key: Optional[str] = None
    replicate_api_key: Optional[str] = None
    personality: Optional[str] = None
    energy_rhythm: Optional[str] = None
    crash_times: Optional[List[str]] = None
    low_energy_mode: Optional[str] = None


# Spots
@router.get("/spots")
async def list_spots(request: Request):
    """List all spots with memory summaries."""
    db = request.app.state.db
    spots = await db.get_all_spots()
    
    result_spots = []
    for s in spots:
        # Get memory for recurring items count
        memory = await db.get_spot_memory(s.id)
        recurring_count = len(memory.patterns.recurring_items) if memory.patterns.recurring_items else 0
        
        # Parse schedule
        schedule = None
        if s.check_schedule:
            try:
                schedule = json.loads(s.check_schedule)
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Get personality emoji if personality is set
        from app.core.personalities import get_personality_emoji
        personality_emoji = get_personality_emoji(s.personality) if s.personality else None
        
        result_spots.append({
            "id": s.id,
            "name": s.name,
            "camera_entity": s.camera_entity,
            "definition": s.definition,
            "spot_type": s.spot_type,
            "voice": s.voice,
            "personality": s.personality,
            "personality_emoji": personality_emoji,
            "status": s.status.value if isinstance(s.status, SpotStatus) else s.status,
            "last_check": s.last_check,
            "current_streak": s.current_streak,
            "longest_streak": s.longest_streak,
            "snoozed_until": s.snoozed_until,
            "recurring_items_count": recurring_count,
            "has_schedule": schedule is not None and len(schedule) > 0,
            "total_checks": memory.total_checks,
        })
    
    return {"spots": result_spots}


@router.post("/spots")
async def create_spot(request: Request, data: CreateSpotRequest):
    """Create a new spot. Camera entity is optional - users can use photo uploads instead."""
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    
    # Validate camera entity if provided
    camera_entity = data.camera_entity or ""
    if camera_entity and camera_entity.strip():
        # Check if camera exists in Home Assistant
        camera = HACamera(db_path)
        cameras = await camera.get_cameras()
        camera_exists = any(c.entity_id == camera_entity for c in cameras)
        
        if not camera_exists:
            raise HTTPException(
                status_code=400,
                detail=f"Camera '{camera_entity}' not found in Home Assistant. Please select a valid camera or create an upload-only spot."
            )
    
    spot_id = await db.create_spot(
        name=data.name,
        camera_entity=camera_entity,
        definition=data.definition or "",  # Empty string if not provided
        spot_type=data.spot_type,
        voice=data.voice,
        custom_voice_prompt=data.custom_voice_prompt,
        personality=data.personality,
    )
    
    # Set schedule if provided
    if data.check_schedule:
        await db.update_spot(spot_id, check_schedule=data.check_schedule)
    
    return {"id": spot_id, "message": "Spot created"}


@router.get("/spots/{spot_id}")
async def get_spot(request: Request, spot_id: int):
    """Get a spot with its memory."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    memory = await db.get_spot_memory(spot_id)
    recent_checks = await db.get_recent_checks(spot_id, limit=10)
    
    # Parse schedule JSON if present
    schedule = None
    if spot.check_schedule:
        try:
            schedule = json.loads(spot.check_schedule)
        except (json.JSONDecodeError, TypeError):
            schedule = None
    
    return {
        "spot": {
            "id": spot.id,
            "name": spot.name,
            "camera_entity": spot.camera_entity,
            "definition": spot.definition,
            "spot_type": spot.spot_type,
            "voice": spot.voice,
            "custom_voice_prompt": spot.custom_voice_prompt,
            "personality": spot.personality,
            "status": spot.status.value if isinstance(spot.status, SpotStatus) else spot.status,
            "last_check": spot.last_check,
            "current_streak": spot.current_streak,
            "longest_streak": spot.longest_streak,
            "snoozed_until": spot.snoozed_until,
            "total_resets": spot.total_resets,
            "check_schedule": schedule,
            "dream_state_image": spot.dream_state_image,
            "dream_state_generating": spot.dream_state_generating,
        },
        "memory": {
            "total_checks": memory.total_checks,
            "patterns": {
                "recurring_items": memory.patterns.recurring_items,
                "worst_day": memory.patterns.worst_day,
                "best_day": memory.patterns.best_day,
                "usually_sorted_by": memory.patterns.usually_sorted_by,
            }
        },
        "recent_checks": recent_checks,
    }


@router.put("/spots/{spot_id}")
async def update_spot(request: Request, spot_id: int, data: UpdateSpotRequest):
    """Update a spot."""
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    # Validate camera entity if being updated
    if "camera_entity" in updates:
        camera_entity = updates["camera_entity"]
        if camera_entity and camera_entity.strip():
            # Check if camera exists in Home Assistant
            camera = HACamera(db_path)
            cameras = await camera.get_cameras()
            camera_exists = any(c.entity_id == camera_entity for c in cameras)
            
            if not camera_exists:
                raise HTTPException(
                    status_code=400,
                    detail=f"Camera '{camera_entity}' not found in Home Assistant. Please select a valid camera."
                )
    
    success = await db.update_spot(spot_id, **updates)
    if not success:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    return {"message": "Spot updated"}


@router.delete("/spots/{spot_id}")
async def delete_spot(request: Request, spot_id: int):
    """Delete a spot."""
    db = request.app.state.db
    
    success = await db.delete_spot(spot_id)
    if not success:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    return {"message": "Spot deleted"}


@router.post("/spots/{spot_id}/check")
async def check_spot(request: Request, spot_id: int):
    """Run a check on a spot."""
    from app.core.dream_state import DreamStateGenerator
    import asyncio
    
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Get camera snapshot
    camera = HACamera(db_path)
    image_bytes = await camera.get_snapshot(spot.camera_entity)
    
    if not image_bytes:
        raise HTTPException(status_code=500, detail="Failed to get camera snapshot. Check Settings for HA URL and Token.")
    
    # Get memory for context
    memory = await db.get_spot_memory(spot_id)
    
    # Get personality and energy settings
    config = ConfigManager(db_path)
    # Use per-spot personality if set, otherwise fall back to global setting
    personality = spot.personality or await config.get_personality()
    crash_times = await config.get_crash_times()
    low_energy_mode = await config.get_low_energy_mode()
    
    # Check if we're in a crash time
    current_hour = datetime.now().hour
    is_low_energy = config.is_crash_time(current_hour, crash_times)
    
    # Skip check entirely if in skip mode during crash time
    if is_low_energy and low_energy_mode == "skip":
        return {
            "check_id": None,
            "status": "skipped",
            "message": "Check skipped - you're in a low energy period. Take care of yourself!",
            "to_sort": [],
            "looking_good": [],
            "notes": {"main": "Check was skipped during your crash time."},
        }
    
    # Analyze with Gemini
    analyzer = SpotAnalyzer(db_path)
    result = await analyzer.analyze(
        image_bytes=image_bytes,
        spot_name=spot.name,
        definition=spot.definition,
        voice=spot.voice,
        custom_voice_prompt=spot.custom_voice_prompt,
        memory=memory,
        personality=personality,
        is_low_energy=is_low_energy and low_energy_mode == "gentle",
    )
    
    # Calculate XP earned
    xp_earned = 0
    if result.status == "sorted":
        xp_earned = calculate_xp_for_action("sorted", streak_days=spot.current_streak)
    
    result.xp_earned = xp_earned
    
    # Save check result
    check_id = await db.save_check(spot_id, result)
    
    # Update gamification
    if xp_earned > 0:
        await db.add_xp(xp_earned)
    
    # If needs_attention, reset streak
    if result.status == "needs_attention":
        await db.update_spot(spot_id, current_streak=0)
    
    # Check if this is the first check and no dream state exists
    # Trigger dream state generation in background
    is_first_check = memory.total_checks == 0
    has_no_dream_state = not spot.dream_state_image and not spot.dream_state_generating
    
    if is_first_check and has_no_dream_state:
        # Start dream state generation asynchronously (fire and forget)
        async def generate_dream_state_background():
            try:
                await db.update_spot(spot_id, dream_state_generating=True)
                generator = DreamStateGenerator(db_path=db_path, data_dir=DATA_DIR)
                dream_image_path, error_msg = await generator.generate_dream_state(
                    image_bytes=image_bytes,
                    spot_name=spot.name,
                    spot_type=spot.spot_type,
                )
                if dream_image_path:
                    await db.update_spot(
                        spot_id,
                        dream_state_image=dream_image_path,
                        dream_state_generating=False
                    )
                else:
                    # Generation failed but logged, just mark as not generating
                    logger.info(f"Dream state generation failed for spot {spot_id}: {error_msg}")
                    await db.update_spot(spot_id, dream_state_generating=False)
            except Exception as e:
                # Log unexpected errors
                logger.error(f"Background dream state generation error: {e}", exc_info=True)
                await db.update_spot(spot_id, dream_state_generating=False)
        
        # Create background task
        asyncio.create_task(generate_dream_state_background())
    
    # Build rich response
    response = {
        "check_id": check_id,
        "status": result.status,
        "to_sort": result.to_sort,
        "looking_good": result.looking_good,
        "notes": result.notes,
        "error_message": result.error_message,
        "api_response_time": result.api_response_time,
        "xp_earned": xp_earned,
    }
    
    # Add rich analysis if available
    if result.rich_analysis:
        response["rich_analysis"] = {
            "items_out_of_place": result.rich_analysis.items_out_of_place,
            "quick_wins": result.rich_analysis.quick_wins,
            "time_estimate": result.rich_analysis.time_estimate,
            "one_thing_focus": result.rich_analysis.one_thing_focus,
            "personality_message": result.rich_analysis.personality_message,
        }
    
    return response


@router.post("/spots/{spot_id}/check-upload")
async def check_spot_with_upload(request: Request, spot_id: int, image: UploadFile = File(...)):
    """Run a check on a spot using an uploaded image instead of a camera snapshot."""
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Validate file type
    if not image.content_type or not image.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Read image bytes
    image_bytes = await image.read()
    
    # Check file size
    if len(image_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
    
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")
    
    # Get memory for context
    memory = await db.get_spot_memory(spot_id)
    
    # Get personality and energy settings
    config = ConfigManager(db_path)
    # Use per-spot personality if set, otherwise fall back to global setting
    personality = spot.personality or await config.get_personality()
    crash_times = await config.get_crash_times()
    low_energy_mode = await config.get_low_energy_mode()
    
    # Check if we're in a crash time
    current_hour = datetime.now().hour
    is_low_energy = config.is_crash_time(current_hour, crash_times)
    
    # Skip check entirely if in skip mode during crash time
    if is_low_energy and low_energy_mode == "skip":
        return {
            "check_id": None,
            "status": "skipped",
            "message": "Check skipped - you're in a low energy period. Take care of yourself!",
            "to_sort": [],
            "looking_good": [],
            "notes": {"main": "Check was skipped during your crash time."},
        }
    
    # Analyze with Gemini
    analyzer = SpotAnalyzer(db_path)
    result = await analyzer.analyze(
        image_bytes=image_bytes,
        spot_name=spot.name,
        definition=spot.definition,
        voice=spot.voice,
        custom_voice_prompt=spot.custom_voice_prompt,
        memory=memory,
        personality=personality,
        is_low_energy=is_low_energy and low_energy_mode == "gentle",
    )
    
    # Calculate XP earned
    xp_earned = 0
    if result.status == "sorted":
        xp_earned = calculate_xp_for_action("sorted", streak_days=spot.current_streak)
    
    result.xp_earned = xp_earned
    
    # Save check result
    check_id = await db.save_check(spot_id, result)
    
    # Update gamification
    if xp_earned > 0:
        await db.add_xp(xp_earned)
    
    # If needs_attention, reset streak
    if result.status == "needs_attention":
        await db.update_spot(spot_id, current_streak=0)
    
    # Trigger automatic dream state generation if this is first check and no dream state
    is_first_check = memory.total_checks == 0
    has_no_dream_state = not spot.dream_state_image and not spot.dream_state_generating
    
    if is_first_check and has_no_dream_state:
        # Start dream state generation asynchronously (fire and forget)
        async def generate_dream_state_background():
            try:
                await db.update_spot(spot_id, dream_state_generating=True)
                from app.core.dream_state import DreamStateGenerator
                generator = DreamStateGenerator(db_path=db_path, data_dir=DATA_DIR)
                dream_image_path, error_msg = await generator.generate_dream_state(
                    image_bytes=image_bytes,
                    spot_name=spot.name,
                    spot_type=spot.spot_type,
                )
                if dream_image_path:
                    await db.update_spot(
                        spot_id,
                        dream_state_image=dream_image_path,
                        dream_state_generating=False
                    )
                else:
                    # Generation failed but logged, just mark as not generating
                    logger.info(f"Dream state generation failed for spot {spot_id}: {error_msg}")
                    await db.update_spot(spot_id, dream_state_generating=False)
            except Exception as e:
                # Log unexpected errors
                logger.error(f"Background dream state generation error: {e}", exc_info=True)
                await db.update_spot(spot_id, dream_state_generating=False)
        
        # Create background task
        import asyncio
        asyncio.create_task(generate_dream_state_background())
    
    # Build rich response
    response = {
        "check_id": check_id,
        "status": result.status,
        "to_sort": result.to_sort,
        "looking_good": result.looking_good,
        "notes": result.notes,
        "error_message": result.error_message,
        "api_response_time": result.api_response_time,
        "xp_earned": xp_earned,
    }
    
    # Add rich analysis if available
    if result.rich_analysis:
        response["rich_analysis"] = {
            "items_out_of_place": result.rich_analysis.items_out_of_place,
            "quick_wins": result.rich_analysis.quick_wins,
            "time_estimate": result.rich_analysis.time_estimate,
            "one_thing_focus": result.rich_analysis.one_thing_focus,
            "personality_message": result.rich_analysis.personality_message,
        }
    
    return response


@router.post("/spots/{spot_id}/reset")
async def reset_spot(request: Request, spot_id: int, image: UploadFile = File(...)):
    """Mark a spot as fixed (reset) - requires photo verification."""
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Validate file type
    if not image.content_type or not image.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Read image bytes
    image_bytes = await image.read()
    
    # Check file size
    if len(image_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
    
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")
    
    # Get memory for context
    memory = await db.get_spot_memory(spot_id)
    
    # Get last check to calculate quick reset bonus
    recent_checks = await db.get_recent_checks(spot_id, limit=1)
    minutes_since_check = None
    
    if recent_checks:
        try:
            last_check_time = datetime.fromisoformat(recent_checks[0]["timestamp"])
            minutes_since_check = int((datetime.utcnow() - last_check_time).total_seconds() / 60)
        except (ValueError, KeyError):
            pass
    
    # Get personality and energy settings
    config = ConfigManager(db_path)
    personality = spot.personality or await config.get_personality()
    crash_times = await config.get_crash_times()
    low_energy_mode = await config.get_low_energy_mode()
    
    # Check if we're in a crash time
    current_hour = datetime.now().hour
    is_low_energy = config.is_crash_time(current_hour, crash_times)
    
    # Verify with AI that the space is actually tidier
    analyzer = SpotAnalyzer(db_path)
    result = await analyzer.analyze(
        image_bytes=image_bytes,
        spot_name=spot.name,
        definition=spot.definition,
        voice=spot.voice,
        custom_voice_prompt=spot.custom_voice_prompt,
        memory=memory,
        personality=personality,
        is_low_energy=is_low_energy and low_energy_mode == "gentle",
    )
    
    # Save the verification check
    check_id = await db.save_check(spot_id, result)
    
    # Only award XP if the space is actually sorted/tidier
    xp_earned = 0
    if result.status == "sorted":
        # Record the reset
        await db.record_reset(spot_id)
        
        # Fetch the updated spot to get the correct streak value
        updated_spot = await db.get_spot(spot_id)
        
        # Calculate XP for reset
        xp_earned = calculate_xp_for_action(
            "reset",
            streak_days=updated_spot.current_streak,
            minutes_since_check=minutes_since_check
        )
        
        # Add XP
        gamification_state = await db.add_xp(xp_earned)
        
        # Check for achievements
        session_resets = await db.increment_session_resets()
        achievements_unlocked = []
        
        for achievement_id in ["first_blood", "streak_3", "streak_7", "streak_30", 
                               "speed_demon", "multi_spot", "centurion"]:
            if check_achievement_unlock(
                achievement_id,
                streak_days=updated_spot.current_streak,
                total_resets=updated_spot.total_resets,
                reset_time=datetime.now(),
                minutes_since_check=minutes_since_check,
                spots_reset_in_session=session_resets,
            ):
                if await db.unlock_achievement(achievement_id):
                    achievements_unlocked.append(achievement_id)
                    # Add achievement XP bonus
                    bonus_xp = ACHIEVEMENTS[achievement_id]["xp_bonus"]
                    await db.add_xp(bonus_xp)
                    xp_earned += bonus_xp
        
        return {
            "message": "Spot verified as tidy! Great work!",
            "status": "sorted",
            "new_streak": updated_spot.current_streak,
            "xp_earned": xp_earned,
            "level": gamification_state["level"],
            "achievements_unlocked": achievements_unlocked,
            "check_id": check_id,
        }
    else:
        # Space is not tidy yet - no XP awarded, reset streak for any non-sorted status
        await db.update_spot(spot_id, current_streak=0)
        
        return {
            "message": "The space isn't tidy yet. Keep working on it!",
            "status": result.status,
            "to_sort": result.to_sort,
            "looking_good": result.looking_good,
            "notes": result.notes,
            "xp_earned": 0,
            "check_id": check_id,
        }


@router.post("/spots/{spot_id}/snooze")
async def snooze_spot(request: Request, spot_id: int, data: SnoozeRequest):
    """Snooze a spot for N minutes."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    snoozed_until = (datetime.utcnow() + timedelta(minutes=data.minutes)).isoformat()
    await db.update_spot(spot_id, snoozed_until=snoozed_until, status="snoozed")
    
    return {"message": f"Snoozed for {data.minutes} minutes", "until": snoozed_until}


@router.post("/spots/{spot_id}/unsnooze")
async def unsnooze_spot(request: Request, spot_id: int):
    """Cancel snooze on a spot."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    await db.update_spot(spot_id, snoozed_until=None, status="unknown")
    
    return {"message": "Snooze cancelled"}


# History endpoints
class UpdateCheckNotesRequest(BaseModel):
    notes: Optional[str] = None


class MarkItemSortedRequest(BaseModel):
    """Request to mark a specific item as sorted."""
    item_index: int  # Index of the item in the to_sort list


@router.post("/spots/{spot_id}/mark-item-sorted")
async def mark_item_sorted(request: Request, spot_id: int, data: MarkItemSortedRequest):
    """Mark a specific item from the to_sort list as sorted.
    
    This contributes to the streak when items are checked off!
    """
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Get the latest check
    recent_checks = await db.get_recent_checks(spot_id, limit=1)
    if not recent_checks:
        raise HTTPException(status_code=400, detail="No check found to update")
    
    latest_check = recent_checks[0]
    to_sort = latest_check.get("to_sort", [])
    
    if data.item_index < 0 or data.item_index >= len(to_sort):
        raise HTTPException(status_code=400, detail="Invalid item index")
    
    # Mark item as sorted
    item = to_sort[data.item_index]
    if isinstance(item, dict):
        item["sorted"] = True
    else:
        to_sort[data.item_index] = {"item": item, "sorted": True}
    
    # Update the check with the modified to_sort list
    to_sort_json = json.dumps(to_sort)
    await db.conn.execute(
        "UPDATE checks SET to_sort_json = ? WHERE id = ?",
        (to_sort_json, latest_check["id"])
    )
    await db.conn.commit()
    
    # Calculate XP for sorting an item (small reward)
    xp_earned = 10  # XP per item sorted
    gamification_state = await db.add_xp(xp_earned)
    
    # Check if all items are now sorted
    all_sorted = all(
        (isinstance(i, dict) and i.get("sorted", False)) 
        for i in to_sort
    )
    
    if all_sorted:
        # Bonus XP for sorting all items!
        bonus_xp = 25
        await db.add_xp(bonus_xp)
        xp_earned += bonus_xp
        
        # Update spot status to sorted
        await db.record_reset(spot_id)
    
    return {
        "success": True,
        "xp_earned": xp_earned,
        "all_sorted": all_sorted,
        "message": "Item marked as sorted!" + (" 🎉 All items done!" if all_sorted else "")
    }


@router.get("/spots/{spot_id}/history")
async def get_spot_history(request: Request, spot_id: int, page: int = 1, per_page: int = 20):
    """Get paginated check history for a spot."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Clamp per_page
    per_page = min(max(per_page, 1), 100)
    page = max(page, 1)
    
    checks, total = await db.get_checks_paginated(spot_id, page, per_page)
    
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    
    return {
        "checks": checks,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1
        }
    }


@router.get("/spots/{spot_id}/history/graph")
async def get_spot_history_graph(request: Request, spot_id: int, days: int = 30):
    """Get check history data for graphing."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    days = min(max(days, 7), 90)  # Clamp between 7 and 90 days
    graph_data = await db.get_checks_for_graph(spot_id, days)
    
    return {
        "data": graph_data,
        "days": days
    }


@router.delete("/spots/{spot_id}/checks/{check_id}")
async def delete_check(request: Request, spot_id: int, check_id: int):
    """Delete a specific check entry."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Verify check belongs to this spot
    check = await db.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    if check["spot_id"] != spot_id:
        raise HTTPException(status_code=400, detail="Check does not belong to this spot")
    
    await db.delete_check(check_id)
    return {"message": "Check deleted"}


@router.put("/spots/{spot_id}/checks/{check_id}")
async def update_check(request: Request, spot_id: int, check_id: int, data: UpdateCheckNotesRequest):
    """Update notes on a specific check entry."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    check = await db.get_check(check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    if check["spot_id"] != spot_id:
        raise HTTPException(status_code=400, detail="Check does not belong to this spot")
    
    await db.update_check_notes(check_id, data.notes)
    return {"message": "Check updated"}


@router.delete("/spots/{spot_id}/history")
async def clear_spot_history(request: Request, spot_id: int):
    """Clear all check history for a spot."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    deleted_count = await db.clear_spot_history(spot_id)
    return {"message": f"Deleted {deleted_count} check(s)", "deleted_count": deleted_count}


@router.post("/check-all")
async def check_all_spots(request: Request):
    """Check all spots."""
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    spots = await db.get_all_spots()
    
    results = []
    camera = HACamera(db_path)
    analyzer = SpotAnalyzer(db_path)
    
    for spot in spots:
        # Skip snoozed spots
        if spot.snoozed_until:
            try:
                snoozed_until = datetime.fromisoformat(spot.snoozed_until)
                if snoozed_until > datetime.utcnow():
                    results.append({"spot_id": spot.id, "status": "snoozed"})
                    continue
            except ValueError:
                pass
        
        # Get snapshot
        image_bytes = await camera.get_snapshot(spot.camera_entity)
        if not image_bytes:
            results.append({"spot_id": spot.id, "status": "error", "error": "Failed to get snapshot"})
            continue
        
        # Get memory
        memory = await db.get_spot_memory(spot.id)
        
        # Analyze
        result = await analyzer.analyze(
            image_bytes=image_bytes,
            spot_name=spot.name,
            definition=spot.definition,
            voice=spot.voice,
            custom_voice_prompt=spot.custom_voice_prompt,
            memory=memory,
        )
        
        # Save
        await db.save_check(spot.id, result)
        
        if result.status == "needs_attention":
            await db.update_spot(spot.id, current_streak=0)
        
        results.append({
            "spot_id": spot.id,
            "status": result.status,
            "to_sort_count": len(result.to_sort),
        })
    
    return {"results": results}


# Cameras
@router.get("/cameras")
async def list_cameras(request: Request):
    """List available cameras from Home Assistant."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    camera = HACamera(db_path)
    cameras = await camera.get_cameras()
    
    return {
        "cameras": [
            {"entity_id": c.entity_id, "name": c.name, "state": c.state}
            for c in cameras
        ]
    }


@router.get("/cameras/{entity_id}/preview")
async def get_camera_preview(request: Request, entity_id: str):
    """Get a camera preview/snapshot for the add spot form."""
    from fastapi.responses import Response
    
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    camera = HACamera(db_path)
    image_bytes, error = await camera.get_snapshot_with_error(entity_id)
    
    if image_bytes:
        return Response(content=image_bytes, media_type="image/jpeg")
    
    error_msg = str(error) if error else "Failed to get camera snapshot"
    raise HTTPException(status_code=500, detail=error_msg)


@router.get("/cameras/{entity_id}/test")
async def test_camera_connection(request: Request, entity_id: str):
    """Test camera connection and return diagnostics."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    camera = HACamera(db_path)
    result = await camera.test_connection(entity_id)
    
    return {
        "success": result.success,
        "response_time_ms": result.response_time_ms,
        "error": {
            "type": result.error.error_type,
            "message": result.error.message,
            "status_code": result.error.status_code
        } if result.error else None
    }


# Spot types and templates
@router.get("/spot-types")
async def get_spot_types():
    """Get spot types with their templates."""
    return {
        "types": [
            {"key": key, "template": template}
            for key, template in SPOT_TEMPLATES.items()
        ]
    }


# Voices
@router.get("/voices")
async def get_voices():
    """Get available voices."""
    return {"voices": get_all_voices()}


# Personalities
@router.get("/personalities")
async def get_personalities():
    """Get available AI personalities."""
    return {"personalities": get_all_personalities()}


# Gamification
@router.get("/gamification")
async def get_gamification_state(request: Request):
    """Get current gamification state (XP, level, achievements)."""
    db = request.app.state.db
    state = await db.get_gamification_state()
    daily_challenge = get_daily_challenge()
    
    return {
        **state,
        "daily_challenge": daily_challenge,
        "all_achievements": get_all_achievements(),
        "all_levels": get_all_levels(),
    }


@router.get("/gamification/daily-challenge")
async def get_current_daily_challenge():
    """Get today's daily challenge."""
    return {"challenge": get_daily_challenge()}


@router.post("/gamification/complete-daily-challenge")
async def complete_daily_challenge(request: Request):
    """Mark today's daily challenge as complete."""
    db = request.app.state.db
    
    # Get challenge info for XP reward
    challenge = get_daily_challenge()
    
    newly_completed = await db.complete_daily_challenge()
    
    if newly_completed:
        # Add XP reward
        xp_reward = challenge.get("xp_reward", 100)
        await db.add_xp(xp_reward)
        return {"success": True, "xp_earned": xp_reward, "message": f"Challenge completed! +{xp_reward} XP"}
    
    return {"success": False, "message": "Challenge already completed today"}


# Settings - All in one place
@router.get("/settings")
async def get_settings(request: Request):
    """Get current settings including HA URL, has_token, has_gemini_key, personality, energy settings."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    config = ConfigManager(db_path)
    
    ha_url = await config.get_ha_url()
    has_ha_token = await config.has_setting(ConfigManager.HA_TOKEN)
    has_gemini_key = await config.has_setting(ConfigManager.GEMINI_API_KEY)
    has_huggingface_key = await config.has_setting(ConfigManager.HUGGINGFACE_API_KEY)
    has_deepai_key = await config.has_setting(ConfigManager.DEEPAI_API_KEY)
    has_replicate_key = await config.has_setting(ConfigManager.REPLICATE_API_KEY)
    
    # Check for environment fallbacks
    if not has_gemini_key:
        has_gemini_key = bool(os.environ.get("GEMINI_API_KEY", ""))
    
    if not has_ha_token:
        has_ha_token = bool(os.environ.get("SUPERVISOR_TOKEN", ""))
    
    if not has_huggingface_key:
        has_huggingface_key = bool(os.environ.get("HUGGINGFACE_API_KEY", ""))
    
    if not has_deepai_key:
        has_deepai_key = bool(os.environ.get("DEEPAI_API_KEY", ""))
    
    if not has_replicate_key:
        has_replicate_key = bool(os.environ.get("REPLICATE_API_TOKEN", ""))
    
    # Get new settings
    personality = await config.get_personality()
    energy_rhythm = await config.get_energy_rhythm()
    crash_times = await config.get_crash_times()
    low_energy_mode = await config.get_low_energy_mode()
    
    return {
        "ha_url": ha_url or "",
        "has_ha_token": has_ha_token,
        "has_gemini_key": has_gemini_key,
        "has_huggingface_key": has_huggingface_key,
        "has_deepai_key": has_deepai_key,
        "has_replicate_key": has_replicate_key,
        "mode": "addon" if os.environ.get("SUPERVISOR_TOKEN") else "standalone",
        "personality": personality,
        "energy_rhythm": energy_rhythm,
        "crash_times": crash_times,
        "low_energy_mode": low_energy_mode,
    }


@router.post("/settings")
async def save_settings(request: Request, data: SaveSettingsRequest):
    """Save HA URL, HA Token, Gemini Key, Personality, Energy settings - ALL AT ONCE."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    config = ConfigManager(db_path)
    
    saved = []
    
    try:
        if data.ha_url is not None:
            await config.set_ha_url(data.ha_url)
            saved.append("ha_url")
        
        if data.ha_token is not None and data.ha_token.strip():
            await config.set_ha_token(data.ha_token)
            saved.append("ha_token")
        
        if data.gemini_api_key is not None and data.gemini_api_key.strip():
            await config.set_gemini_api_key(data.gemini_api_key)
            saved.append("gemini_api_key")
        
        if data.huggingface_api_key is not None and data.huggingface_api_key.strip():
            await config.set_huggingface_api_key(data.huggingface_api_key)
            saved.append("huggingface_api_key")
        
        if data.deepai_api_key is not None and data.deepai_api_key.strip():
            await config.set_deepai_api_key(data.deepai_api_key)
            saved.append("deepai_api_key")
        
        if data.replicate_api_key is not None and data.replicate_api_key.strip():
            await config.set_replicate_api_key(data.replicate_api_key)
            saved.append("replicate_api_key")
        
        if data.personality is not None:
            await config.set_personality(data.personality)
            saved.append("personality")
        
        if data.energy_rhythm is not None:
            await config.set_energy_rhythm(data.energy_rhythm)
            saved.append("energy_rhythm")
        
        if data.crash_times is not None:
            await config.set_crash_times(data.crash_times)
            saved.append("crash_times")
        
        if data.low_energy_mode is not None:
            await config.set_low_energy_mode(data.low_energy_mode)
            saved.append("low_energy_mode")
        
        return {
            "success": True,
            "message": f"Settings saved: {', '.join(saved) if saved else 'none'}",
            "saved": saved
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {str(e)}")



@router.post("/settings/test-ha")
async def test_ha_connection(request: Request):
    """Test Home Assistant connection."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    camera = HACamera(db_path)
    
    # Invalidate cache to use fresh settings
    camera.invalidate_credentials_cache()
    
    result = await camera.test_connection()
    
    if result.success:
        # Also try to get camera count
        cameras = await camera.get_cameras()
        return {
            "success": True,
            "message": f"Connected! Found {len(cameras)} cameras.",
            "camera_count": len(cameras),
            "response_time_ms": result.response_time_ms
        }
    else:
        return {
            "success": False,
            "message": result.error.message if result.error else "Connection failed",
            "error_type": result.error.error_type if result.error else "unknown"
        }


@router.post("/settings/test-gemini")
async def test_gemini_key(request: Request):
    """Test Gemini API key."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    analyzer = SpotAnalyzer(db_path)
    
    # Invalidate cache to use fresh settings
    analyzer.invalidate_api_key_cache()
    
    valid = await analyzer.validate_api_key()
    
    return {
        "success": valid,
        "message": "Gemini API key is valid" if valid else "Invalid or missing API key"
    }


@router.post("/settings/validate-key")
async def validate_api_key(request: Request):
    """Validate the Gemini API key (legacy endpoint for compatibility)."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    analyzer = SpotAnalyzer(db_path)
    valid = await analyzer.validate_api_key()

    return {"valid": valid}


class HATokenRequest(BaseModel):
    token: str


@router.post("/settings/ha-token")
async def save_ha_token(request: Request, data: HATokenRequest):
    """Save HA token for camera access (legacy endpoint for compatibility)."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    config = ConfigManager(db_path)
    
    try:
        await config.set_ha_token(data.token)
        
        # Test the token works
        camera = HACamera(db_path)
        camera.invalidate_credentials_cache()
        cameras = await camera.get_cameras()
        
        return {
            "success": True, 
            "message": f"Token saved. Found {len(cameras)} cameras."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Photo Upload
@router.post("/spots/{spot_id}/upload")
async def upload_photo(request: Request, spot_id: int, image: UploadFile = File(...)):
    """Upload photo from phone camera and run check.
    
    Accepts JPEG or PNG images up to 10MB.
    """
    db = request.app.state.db
    
    # Validate spot exists
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Validate file type
    content_type = image.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Read image data with size limit
    image_bytes = await image.read()
    if len(image_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
    
    if len(image_bytes) < 100:
        raise HTTPException(status_code=400, detail="Image file is empty or too small")
    
    # Get memory for context
    memory = await db.get_spot_memory(spot_id)
    
    # Analyze with Gemini
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    analyzer = SpotAnalyzer(db_path)
    result = await analyzer.analyze(
        image_bytes=image_bytes,
        spot_name=spot.name,
        definition=spot.definition,
        voice=spot.voice,
        custom_voice_prompt=spot.custom_voice_prompt,
        memory=memory,
    )
    
    # Save check result
    check_id = await db.save_check(spot_id, result)
    
    # If needs_attention, reset streak
    if result.status == "needs_attention":
        await db.update_spot(spot_id, current_streak=0)
    
    return {
        "check_id": check_id,
        "status": result.status,
        "to_sort": result.to_sort,
        "looking_good": result.looking_good,
        "notes": result.notes,
        "error_message": result.error_message,
        "api_response_time": result.api_response_time,
    }


# Bulk Operations
class BulkSpotIdsRequest(BaseModel):
    spot_ids: list[int]


class BulkSnoozeRequest(BaseModel):
    spot_ids: list[int]
    minutes: int = 30


@router.post("/bulk/reset")
async def bulk_reset_spots(request: Request, data: BulkSpotIdsRequest):
    """Reset multiple spots (mark as fixed)."""
    db = request.app.state.db
    
    results = []
    for spot_id in data.spot_ids:
        spot = await db.get_spot(spot_id)
        if spot:
            await db.record_reset(spot_id)
            updated_spot = await db.get_spot(spot_id)
            results.append({
                "spot_id": spot_id,
                "success": True,
                "new_streak": updated_spot.current_streak
            })
        else:
            results.append({
                "spot_id": spot_id,
                "success": False,
                "error": "Spot not found"
            })
    
    return {"results": results}


@router.post("/bulk/snooze")
async def bulk_snooze_spots(request: Request, data: BulkSnoozeRequest):
    """Snooze multiple spots."""
    db = request.app.state.db
    
    snoozed_until = (datetime.utcnow() + timedelta(minutes=data.minutes)).isoformat()
    results = []
    
    for spot_id in data.spot_ids:
        spot = await db.get_spot(spot_id)
        if spot:
            await db.update_spot(spot_id, snoozed_until=snoozed_until, status="snoozed")
            results.append({
                "spot_id": spot_id,
                "success": True,
                "snoozed_until": snoozed_until
            })
        else:
            results.append({
                "spot_id": spot_id,
                "success": False,
                "error": "Spot not found"
            })
    
    return {"results": results}


@router.delete("/bulk/spots")
async def bulk_delete_spots(request: Request, data: BulkSpotIdsRequest):
    """Delete multiple spots."""
    db = request.app.state.db
    
    results = []
    for spot_id in data.spot_ids:
        success = await db.delete_spot(spot_id)
        results.append({
            "spot_id": spot_id,
            "success": success
        })
    
    return {"results": results}


@router.post("/reset-all-needing-attention")
async def reset_all_needing_attention(request: Request):
    """Reset all spots that need attention."""
    db = request.app.state.db
    spots = await db.get_all_spots()
    
    results = []
    for spot in spots:
        if spot.status.value == "needs_attention":
            await db.record_reset(spot.id)
            updated_spot = await db.get_spot(spot.id)
            results.append({
                "spot_id": spot.id,
                "name": spot.name,
                "new_streak": updated_spot.current_streak
            })
    
    return {"reset_count": len(results), "results": results}


# Schedule endpoints
@router.get("/spots/{spot_id}/schedule")
async def get_spot_schedule(request: Request, spot_id: int):
    """Get the check schedule for a spot."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    schedule = None
    if spot.check_schedule:
        try:
            schedule = json.loads(spot.check_schedule)
        except (json.JSONDecodeError, TypeError):
            schedule = None
    
    return {"schedule": schedule or []}


@router.put("/spots/{spot_id}/schedule")
async def update_spot_schedule(request: Request, spot_id: int, data: UpdateScheduleRequest):
    """Update the check schedule for a spot."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Validate schedule items
    valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    for item in data.schedule:
        # Validate time format
        try:
            parts = item.time.split(":")
            if len(parts) != 2:
                raise ValueError("Invalid time format")
            hour = int(parts[0])
            minute = int(parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Invalid time values")
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail=f"Invalid time format '{item.time}'. Use HH:MM format.")
        
        # Validate days
        for day in item.days:
            if day.lower() not in valid_days:
                raise HTTPException(status_code=400, detail=f"Invalid day '{day}'. Use: mon, tue, wed, thu, fri, sat, sun")
    
    # Convert to JSON and save
    schedule_json = json.dumps([{"time": item.time, "days": item.days} for item in data.schedule])
    await db.update_spot(spot_id, check_schedule=schedule_json)
    
    return {"message": "Schedule updated", "schedule": data.schedule}


@router.delete("/spots/{spot_id}/schedule")
async def clear_spot_schedule(request: Request, spot_id: int):
    """Clear the check schedule for a spot."""
    db = request.app.state.db
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    await db.update_spot(spot_id, check_schedule=None)
    
    return {"message": "Schedule cleared"}


@router.get("/scheduled-spots")
async def get_scheduled_spots(request: Request):
    """Get all spots that have schedules configured."""
    db = request.app.state.db
    spots = await db.get_all_spots()
    
    scheduled = []
    for spot in spots:
        if spot.check_schedule:
            try:
                schedule = json.loads(spot.check_schedule)
                if schedule:
                    scheduled.append({
                        "id": spot.id,
                        "name": spot.name,
                        "camera_entity": spot.camera_entity,
                        "schedule": schedule,
                        "last_check": spot.last_check,
                        "status": spot.status.value if isinstance(spot.status, SpotStatus) else spot.status
                    })
            except (json.JSONDecodeError, TypeError):
                pass
    
    return {"spots": scheduled}


# Wizard endpoints
@router.get("/wizard/status")
async def get_wizard_status(request: Request):
    """Check if the setup wizard has been completed."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    config = ConfigManager(db_path)
    
    # Check if wizard has been completed
    wizard_completed = await config.get_setting("wizard_completed")
    
    # Also check if there are any spots (as a fallback for existing users)
    db = request.app.state.db
    spots = await db.get_all_spots()
    has_spots = len(spots) > 0
    
    # Check if connections are configured
    has_ha_token = await config.has_setting(ConfigManager.HA_TOKEN) or bool(os.environ.get("SUPERVISOR_TOKEN", ""))
    has_gemini_key = await config.has_setting(ConfigManager.GEMINI_API_KEY) or bool(os.environ.get("GEMINI_API_KEY", ""))
    
    return {
        "wizard_completed": wizard_completed == "true" or has_spots,
        "has_spots": has_spots,
        "has_ha_token": has_ha_token,
        "has_gemini_key": has_gemini_key,
    }


@router.post("/wizard/complete")
async def complete_wizard(request: Request):
    """Mark the setup wizard as completed."""
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    config = ConfigManager(db_path)
    
    await config.set_setting("wizard_completed", "true")
    
    return {"success": True, "message": "Wizard completed"}


@router.get("/cameras/discover")
async def discover_cameras(request: Request):
    """Discover cameras from Home Assistant with live previews.
    
    Returns list of cameras with entity_id, name, state, and a preview URL.
    """
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    camera = HACamera(db_path)
    
    cameras = await camera.get_cameras()
    
    return {
        "cameras": [
            {
                "entity_id": c.entity_id,
                "name": c.name,
                "state": c.state,
                "preview_url": f"/api/cameras/{c.entity_id}/preview",
                # Suggest spot type based on camera name
                "suggested_type": suggest_spot_type(c.name, c.entity_id),
            }
            for c in cameras
        ]
    }


def suggest_spot_type(name: str, entity_id: str) -> str:
    """Suggest a spot type based on camera name or entity_id."""
    combined = (name + " " + entity_id).lower()
    
    if any(term in combined for term in ["desk", "office", "work", "study"]):
        return "work"
    elif any(term in combined for term in ["kitchen", "cook", "fridge"]):
        return "kitchen"
    elif any(term in combined for term in ["bed", "sleep", "bedroom"]):
        return "sleep"
    elif any(term in combined for term in ["living", "lounge", "couch", "tv", "chill"]):
        return "chill"
    elif any(term in combined for term in ["entry", "door", "hall", "foyer"]):
        return "entryway"
    elif any(term in combined for term in ["garage", "storage", "closet", "basement"]):
        return "storage"
    
    return "custom"


# Dream State endpoints
@router.post("/spots/{spot_id}/generate-dream-state")
async def generate_dream_state(request: Request, spot_id: int):
    """Generate a dream state image for a spot using the latest snapshot.
    
    This creates an AI-generated aspirational image showing how the spot
    could look when perfectly tidy and organized.
    """
    from fastapi import BackgroundTasks
    from app.core.dream_state import DreamStateGenerator
    
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Check if already generating
    if spot.dream_state_generating:
        return {
            "status": "generating",
            "message": "Dream state image is already being generated"
        }
    
    # Get camera snapshot
    camera = HACamera(db_path)
    image_bytes = await camera.get_snapshot(spot.camera_entity)
    
    if not image_bytes:
        return {
            "status": "failed",
            "message": "Could not get camera snapshot. Please check your camera connection in settings."
        }
    
    # Mark as generating
    await db.update_spot(spot_id, dream_state_generating=True)
    
    try:
        # Generate dream state image
        generator = DreamStateGenerator(db_path=db_path, data_dir=DATA_DIR)
        dream_image_path, error_msg = await generator.generate_dream_state(
            image_bytes=image_bytes,
            spot_name=spot.name,
            spot_type=spot.spot_type,
        )
        
        if dream_image_path:
            # Save the dream state image path
            await db.update_spot(
                spot_id, 
                dream_state_image=dream_image_path,
                dream_state_generating=False
            )
            return {
                "status": "success",
                "dream_state_image": dream_image_path,
                "message": "Dream state image generated successfully!"
            }
        else:
            # Generation failed with error message
            logger.info(f"Dream state generation failed for spot {spot_id}: {error_msg}")
            await db.update_spot(spot_id, dream_state_generating=False)
            return {
                "status": "failed",
                "message": f"Could not generate dream state: {error_msg}"
            }
    
    except Exception as e:
        # Log unexpected errors
        logger.error(f"Dream state generation error: {e}", exc_info=True)
        await db.update_spot(spot_id, dream_state_generating=False)
        return {
            "status": "failed",
            "message": f"Dream state generation failed: {str(e)}"
        }


@router.get("/dream-states/{filename}")
async def serve_dream_state_image(filename: str):
    """Serve a dream state image file."""
    from fastapi.responses import FileResponse
    from app.core.dream_state import DreamStateGenerator
    
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    generator = DreamStateGenerator(db_path=db_path, data_dir=DATA_DIR)
    
    image_path = generator.get_dream_image_path(f"/dream-states/{filename}")
    
    if not image_path or not image_path.exists():
        raise HTTPException(status_code=404, detail="Dream state image not found")
    
    return FileResponse(
        path=str(image_path),
        media_type="image/jpeg",
        filename=filename
    )


@router.post("/spots/{spot_id}/upload-initial")
async def upload_initial_photo(request: Request, spot_id: int, image: UploadFile = File(...)):
    """Upload an initial photo for a spot and trigger automatic dream state generation.
    
    This endpoint is meant for setting up spots without cameras - user uploads
    a photo as the reference image, and a dream state is automatically generated.
    """
    from app.core.dream_state import DreamStateGenerator
    import asyncio
    
    db = request.app.state.db
    db_path = os.path.join(DATA_DIR, "twinsync.db")
    
    spot = await db.get_spot(spot_id)
    if not spot:
        raise HTTPException(status_code=404, detail="Spot not found")
    
    # Validate file type
    if not image.content_type or not image.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Read image bytes
    image_bytes = await image.read()
    
    # Check file size
    if len(image_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
    
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")
    
    # Check if dream state generation is already in progress
    if spot.dream_state_generating:
        return {
            "status": "generating",
            "message": "Dream state is already being generated"
        }
    
    # Start dream state generation immediately
    await db.update_spot(spot_id, dream_state_generating=True, dream_state_error=None)
    
    # Generate dream state in background
    async def generate_dream_state_background():
        try:
            generator = DreamStateGenerator(db_path=db_path, data_dir=DATA_DIR)
            dream_image_path, error_msg = await generator.generate_dream_state(
                image_bytes=image_bytes,
                spot_name=spot.name,
                spot_type=spot.spot_type,
            )
            if dream_image_path:
                await db.update_spot(
                    spot_id,
                    dream_state_image=dream_image_path,
                    dream_state_generating=False
                )
                logger.info(f"Dream state generated for spot {spot_id}")
            else:
                # Generation failed but logged
                logger.info(f"Dream state generation failed for spot {spot_id}: {error_msg}")
                await db.update_spot(spot_id, dream_state_generating=False)
        except Exception as e:
            # Log unexpected errors
            logger.error(f"Background dream state generation error: {e}", exc_info=True)
            await db.update_spot(spot_id, dream_state_generating=False)
    
    # Create background task
    asyncio.create_task(generate_dream_state_background())
    
    return {
        "status": "generating",
        "message": "Dream state generation started! It will appear on the spot page when ready.",
        "spot_id": spot_id
    }
