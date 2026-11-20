"""Session/role helpers shared by the Flask app and the API blueprints."""

from functools import wraps
from typing import Any, Dict, Optional

from flask import jsonify, redirect, request, session, url_for

from db import fetch_one


def current_user() -> Optional[Dict[str, Any]]:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return fetch_one("SELECT id, name, email, role FROM users WHERE id = ?", (user_id,))


def _wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    return request.accept_mimetypes.best == "application/json"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if _wants_json():
                return jsonify({"error": "Please sign in to continue."}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(role: str):
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                session.clear()
                return jsonify({"error": "Your session has expired. Please sign in again."}), 401
            if user["role"] != role:
                if _wants_json():
                    return jsonify({"error": "This page is not available for your account type."}), 403
                return redirect(url_for("index"))
            return view(*args, **kwargs)

        return wrapped

    return decorate


def owns_row(table: str, row_id: str, owner_column: str = "owner_id") -> Optional[Dict[str, Any]]:
    row = fetch_one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
    if not row:
        return None
    if int(row.get(owner_column) or 0) != int(session.get("user_id") or 0):
        return None
    return row
