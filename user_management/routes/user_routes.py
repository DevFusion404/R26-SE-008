"""
routes/user_routes.py
Flask Blueprint for user management endpoints.
Prefix: /api/auth
"""

from flask import Blueprint, request, jsonify, g
from middleware.auth_middleware import require_auth, require_admin
from models.user_model import (
    validate_register_data,
    validate_login_data,
    validate_update_profile_data,
)
import services.user_service as user_service

user_bp = Blueprint("user_bp", __name__, url_prefix="/api/auth")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@user_bp.route("/health", methods=["GET"])
def health():
    """GET /api/auth/health"""
    return jsonify({"success": True, "status": "ok", "service": "user-management-auth"}), 200


# ---------------------------------------------------------------------------
# Registration & Authentication
# ---------------------------------------------------------------------------

@user_bp.route("/register", methods=["POST"])
def register():
    """POST /api/auth/register"""
    data = request.get_json(silent=True) or {}

    # Validate input
    validation = validate_register_data(data)
    if not validation["valid"]:
        return jsonify({"success": False, "errors": validation["errors"]}), 400

    result = user_service.register(
        email=data["email"].strip().lower(),
        password=data["password"],
        full_name=data["full_name"].strip(),
        role=data.get("role", "user"),
    )

    if not result["success"]:
        return jsonify(result), 400

    return jsonify(result), 201


@user_bp.route("/login", methods=["POST"])
def login():
    """POST /api/auth/login"""
    data = request.get_json(silent=True) or {}

    validation = validate_login_data(data)
    if not validation["valid"]:
        return jsonify({"success": False, "errors": validation["errors"]}), 400

    result = user_service.login(
        email=data["email"].strip().lower(),
        password=data["password"],
    )

    if not result["success"]:
        return jsonify(result), 401

    return jsonify(result), 200


@user_bp.route("/logout", methods=["POST"])
@require_auth
def logout():
    """POST /api/auth/logout  (requires Bearer token)"""
    result = user_service.logout(access_token=g.token)

    if not result["success"]:
        return jsonify(result), 500

    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@user_bp.route("/profile", methods=["GET"])
@require_auth
def get_profile():
    """GET /api/auth/profile  (requires Bearer token)"""
    result = user_service.get_profile(user_id=g.user_id)

    if not result["success"]:
        status = 404 if "not found" in result.get("error", "").lower() else 500
        return jsonify(result), status

    return jsonify(result), 200


@user_bp.route("/profile", methods=["PUT"])
@require_auth
def update_profile():
    """PUT /api/auth/profile  (requires Bearer token)"""
    data = request.get_json(silent=True) or {}

    validation = validate_update_profile_data(data)
    if not validation["valid"]:
        return jsonify({"success": False, "errors": validation["errors"]}), 400

    result = user_service.update_profile(user_id=g.user_id, data=data)

    if not result["success"]:
        status = 404 if "not found" in result.get("error", "").lower() else 500
        return jsonify(result), status

    return jsonify(result), 200


@user_bp.route("/account", methods=["DELETE"])
@require_auth
def delete_account():
    """DELETE /api/auth/account  (requires Bearer token)"""
    result = user_service.delete_account(user_id=g.user_id, access_token=g.token)

    if not result["success"]:
        return jsonify(result), 500

    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@user_bp.route("/users", methods=["GET"])
@require_admin
def get_all_users():
    """GET /api/auth/users  (admin only)"""
    result = user_service.get_all_users()

    if not result["success"]:
        return jsonify(result), 500

    return jsonify(result), 200


@user_bp.route("/users/<user_id>/role", methods=["PUT"])
@require_admin
def update_user_role(user_id: str):
    """PUT /api/auth/users/<user_id>/role  (admin only)"""
    data = request.get_json(silent=True) or {}
    new_role = data.get("role")

    if not new_role:
        return jsonify({"success": False, "error": "Field 'role' is required."}), 400

    result = user_service.update_user_role(user_id=user_id, new_role=new_role)

    if not result["success"]:
        status = 400 if "must be" in result.get("error", "").lower() else 404
        return jsonify(result), status

    return jsonify(result), 200
