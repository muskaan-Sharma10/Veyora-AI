"""
Veyora AI — SQLite persistence layer.
=====================================
Reuses the existing `users` table and adds the tables required by the
recruiter and candidate workspaces. All statements are idempotent so the
schema is created/upgraded on connect without destroying existing records.
"""

import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('recruiter', 'candidate')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    required_skills TEXT NOT NULL DEFAULT '[]',
    preferred_skills TEXT NOT NULL DEFAULT '[]',
    min_years REAL NOT NULL DEFAULT 0,
    seniority TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resumes (
    id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    filename TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    parsed TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'upload',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    ai_mode TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    recruiter_id INTEGER NOT NULL,
    resume_id TEXT NOT NULL DEFAULT '',
    review_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'under_review',
    analysis TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_applications_job ON applications (job_id);

CREATE TABLE IF NOT EXISTS interview_slots (
    id TEXT PRIMARY KEY,
    recruiter_id INTEGER NOT NULL,
    job_id TEXT NOT NULL DEFAULT '',
    application_id TEXT NOT NULL DEFAULT '',
    candidate_name TEXT NOT NULL DEFAULT '',
    candidate_email TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    interviewer TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'Video',
    location TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'available',
    instructions TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_slots_owner_date ON interview_slots (recruiter_id, date);

CREATE TABLE IF NOT EXISTS email_log (
    id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL DEFAULT '',
    application_id TEXT NOT NULL DEFAULT '',
    recruiter_id INTEGER NOT NULL,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidate_reports (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    resume_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    target_role TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reports_user_kind ON candidate_reports (user_id, kind);

CREATE TABLE IF NOT EXISTS roadmaps (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    target_role TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT '{}',
    progress TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def new_id() -> str:
    return uuid.uuid4().hex


def connect(path: str) -> sqlite3.Connection:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(path: str) -> None:
    """Create every table if it does not exist. Safe to call repeatedly."""
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        database = connect(current_app.config["DATABASE"])
        database.executescript(SCHEMA)
        database.commit()
        g.db = database
    return g.db


def close_db(_error: Optional[BaseException] = None) -> None:
    database = g.pop("db", None)
    if database is not None:
        database.close()


def fetch_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in get_db().execute(sql, params).fetchall()]


def fetch_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    row = get_db().execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple = ()) -> None:
    database = get_db()
    database.execute(sql, params)
    database.commit()
