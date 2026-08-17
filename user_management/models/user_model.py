"""
models/user_model.py
Validation helpers for User and Profile data.
No ORM - Supabase handles persistence.
"""

import re
from email_validator import validate_email as _validate_email, EmailNotValidError


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------

def validate_email(email: str) -> dict:
    """
    Validates an email address format.
    Returns: {valid: bool, error: str | None}
    """
    if not email or not isinstance(email, str):
        return {"valid": False, "error": "Email is required."}
    try:
        _validate_email(email.strip(), check_deliverability=False)
        return {"valid": True, "error": None}
    except EmailNotValidError as exc:
        return {"valid": False, "error": str(exc)}


def validate_password(password: str) -> dict:
    """
    Validates password strength:
    - Minimum 8 characters
    - At least 1 uppercase letter
    - At least 1 number
    Returns: {valid: bool, error: str | None}
    """
    if not password or not isinstance(password, str):
        return {"valid": False, "error": "Password is required."}
    if len(password) < 8:
        return {"valid": False, "error": "Password must be at least 8 characters long."}
    if not re.search(r"[0-9]", password):
        return {"valid": False, "error": "Password must contain at least one number."}
    if not re.search(r"[A-Z]", password):
        return {"valid": False, "error": "Password must contain at least one uppercase letter."}
    return {"valid": True, "error": None}


def validate_full_name(full_name: str) -> dict:
    """
    Validates that full_name is a non-empty string.
    Returns: {valid: bool, error: str | None}
    """
    if not full_name or not isinstance(full_name, str) or not full_name.strip():
        return {"valid": False, "error": "Full name is required."}
    if len(full_name.strip()) < 2:
        return {"valid": False, "error": "Full name must be at least 2 characters."}
    return {"valid": True, "error": None}


# ---------------------------------------------------------------------------
# Composite validators
# ---------------------------------------------------------------------------

def validate_register_data(data: dict) -> dict:
    """
    Validates the full registration payload.
    Expected keys: email, password, full_name, role (optional)

    Returns: {valid: bool, errors: list[str]}
    """
    errors = []

    email_check = validate_email(data.get("email", ""))
    if not email_check["valid"]:
        errors.append(email_check["error"])

    password_check = validate_password(data.get("password", ""))
    if not password_check["valid"]:
        errors.append(password_check["error"])

    name_check = validate_full_name(data.get("full_name", ""))
    if not name_check["valid"]:
        errors.append(name_check["error"])

    role = data.get("role", "user")
    if role not in ("user", "admin"):
        errors.append("Role must be 'user' or 'admin'.")

    return {"valid": len(errors) == 0, "errors": errors}


def validate_login_data(data: dict) -> dict:
    """
    Validates the login payload.
    Expected keys: email, password

    Returns: {valid: bool, errors: list[str]}
    """
    errors = []

    if not data.get("email"):
        errors.append("Email is required.")

    if not data.get("password"):
        errors.append("Password is required.")

    return {"valid": len(errors) == 0, "errors": errors}


def validate_update_profile_data(data: dict) -> dict:
    """
    Validates update-profile payload.
    Allowed keys: full_name, email  (both optional but at least one required)

    Returns: {valid: bool, errors: list[str]}
    """
    errors = []
    allowed_keys = {"full_name", "email"}
    provided = {k for k in data if k in allowed_keys and data[k]}

    if not provided:
        errors.append("At least one field (full_name or email) must be provided.")

    if "email" in provided:
        email_check = validate_email(data["email"])
        if not email_check["valid"]:
            errors.append(email_check["error"])

    if "full_name" in provided:
        name_check = validate_full_name(data["full_name"])
        if not name_check["valid"]:
            errors.append(name_check["error"])

    return {"valid": len(errors) == 0, "errors": errors}


# ---------------------------------------------------------------------------
# Profile dict builder (documentation / type hint substitute)
# ---------------------------------------------------------------------------

def build_profile_dict(
    user_id: str,
    email: str,
    full_name: str,
    role: str = "user",
    is_active: bool = True,
) -> dict:
    """
    Returns a profile dict ready for insertion into the `profiles` table.
    `created_at` / `updated_at` are left to Supabase defaults.
    """
    return {
        "id": user_id,
        "email": email,
        "full_name": full_name,
        "role": role,
        "is_active": is_active,
    }
