import logging
from typing import List
from agents.legacy.base_agent import BaseAgent
from memory.candidate_pool import RankedCandidate, InterviewSlot
from tools.availability_tool import AvailabilityTool

logger = logging.getLogger(__name__)


class SchedulerAgent(BaseAgent):
    """
    Scheduler Agent: Assigns interview slots to top-ranked candidates using
    the AvailabilityTool (mock calendar system).
    """

    def __init__(self):
        super().__init__(
            name="Scheduler Agent",
            description="Assigns interview time slots to top candidates from calendar availability",
        )
        self.availability_tool = AvailabilityTool()

    def run(self, ranked_candidates: List[RankedCandidate], top_n: int = 5) -> List[InterviewSlot]:
        """
        Assign interview slots to the top N candidates.
        Returns a list of InterviewSlot objects.
        """
        # Schedule up to the requested 3–5 candidates; smaller pools remain valid.
        top_n = max(3, min(5, top_n))
        candidates_to_schedule = ranked_candidates[:min(top_n, len(ranked_candidates))]

        logger.info(f"[SchedulerAgent] Scheduling interviews for top {len(candidates_to_schedule)} candidates")

        # Fetch more slots than needed to allow for conflicts
        available_slots = self.availability_tool.get_available_slots(num_slots=top_n * 3)
        booked_slot_ids = set()
        schedule: List[InterviewSlot] = []

        for candidate in candidates_to_schedule:
            assigned_slot = None

            # Find next available, unbooked slot
            for slot in available_slots:
                if slot["id"] not in booked_slot_ids:
                    assigned_slot = slot
                    booked_slot_ids.add(slot["id"])
                    break

            if not assigned_slot:
                logger.warning(f"[SchedulerAgent] No slot available for {candidate.profile.name}")
                continue

            # Book the slot
            self.availability_tool.book_slot(assigned_slot["id"], candidate.profile.name)

            slot_obj = InterviewSlot(
                candidate_name=candidate.profile.name,
                candidate_id=candidate.profile.id,
                slot_datetime=assigned_slot["datetime"],
                slot_date=assigned_slot["date"],
                slot_time=assigned_slot["time"],
                interviewer=assigned_slot["interviewer"]["name"],
                interviewer_role=assigned_slot["interviewer"]["role"],
                mode=assigned_slot["mode"],
                duration_minutes=assigned_slot["duration_minutes"],
                meeting_link=None,
                rank=candidate.rank,
                score=candidate.match.score,
            )
            schedule.append(slot_obj)
            logger.info(
                f"[SchedulerAgent] Booked: {candidate.profile.name} → {assigned_slot['date']} {assigned_slot['time']} ({assigned_slot['mode']}) with {assigned_slot['interviewer']['name']}"
            )

        return schedule
