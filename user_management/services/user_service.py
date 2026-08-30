"""
services/user_service.py
Business logic layer: wraps Supabase Auth + profiles table operations.
"""

import os
import jwt
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# Import the shared Supabase client
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.supabase_client import supabase

import logging

logger = logging.getLogger("user_management.service")

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-change-in-production")
PROFILES_TABLE = "profiles"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ok(data) -> dict:
    return {"success": True, "data": data}


def _err(message: str) -> dict:
    return {"success": False, "error": message}


def _extract_role_from_profile(user_id: str) -> str:
    """Fetches role from profiles table; falls back to 'user'."""
    try:
        resp = supabase.table(PROFILES_TABLE).select("role").eq("id", user_id).single().execute()
        return resp.data.get("role", "user") if resp.data else "user"
    except Exception as exc:
        logger.warning(f"Failed to fetch role from profile for user {user_id}: {exc}")
        return "user"


# ---------------------------------------------------------------------------
# Auth operations
# ---------------------------------------------------------------------------

def register(email: str, password: str, full_name: str, role: str = "user") -> dict:
    """
    Registers a new user via Supabase Auth and inserts a profile row.
    Steps:
      1. supabase.auth.sign_up()
      2. Insert row into `profiles` table
    Returns: {success, data: {user, profile}}
    """
    try:
        # Step 1 - Supabase Auth sign-up
        auth_resp = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": {"full_name": full_name, "role": role}
            }
        })

        if not auth_resp.user:
            logger.error("Supabase Auth sign_up succeeded but returned no user object.")
            return _err("Registration failed: no user returned from Supabase Auth.")

        user = auth_resp.user
        user_id = user.id

        # Step 2 - Insert into profiles table
        profile_data = {
            "id": user_id,
            "email": email,
            "full_name": full_name,
            "role": role,
            "is_active": True,
        }
        profile_resp = supabase.table(PROFILES_TABLE).insert(profile_data).execute()

        if not profile_resp.data:
            logger.error(f"Profile creation failed in table '{PROFILES_TABLE}' for user_id {user_id}")
            return _err("User registered in Auth but profile creation failed.")

        logger.info(f"Successfully registered user {email} (ID: {user_id})")
        return _ok({
            "user": {
                "id": user_id,
                "email": user.email,
                "email_confirmed": user.email_confirmed_at is not None,
            },
            "profile": profile_resp.data[0],
            "session": {
                "access_token": auth_resp.session.access_token if auth_resp.session else None,
                "refresh_token": auth_resp.session.refresh_token if auth_resp.session else None,
            }
        })

    except Exception as exc:
        msg = str(exc)
        logger.error(f"Supabase Auth registration exception for {email}: {msg}", exc_info=True)
        if "already registered" in msg.lower() or "already exists" in msg.lower():
            return _err("An account with this email already exists.")
        return _err(f"Registration error: {msg}")


def login(email: str, password: str) -> dict:
    """
    Signs in with email + password via Supabase Auth.
    Also fetches the user's role from the profiles table.
    Returns: {success, data: {user, session, role}}
    """
    try:
        auth_resp = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })

        if not auth_resp.user or not auth_resp.session:
            return _err("Login failed: invalid credentials.")

        user = auth_resp.user
        session = auth_resp.session

        # Fetch role and profile details from database profiles table
        try:
            profile_resp = supabase.table(PROFILES_TABLE).select("*").eq("id", user.id).single().execute()
            profile = profile_resp.data if profile_resp.data else {
                "id": user.id,
                "email": user.email,
                "full_name": user.user_metadata.get("full_name", "User"),
                "role": role,
            }
        except Exception:
            profile = {
                "id": user.id,
                "email": user.email,
                "full_name": user.user_metadata.get("full_name", "User"),
                "role": role,
            }

        return _ok({
            "user": {
                "id": user.id,
                "email": user.email,
                "email_confirmed": user.email_confirmed_at is not None,
            },
            "profile": profile,
            "session": {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_in": session.expires_in,
                "token_type": session.token_type,
            }
        })

    except Exception as exc:
        msg = str(exc)
        if "invalid login credentials" in msg.lower() or "invalid" in msg.lower():
            return _err("Invalid email or password.")
        return _err(f"Login error: {msg}")


