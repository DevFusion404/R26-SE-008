"""
middleware/auth_middleware.py
Token verification helper used by route decorators.
"""

import os
import jwt
from functools import wraps
from flask import request, jsonify, g
from dotenv import load_dotenv

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-change-in-production")


def extract_bearer_token(auth_header: str | None) -> str | None:
    """
    Parses the Authorization header and returns the raw Bearer token,
    or None if the header is missing / malformed.
    """
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def verify_supabase_token(token: str) -> dict:
    """
    Decodes a Supabase-issued JWT using the project's JWT_SECRET.
    Supabase signs tokens with HS256.

    Returns:
        {success: True, user_id: str, email: str, role: str, payload: dict}
        {success: False, error: str}
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},  # Supabase may omit aud
        )
        user_id = payload.get("sub")
        email = payload.get("email", "")

        # Role is stored in user_metadata by our service layer
        user_meta = payload.get("user_metadata", {}) or {}
        app_meta = payload.get("app_metadata", {}) or {}
        role = user_meta.get("role") or app_meta.get("role") or "user"

        if not user_id:
            return {"success": False, "error": "Token missing subject (user_id)."}

        return {
            "success": True,
            "user_id": user_id,
            "email": email,
            "role": role,
            "payload": payload,
        }
    except jwt.ExpiredSignatureError:
        return {"success": False, "error": "Token has expired."}
    except jwt.InvalidTokenError as exc:
        return {"success": False, "error": f"Invalid token: {exc}"}
    except Exception as exc:
        return {"success": False, "error": f"Token verification failed: {exc}"}


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

import logging

logger = logging.getLogger("user_management.auth_middleware")


def require_auth(f):
    """
    Decorator: ensures the request carries a valid Bearer JWT.
    On success, sets g.user_id, g.email, g.role, g.token.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = extract_bearer_token(request.headers.get("Authorization"))
        if not token:
            logger.warning(f"[AUTH FAILED - 401] Missing or malformed Authorization header on {request.path}")
            return jsonify({"success": False, "error": "Authorization token is required."}), 401

        result = verify_supabase_token(token)
        if not result["success"]:
            logger.warning(f"[AUTH FAILED - 401] Token error on {request.path}: {result['error']}")
            return jsonify({"success": False, "error": result["error"]}), 401

        # Attach verified identity to Flask g for use in route handlers
        g.user_id = result["user_id"]
        g.email = result["email"]
        g.role = result["role"]
        g.token = token

        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """
    Decorator: like require_auth but additionally enforces role == 'admin'.
    Must be applied AFTER require_auth (i.e., closer to the function).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = extract_bearer_token(request.headers.get("Authorization"))
        if not token:
            logger.warning(f"[ADMIN AUTH FAILED - 401] Missing token on {request.path}")
            return jsonify({"success": False, "error": "Authorization token is required."}), 401

        result = verify_supabase_token(token)
        if not result["success"]:
            logger.warning(f"[ADMIN AUTH FAILED - 401] Token error on {request.path}: {result['error']}")
            return jsonify({"success": False, "error": result["error"]}), 401

        if result.get("role") != "admin":
            logger.warning(f"[ADMIN AUTH FAILED - 403] User '{result.get('email')}' has role '{result.get('role')}', admin required.")
            return jsonify({"success": False, "error": "Admin access required."}), 403

        g.user_id = result["user_id"]
        g.email = result["email"]
        g.role = result["role"]
        g.token = token

        return f(*args, **kwargs)
    return decorated
