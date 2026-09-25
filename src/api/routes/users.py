"""User management routes (owner / co_owner).

Provides CRUD operations for user accounts including approval of
pending registrations, role changes, account suspension, and
ownership transfer.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from litestar import Controller, Request, delete, get, patch, post
from litestar.exceptions import ClientException, NotFoundException
from litestar.security.jwt import Token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.api.schemas import (
    MessageSchema,
    UserApproveRequestSchema,
    UserListResponseSchema,
    UserResponseSchema,
    UserRoleUpdateSchema,
    UserStatusUpdateSchema,
)
from src.core.models import User

logger = structlog.get_logger(__name__)


class UserController(Controller):
    """Owner / co_owner endpoints for managing user accounts."""

    path = "/api/v1/users"
    tags = ["users"]
    guards = [require_role("owner", "co_owner")]

    # -----------------------------------------------------------------
    # GET /api/v1/users
    # -----------------------------------------------------------------

    @get(
        "/",
        summary="List users with optional status filter",
    )
    async def list_users(
        self,
        db_session: AsyncSession,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> UserListResponseSchema:
        """Return a paginated list of users, optionally filtered by status."""
        base = select(User)
        count_base = select(func.count()).select_from(User)

        if status:
            base = base.where(User.status == status)
            count_base = count_base.where(User.status == status)

        # Total count
        total_result = await db_session.execute(count_base)
        total = total_result.scalar_one()

        # Fetch page
        stmt = base.order_by(User.created_at.desc()).offset(offset).limit(limit)
        result = await db_session.execute(stmt)
        users = list(result.scalars().all())

        return UserListResponseSchema(
            users=[UserResponseSchema.model_validate(u) for u in users],
            total=total,
        )

    # -----------------------------------------------------------------
    # POST /api/v1/users/{user_id}/approve
    # -----------------------------------------------------------------

    @post(
        "/{user_id:uuid}/approve",
        summary="Approve a pending user registration",
    )
    async def approve_user(
        self,
        user_id: uuid.UUID,
        data: UserApproveRequestSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> UserResponseSchema:
        """Set a pending user's status to active and assign the given role."""
        caller = request.user
        user = await self._get_user(db_session, user_id)

        if user.status != "pending_approval":
            raise ClientException(
                detail=f"User is not pending approval (current status: {user.status})",
                status_code=409,
            )

        # Only owner can assign co_owner role
        if data.role == "co_owner" and caller.role != "owner":
            raise ClientException(
                detail="Only the owner can assign the co_owner role",
                status_code=403,
            )

        user.status = "active"
        user.role = data.role
        await db_session.flush()

        logger.info("users.approved", user_id=str(user_id), role=data.role)
        return UserResponseSchema.model_validate(user)

    # -----------------------------------------------------------------
    # POST /api/v1/users/{user_id}/reject
    # -----------------------------------------------------------------

    @post(
        "/{user_id:uuid}/reject",
        summary="Reject a pending user registration",
    )
    async def reject_user(
        self,
        user_id: uuid.UUID,
        db_session: AsyncSession,
    ) -> UserResponseSchema:
        """Set a pending user's status to rejected."""
        user = await self._get_user(db_session, user_id)

        if user.status != "pending_approval":
            raise ClientException(
                detail=f"User is not pending approval (current status: {user.status})",
                status_code=409,
            )

        user.status = "rejected"
        await db_session.flush()

        logger.info("users.rejected", user_id=str(user_id))
        return UserResponseSchema.model_validate(user)

    # -----------------------------------------------------------------
    # PATCH /api/v1/users/{user_id}/role
    # -----------------------------------------------------------------

    @patch(
        "/{user_id:uuid}/role",
        summary="Change a user's role",
    )
    async def update_role(
        self,
        user_id: uuid.UUID,
        data: UserRoleUpdateSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> UserResponseSchema:
        """Change the role of an existing user.

        Restrictions:
        - Cannot change the owner's role.
        - Co-owner cannot change another co_owner or owner.
        - Only owner can assign co_owner role.
        """
        caller = request.user
        user = await self._get_user(db_session, user_id)

        if user.role == "owner":
            raise ClientException(
                detail="Cannot change the owner's role",
                status_code=403,
            )

        # Co-owner cannot touch other co_owners
        if caller.role == "co_owner" and user.role == "co_owner":
            raise ClientException(
                detail="Co-owners cannot change another co-owner's role",
                status_code=403,
            )

        # Only owner can assign co_owner
        if data.role == "co_owner" and caller.role != "owner":
            raise ClientException(
                detail="Only the owner can assign the co_owner role",
                status_code=403,
            )

        user.role = data.role
        await db_session.flush()

        logger.info("users.role_changed", user_id=str(user_id), new_role=data.role)
        return UserResponseSchema.model_validate(user)

    # -----------------------------------------------------------------
    # PATCH /api/v1/users/{user_id}/status
    # -----------------------------------------------------------------

    @patch(
        "/{user_id:uuid}/status",
        summary="Suspend or reactivate a user",
    )
    async def update_status(
        self,
        user_id: uuid.UUID,
        data: UserStatusUpdateSchema,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> UserResponseSchema:
        """Suspend or reactivate a user.

        Restrictions:
        - Cannot modify yourself.
        - Cannot suspend the owner.
        - Co-owner cannot suspend owner or other co_owners.
        """
        caller = request.user

        if user_id == caller.id:
            raise ClientException(
                detail="Cannot change your own status",
                status_code=403,
            )

        user = await self._get_user(db_session, user_id)

        if user.role == "owner":
            raise ClientException(
                detail="Cannot suspend the owner account",
                status_code=403,
            )

        # Co-owner cannot touch other co_owners
        if caller.role == "co_owner" and user.role == "co_owner":
            raise ClientException(
                detail="Co-owners cannot change another co-owner's status",
                status_code=403,
            )

        user.status = data.status
        await db_session.flush()

        logger.info("users.status_changed", user_id=str(user_id), new_status=data.status)
        return UserResponseSchema.model_validate(user)

    # -----------------------------------------------------------------
    # DELETE /api/v1/users/{user_id}
    # -----------------------------------------------------------------

    @delete(
        "/{user_id:uuid}",
        summary="Delete a user account",
        status_code=200,
    )
    async def delete_user(
        self,
        user_id: uuid.UUID,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> MessageSchema:
        """Permanently delete a user account.

        Restrictions:
        - Cannot delete yourself.
        - Cannot delete the owner.
        - Co-owner cannot delete owner or other co_owners.
        """
        caller = request.user

        if user_id == caller.id:
            raise ClientException(
                detail="Cannot delete your own account",
                status_code=403,
            )

        user = await self._get_user(db_session, user_id)

        if user.role == "owner":
            raise ClientException(
                detail="Cannot delete the owner account",
                status_code=403,
            )

        # Co-owner cannot delete other co_owners
        if caller.role == "co_owner" and user.role == "co_owner":
            raise ClientException(
                detail="Co-owners cannot delete another co-owner",
                status_code=403,
            )

        await db_session.delete(user)
        await db_session.flush()

        logger.info("users.deleted", user_id=str(user_id), email=user.email)
        return MessageSchema(message=f"User {user.email} deleted")

    # -----------------------------------------------------------------
    # POST /api/v1/users/{user_id}/transfer-ownership
    # -----------------------------------------------------------------

    @post(
        "/{user_id:uuid}/transfer-ownership",
        summary="Transfer ownership to another user",
    )
    async def transfer_ownership(
        self,
        user_id: uuid.UUID,
        request: Request[User, Token, Any],
        db_session: AsyncSession,
    ) -> MessageSchema:
        """Transfer owner role to another active user.

        Only the current owner can transfer ownership.
        The current owner is demoted to co_owner.
        """
        caller = request.user

        if caller.role != "owner":
            raise ClientException(
                detail="Only the owner can transfer ownership",
                status_code=403,
            )

        target = await self._get_user(db_session, user_id)

        if target.id == caller.id:
            raise ClientException(
                detail="Cannot transfer ownership to yourself",
                status_code=400,
            )

        if target.status != "active":
            raise ClientException(
                detail="Target user must be active to receive ownership",
                status_code=400,
            )

        # Atomic swap: target becomes owner, caller becomes co_owner
        target.role = "owner"
        caller.role = "co_owner"
        await db_session.flush()

        logger.info(
            "users.ownership_transferred",
            from_user=str(caller.id),
            to_user=str(user_id),
        )
        return MessageSchema(
            message=f"Ownership transferred to {target.email}. You are now co_owner.",
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    async def _get_user(db_session: AsyncSession, user_id: uuid.UUID) -> User:
        """Fetch a user by ID or raise 404."""
        stmt = select(User).where(User.id == user_id)
        result = await db_session.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            raise NotFoundException(detail="User not found")

        return user