def logout(access_token: str) -> dict:
    """
    Signs out the current user session.
    Returns: {success, data: {message}}
    """
    try:
        supabase.auth.sign_out()
        return _ok({"message": "Logged out successfully."})
    except Exception as exc:
        return _err(f"Logout error: {str(exc)}")


# ---------------------------------------------------------------------------
# Profile operations
# ---------------------------------------------------------------------------

def get_profile(user_id: str) -> dict:
    """
    Fetches a user's profile from the profiles table.
    Returns: {success, data: profile_row}
    """
    try:
        resp = (
            supabase.table(PROFILES_TABLE)
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if not resp.data:
            return _err("Profile not found.")
        return _ok(resp.data)
    except Exception as exc:
        msg = str(exc)
        if "PGRST116" in msg or "no rows" in msg.lower():
            return _err("Profile not found.")
        return _err(f"Error fetching profile: {msg}")


def update_profile(user_id: str, data: dict) -> dict:
    """
    Updates allowed profile fields: full_name, email.
    Returns: {success, data: updated_profile_row}
    """
    allowed = {}
    if "full_name" in data:
        allowed["full_name"] = data["full_name"]
    if "email" in data:
        allowed["email"] = data["email"]

    if not allowed:
        return _err("No valid fields to update.")

    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        resp = (
            supabase.table(PROFILES_TABLE)
            .update(allowed)
            .eq("id", user_id)
            .execute()
        )
        if not resp.data:
            return _err("Profile update failed or profile not found.")
        return _ok(resp.data[0])
    except Exception as exc:
        return _err(f"Profile update error: {str(exc)}")


def delete_account(user_id: str, access_token: str) -> dict:
    """
    Deletes the user's profile row and calls Supabase admin to remove the auth user.
    Returns: {success, data: {message}}
    """
    try:
        # Delete profile row first
        supabase.table(PROFILES_TABLE).delete().eq("id", user_id).execute()

        # Delete auth user via admin API
        try:
            supabase.auth.admin.delete_user(user_id)
        except Exception as admin_exc:
            # Profile already deleted - log but don't fail hard
            print(f"[WARN] Admin delete user failed: {admin_exc}")

        return _ok({"message": "Account deleted successfully."})
    except Exception as exc:
        return _err(f"Account deletion error: {str(exc)}")


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------

def get_all_users() -> dict:
    """
    Admin only: lists all rows from the profiles table.
    Returns: {success, data: list[profile_row]}
    """
    try:
        resp = (
            supabase.table(PROFILES_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return _ok(resp.data or [])
    except Exception as exc:
        return _err(f"Error fetching users: {str(exc)}")


def update_user_role(user_id: str, new_role: str) -> dict:
    """
    Admin only: changes a user's role in the profiles table.
    Returns: {success, data: updated_profile_row}
    """
    if new_role not in ("user", "admin"):
        return _err("Role must be 'user' or 'admin'.")

    try:
        resp = (
            supabase.table(PROFILES_TABLE)
            .update({"role": new_role, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", user_id)
            .execute()
        )
        if not resp.data:
            return _err("User not found or role update failed.")
        return _ok(resp.data[0])
    except Exception as exc:
        return _err(f"Role update error: {str(exc)}")


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_token(token: str) -> dict:
    """
    Decodes a Supabase JWT and extracts user_id and role.
    Returns: {success, data: {user_id, email, role}} or {success, error}
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        user_id = payload.get("sub")
        email = payload.get("email", "")
        user_meta = payload.get("user_metadata", {}) or {}
        app_meta = payload.get("app_metadata", {}) or {}
        role = user_meta.get("role") or app_meta.get("role") or "user"

        if not user_id:
            return _err("Token missing user id.")

        return _ok({"user_id": user_id, "email": email, "role": role})
    except jwt.ExpiredSignatureError:
        return _err("Token has expired.")
    except jwt.InvalidTokenError as exc:
        return _err(f"Invalid token: {exc}")
    except Exception as exc:
        return _err(f"Token error: {exc}")
