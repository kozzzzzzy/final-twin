"""Memory Engine for TwinSync Spot - THE KILLER FEATURE.

This module analyzes check history to detect patterns:
- Recurring items that keep appearing
- Best/worst days
- Usual times when sorted
- Streaks
- Items frequency tracking
- Day and time of day stats
- Pattern insights generation
"""
from collections import Counter
from datetime import datetime
from typing import Optional

from app.core.models import SpotMemory, SpotPatterns, Spot


# How many times an item must appear to be "recurring"
RECURRING_THRESHOLD = 3

# How many days of history to consider
MEMORY_RETENTION_DAYS = 30


class MemoryEngine:
    """Analyzes check history to detect patterns."""
    
    def calculate_memory(self, spot_id: int, checks: list[dict], spot: Optional[Spot] = None) -> SpotMemory:
        """Calculate memory/patterns from check history."""
        if not checks:
            return SpotMemory(
                spot_id=spot_id,
                current_streak=spot.current_streak if spot else 0,
                longest_streak=spot.longest_streak if spot else 0,
            )
        
        # Calculate all pattern data
        items_frequency = self._count_all_items(checks)
        day_stats = self._calculate_day_of_week_stats(checks)
        time_stats = self._calculate_time_of_day_stats(checks)
        insights = self._generate_pattern_insights(checks, items_frequency, day_stats, time_stats)
        
        patterns = SpotPatterns(
            recurring_items=self._count_recurring_items(checks),
            worst_day=self._find_worst_day(checks),
            best_day=self._find_best_day(checks),
            usually_sorted_by=self._find_usual_sorted_time(checks),
            items_frequency=items_frequency,
            day_of_week_stats=day_stats,
            time_of_day_stats=time_stats,
            pattern_insights=insights,
        )
        
        last_check = checks[-1] if checks else None
        
        return SpotMemory(
            spot_id=spot_id,
            patterns=patterns,
            current_streak=spot.current_streak if spot else 0,
            longest_streak=spot.longest_streak if spot else 0,
            total_checks=len(checks),
            last_check_status=last_check["status"] if last_check else None,
        )
    
    def _count_recurring_items(self, checks: list[dict]) -> dict[str, int]:
        """Count how many times each item appears in to_sort."""
        counter = Counter()
        
        for check in checks:
            to_sort = check.get("to_sort", [])
            for item in to_sort:
                # Handle both dict and string formats
                if isinstance(item, dict):
                    item_name = item.get("item", "").lower().strip()
                else:
                    item_name = str(item).lower().strip()
                
                if item_name:
                    counter[item_name] += 1
        
        # Only return items that appear at least RECURRING_THRESHOLD times
        return {item: count for item, count in counter.items() 
                if count >= RECURRING_THRESHOLD}
    
    def _count_all_items(self, checks: list[dict]) -> dict[str, int]:
        """Count frequency of all items (not just recurring ones)."""
        counter = Counter()
        
        for check in checks:
            to_sort = check.get("to_sort", [])
            for item in to_sort:
                if isinstance(item, dict):
                    item_name = item.get("item", "").lower().strip()
                else:
                    item_name = str(item).lower().strip()
                
                if item_name:
                    counter[item_name] += 1
        
        return dict(counter.most_common(20))  # Top 20 items
    
    def _calculate_day_of_week_stats(self, checks: list[dict]) -> dict[str, dict]:
        """Calculate stats broken down by day of week."""
        day_stats = {}
        
        for check in checks:
            try:
                dt = datetime.fromisoformat(check["timestamp"])
                day = dt.strftime("%A")
                
                if day not in day_stats:
                    day_stats[day] = {"sorted": 0, "needs_attention": 0, "total": 0}
                
                day_stats[day]["total"] += 1
                if check.get("status") == "sorted":
                    day_stats[day]["sorted"] += 1
                elif check.get("status") == "needs_attention":
                    day_stats[day]["needs_attention"] += 1
            except (ValueError, KeyError):
                pass
        
        return day_stats
    
    def _calculate_time_of_day_stats(self, checks: list[dict]) -> dict[str, dict]:
        """Calculate stats broken down by time of day."""
        time_stats = {
            "morning": {"sorted": 0, "needs_attention": 0, "total": 0},    # 6-12
            "afternoon": {"sorted": 0, "needs_attention": 0, "total": 0},   # 12-17
            "evening": {"sorted": 0, "needs_attention": 0, "total": 0},     # 17-21
            "night": {"sorted": 0, "needs_attention": 0, "total": 0},       # 21-6
        }
        
        for check in checks:
            try:
                dt = datetime.fromisoformat(check["timestamp"])
                hour = dt.hour
                
                if 6 <= hour < 12:
                    period = "morning"
                elif 12 <= hour < 17:
                    period = "afternoon"
                elif 17 <= hour < 21:
                    period = "evening"
                else:
                    period = "night"
                
                time_stats[period]["total"] += 1
                if check.get("status") == "sorted":
                    time_stats[period]["sorted"] += 1
                elif check.get("status") == "needs_attention":
                    time_stats[period]["needs_attention"] += 1
            except (ValueError, KeyError):
                pass
        
        return time_stats
    
    def _generate_pattern_insights(
        self, 
        checks: list[dict], 
        items_frequency: dict, 
        day_stats: dict, 
        time_stats: dict
    ) -> list[str]:
        """Generate human-readable pattern insights."""
        insights = []
        
        # Insight: Top recurring item
        if items_frequency:
            top_item, count = list(items_frequency.items())[0]
            if count >= 5:
                insights.append(f"'{top_item.title()}' is your #1 usual suspect ({count} times)")
        
        # Insight: Best vs worst day comparison
        best_day = None
        worst_day = None
        best_rate = 0
        worst_rate = 100
        
        for day, stats in day_stats.items():
            if stats["total"] >= 3:  # Need enough data
                rate = (stats["sorted"] / stats["total"]) * 100
                if rate > best_rate:
                    best_rate = rate
                    best_day = day
                if rate < worst_rate:
                    worst_rate = rate
                    worst_day = day
        
        if best_day and worst_day and best_day != worst_day:
            insights.append(f"{best_day}s are your cleanest days ({int(best_rate)}% sorted)")
            insights.append(f"{worst_day}s need more attention ({int(100 - worst_rate)}% messy)")
        
        # Insight: Time of day pattern
        best_time = None
        best_time_rate = 0
        
        for period, stats in time_stats.items():
            if stats["total"] >= 3:
                rate = (stats["sorted"] / stats["total"]) * 100
                if rate > best_time_rate:
                    best_time_rate = rate
                    best_time = period
        
        if best_time and best_time_rate >= 70:
            insights.append(f"You're most tidy in the {best_time}")
        
        # Insight: Streak encouragement
        total_checks = len(checks)
        sorted_checks = sum(1 for c in checks if c.get("status") == "sorted")
        if total_checks >= 5:
            overall_rate = (sorted_checks / total_checks) * 100
            if overall_rate >= 80:
                insights.append(f"Amazing! {int(overall_rate)}% of your checks are sorted!")
            elif overall_rate <= 30:
                insights.append("Let's work on building that tidiness habit!")
        
        return insights[:5]  # Max 5 insights
    
    def _find_worst_day(self, checks: list[dict]) -> Optional[str]:
        """Find the day of week with most 'needs_attention' statuses."""
        day_counts = Counter()
        
        for check in checks:
            if check.get("status") == "needs_attention":
                try:
                    dt = datetime.fromisoformat(check["timestamp"])
                    day_counts[dt.strftime("%A")] += 1
                except (ValueError, KeyError):
                    pass
        
        if not day_counts:
            return None
        
        return day_counts.most_common(1)[0][0]
    
    def _find_best_day(self, checks: list[dict]) -> Optional[str]:
        """Find the day of week with most 'sorted' statuses."""
        day_counts = Counter()
        
        for check in checks:
            if check.get("status") == "sorted":
                try:
                    dt = datetime.fromisoformat(check["timestamp"])
                    day_counts[dt.strftime("%A")] += 1
                except (ValueError, KeyError):
                    pass
        
        if not day_counts:
            return None
        
        return day_counts.most_common(1)[0][0]
    
    def _find_usual_sorted_time(self, checks: list[dict]) -> Optional[str]:
        """Find the usual time when spot is sorted."""
        hour_counts = Counter()
        
        for check in checks:
            if check.get("status") == "sorted":
                try:
                    dt = datetime.fromisoformat(check["timestamp"])
                    hour_counts[dt.hour] += 1
                except (ValueError, KeyError):
                    pass
        
        if not hour_counts:
            return None
        
        most_common_hour = hour_counts.most_common(1)[0][0]
        
        # Format nicely
        if most_common_hour == 0:
            return "midnight"
        elif most_common_hour == 12:
            return "noon"
        elif most_common_hour < 12:
            return f"{most_common_hour}:00 AM"
        else:
            return f"{most_common_hour - 12}:00 PM"
    
    def build_memory_context(self, memory: SpotMemory) -> str:
        """Build context string for AI prompt."""
        lines = []
        
        if memory.total_checks == 0:
            return "First check - no history yet."
        
        lines.append(f"Total checks: {memory.total_checks}")
        
        if memory.last_check_status:
            lines.append(f"Last check: {memory.last_check_status}")
        
        if memory.current_streak > 0:
            lines.append(f"Current streak: {memory.current_streak} days")
        
        if memory.patterns.recurring_items:
            top_items = list(memory.patterns.recurring_items.items())[:3]
            items_str = ", ".join(f"{item} ({count}x)" for item, count in top_items)
            lines.append(f"Recurring items: {items_str}")
        
        if memory.patterns.worst_day:
            lines.append(f"Worst day: {memory.patterns.worst_day}")
        
        if memory.patterns.best_day:
            lines.append(f"Best day: {memory.patterns.best_day}")
        
        if memory.patterns.usually_sorted_by:
            lines.append(f"Usually sorted by: {memory.patterns.usually_sorted_by}")
        
        return "\n".join(lines) if lines else "No patterns detected yet."
    
    def enrich_items_with_recurring(self, items: list, recurring_items: dict[str, int]) -> list:
        """Add recurring flag and count to items based on memory."""
        enriched = []
        
        for item in items:
            if isinstance(item, dict):
                item_name = item.get("item", "").lower().strip()
                if item_name in recurring_items:
                    item["recurring"] = True
                    item["recurrence_count"] = recurring_items[item_name]
                else:
                    item["recurring"] = False
                enriched.append(item)
            else:
                item_name = str(item).lower().strip()
                if item_name in recurring_items:
                    enriched.append({
                        "item": str(item),
                        "recurring": True,
                        "recurrence_count": recurring_items[item_name]
                    })
                else:
                    enriched.append({
                        "item": str(item),
                        "recurring": False
                    })
        
        return enriched
