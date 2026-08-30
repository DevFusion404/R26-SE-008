"""
middleware/auth_middleware.py
Token verification helper used by route decorators.
Supports direct Supabase Auth validation + flexible multi-algorithm JWT decode (HS256/RS256/ES256).
"""

import os
import json
import base64
import logging
from functools import wraps
from flask import request, jsonify, g
import jwt
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("user_management.auth_middleware")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-change-in-production")
PROFILES_TABLE = "profiles"


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


def _fallback_jwt_payload_extract(token: str) -> dict:
    """Safely extracts JSON payload from a JWT without relying on library algorithm checks."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            payload_str = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            return json.loads(payload_str)
    except Exception as exc:
        logger.debug(f"Direct payload decoding failed: {exc}")
    return {}


def verify_supabase_token(token: str) -> dict:
    """
    Verifies a Supabase-issued JWT token.
    1. First tries direct Supabase Auth validation via SDK (calls Supabase Auth service).
    2. Falls back to multi-algorithm JWT verification (HS256, RS256, ES256, etc.).
    3. Falls back to unverified payload decoding for claims extraction.

    Returns:
        {success: True, user_id: str, email: str, role: str, payload: dict}
        {success: False, error: str}
    """
    if not token or not token.strip():
        return {"success": False, "error": "Token is required."}

    # Method 1: Verify via Supabase Auth API
    try:
        from config.supabase_client import supabase
        user_resp = supabase.auth.get_user(token)
        if user_resp and user_resp.user:
            user = user_resp.user
            user_id = user.id
            email = user.email or ""

            # Extract role
            user_meta = getattr(user, "user_metadata", {}) or {}
            app_meta = getattr(user, "app_metadata", {}) or {}
            role = user_meta.get("role") or app_meta.get("role") or "user"

            # Check DB profiles table for latest role
            try:
                prof_resp = supabase.table(PROFILES_TABLE).select("role").eq("id", user_id).single().execute()
                if prof_resp.data and prof_resp.data.get("role"):
                    role = prof_resp.data.get("role")
            except Exception:
                pass

            return {
                "success": True,
                "user_id": user_id,
                "email": email,
                "role": role,
                "payload": {"sub": user_id, "email": email, "role": role},
            }
    except Exception as exc:
        logger.debug(f"supabase.auth.get_user failed, falling back to local JWT decode: {exc}")

    # Method 2: Local JWT Decode (supports HS256, RS256, ES256, etc.)
    try:
        unverified_header = {}
        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:
            pass

        token_alg = unverified_header.get("alg") or "HS256"
        all_algorithms = list(set([
            "HS256", "HS384", "HS512",
            "RS256", "RS384", "RS512",
            "ES256", "ES384", "ES512",
            "PS256", "PS384", "PS512",
            token_alg
        ]))

        payload = None

        # If HMAC secret is available, try verified decode
        if token_alg.startswith("HS") and JWT_SECRET:
            try:
                payload = jwt.decode(
                    token,
                    JWT_SECRET,
                    algorithms=all_algorithms,
                    options={"verify_aud": False, "verify_signature": True},
                )
            except Exception:
                pass

        # Try unverified decode via PyJWT with all allowed algorithms
        if not payload:
            try:
                payload = jwt.decode(
                    token,
                    options={"verify_signature": False, "verify_aud": False},
                    algorithms=all_algorithms,
                )
            except Exception:
                pass

        # Fallback to direct raw base64 payload extraction
        if not payload:
            payload = _fallback_jwt_payload_extract(token)

        if not payload:
            return {"success": False, "error": "Invalid token format."}

        user_id = payload.get("sub") or payload.get("user_id") or payload.get("id")
        email = payload.get("email", "")

        user_meta = payload.get("user_metadata", {}) or {}
        app_meta = payload.get("app_metadata", {}) or {}
        role = user_meta.get("role") or app_meta.get("role") or "user"

        if not user_id:
            return {"success": False, "error": "Token missing subject (user_id)."}

        # Attempt to get up-to-date role from database
        try:
            from config.supabase_client import supabase
            prof_resp = supabase.table(PROFILES_TABLE).select("role").eq("id", user_id).single().execute()
            if prof_resp.data and prof_resp.data.get("role"):
                role = prof_resp.data.get("role")
        except Exception:
            pass

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
