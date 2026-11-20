"""In-process store for review runs (progress stages + result)."""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
REVIEWS: Dict[str, Dict[str, Any]] = {}
MAX_REVIEWS = 60
REVIEW_TTL_SECONDS = 60 * 60 * 6


def create_review(owner_id: int, job_id: str, ai_mode: str = "local") -> str:
    review_id = uuid.uuid4().hex
    now = time.time()
    with _LOCK:
        _prune(now)
        REVIEWS[review_id] = {
            "id": review_id,
            "owner_id": owner_id,
            "job_id": job_id,
            "status": "running",
            "stages": [],
            "result": None,
            "error": "",
            "ai_mode": ai_mode,
            "created_at": now,
            "payload": None,
        }
    return review_id


def _prune(now: float) -> None:
    expired = [
        key for key, value in REVIEWS.items()
        if now - value.get("created_at", now) > REVIEW_TTL_SECONDS
    ]
    for key in expired:
        REVIEWS.pop(key, None)
    while len(REVIEWS) > MAX_REVIEWS:
        oldest = min(REVIEWS, key=lambda key: REVIEWS[key].get("created_at", 0))
        if REVIEWS[oldest]["status"] == "running":
            break
        REVIEWS.pop(oldest, None)


def get_review(review_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return REVIEWS.get(review_id)


def add_stage(review_id: str, name: str, status: str, message: str = "", **extra: Any) -> None:
    with _LOCK:
        review = REVIEWS.get(review_id)
        if not review:
            return
        stage = {
            "name": name,
            "status": status,
            "message": message,
            "timestamp": time.time(),
        }
        stage.update(extra)
        review["stages"].append(stage)


def finish_review(review_id: str, result: Dict[str, Any]) -> None:
    with _LOCK:
        review = REVIEWS.get(review_id)
        if not review:
            return
        review["result"] = result
        review["status"] = "complete"


def fail_review(review_id: str, message: str) -> None:
    with _LOCK:
        review = REVIEWS.get(review_id)
        if not review:
            return
        review["status"] = "error"
        review["error"] = message
