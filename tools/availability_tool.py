"""
Availability Tool
=================
Mock calendar/scheduling tool used by the Scheduler Agent.
Simulates a corporate calendar system providing open interview slots
and allowing slot booking. In production, this would integrate with
Google Calendar, Outlook, or Calendly APIs.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class AvailabilityTool:
    """
    Mock corporate calendar tool.

    Provides:
      - get_available_slots(num_slots): returns open time slots starting 3 workdays from now
      - book_slot(slot_id, candidate_name): marks a slot as booked
      - get_booked_slots(): returns all booked slots
    """

    # Interviewers pool
    INTERVIEWERS = [
        {"name": "Sarah Chen",         "role": "Engineering Manager"},
        {"name": "Michael Rodriguez",  "role": "Senior Software Engineer"},
        {"name": "Priya Patel",        "role": "Tech Lead"},
        {"name": "James Wilson",       "role": "HR Business Partner"},
        {"name": "Elena Kowalski",     "role": "Principal Engineer"},
    ]

    # Interview mode rotation
    MODES = ["Video", "On-site", "Video", "Phone", "Video"]

    # Available time slots (24h format, in-office hours)
    TIME_SLOTS = ["09:00", "10:30", "12:00", "14:00", "15:30", "16:30"]

    def __init__(self):
        self._booked: Dict[str, dict] = {}  # slot_id → booking info
        self._all_slots: List[dict] = []
        self._generated = False

    def _generate_slots(self, num_slots: int) -> List[dict]:
        """Generate available slots starting 3 workdays from today."""
        slots = []
        base_date = datetime.now() + timedelta(days=3)
        slot_counter = 0
        day_offset = 0

        while len(slots) < num_slots:
            current_date = base_date + timedelta(days=day_offset)
            day_offset += 1

            # Skip weekends
            if current_date.weekday() >= 5:
                continue

            for i, time_str in enumerate(self.TIME_SLOTS):
                if len(slots) >= num_slots:
                    break

                hour, minute = map(int, time_str.split(":"))
                slot_datetime = current_date.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )

                interviewer_idx = slot_counter % len(self.INTERVIEWERS)
                mode_idx = slot_counter % len(self.MODES)

                slot = {
                    "id": f"slot_{slot_counter + 1:03d}",
                    "date": current_date.strftime("%Y-%m-%d"),
                    "time": time_str,
                    "datetime": slot_datetime.strftime("%Y-%m-%d %H:%M"),
                    "day_name": current_date.strftime("%A"),
                    "interviewer": self.INTERVIEWERS[interviewer_idx],
                    "mode": self.MODES[mode_idx],
                    "duration_minutes": 60,
                    "available": True,
                }
                slots.append(slot)
                slot_counter += 1

        return slots

    def get_available_slots(self, num_slots: int = 15) -> List[dict]:
        """
        Return a list of available (unbooked) time slots.

        Args:
            num_slots: How many slots to generate in total

        Returns:
            List of slot dicts with keys: id, date, time, datetime, interviewer, mode, duration_minutes
        """
        if not self._generated:
            self._all_slots = self._generate_slots(max(num_slots, 20))
            self._generated = True

        available = [
            s for s in self._all_slots
            if s["id"] not in self._booked and s.get("available", True)
        ]

        logger.info(f"[AvailabilityTool] {len(available)} slots available out of {len(self._all_slots)} total")
        return available[:num_slots]

    def book_slot(self, slot_id: str, candidate_name: str) -> bool:
        """
        Book a slot for a candidate.

        Args:
            slot_id: The slot ID to book
            candidate_name: Name of the candidate

        Returns:
            True if booking succeeded, False if slot was already taken
        """
        if slot_id in self._booked:
            logger.warning(f"[AvailabilityTool] Slot {slot_id} already booked for {self._booked[slot_id]['candidate']}")
            return False

        self._booked[slot_id] = {
            "candidate": candidate_name,
            "booked_at": datetime.now().isoformat(),
        }
        logger.info(f"[AvailabilityTool] Booked slot {slot_id} for {candidate_name}")
        return True

    def release_slot(self, slot_id: str) -> bool:
        """Release a previously booked slot."""
        if slot_id in self._booked:
            del self._booked[slot_id]
            logger.info(f"[AvailabilityTool] Released slot {slot_id}")
            return True
        return False

    def get_booked_slots(self) -> Dict[str, dict]:
        """Return all currently booked slots."""
        return dict(self._booked)

    def is_slot_available(self, slot_id: str) -> bool:
        """Check if a specific slot is still available."""
        return slot_id not in self._booked

    def __repr__(self):
        return f"<AvailabilityTool booked={len(self._booked)} total_slots={len(self._all_slots)}>"
