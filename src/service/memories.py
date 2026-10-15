"""Authenticated Mem0 management endpoints for the bound user's chatbot memories."""

import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError

from memory.mem0_service import (
    Mem0Service,
    MemoryBusyError,
    MemoryNotFoundError,
    get_mem0_service,
)
from schema.api import ApiResponse, api_success
from schema.memory import (
    MemoryDeleteResult,
    MemoryHistory,
    MemoryList,
    MemoryRecord,
    MemoryUpdate,
    MemoryUserId,
)
from service.access import bind_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memories", tags=["memories"])


async def memory_access(
    request: Request,
    user_id: Annotated[MemoryUserId | None, Query()] = None,
) -> AsyncIterator[tuple[Mem0Service, str]]:
    identity = request.state.principal
    if identity.admin and user_id is None:
        raise HTTPException(422, "user_id is required for trusted service credentials")
    bound_user = bind_user(identity, user_id)
    try:
        service = get_mem0_service()
        if service is None:
            raise HTTPException(503, "Mem0 memory is disabled")
        yield service, bound_user
    except (HTTPException, RequestValidationError):
        raise
    except MemoryNotFoundError:
        raise HTTPException(404, "Memory not found") from None
    except MemoryBusyError:
        raise HTTPException(503, "Mem0 is busy; retry later") from None
    except TimeoutError:
        raise HTTPException(
            504,
            "Memory operation timed out; it may still complete. Check its state before retrying.",
        ) from None
    except Exception as exc:
        logger.warning("Memory management failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            503, "Memory operation failed; check its state before retrying"
        ) from None


MemoryAccess = Annotated[tuple[Mem0Service, str], Depends(memory_access)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=ApiResponse[MemoryList])
async def list_memories(
    access: MemoryAccess,
    limit: PageLimit = 50,
    cursor: UUID | None = None,
) -> ApiResponse[MemoryList]:
    service, user_id = access
    return api_success(await service.list_memories(user_id, limit, cursor))


@router.delete("", response_model=ApiResponse[MemoryDeleteResult])
async def delete_all_memories(access: MemoryAccess) -> ApiResponse[MemoryDeleteResult]:
    service, user_id = access
    return api_success(await service.delete_all_memories(user_id))


@router.get("/{memory_id}", response_model=ApiResponse[MemoryRecord])
async def get_memory(memory_id: UUID, access: MemoryAccess) -> ApiResponse[MemoryRecord]:
    service, user_id = access
    return api_success(await service.get_memory(str(memory_id), user_id))


@router.put("/{memory_id}", response_model=ApiResponse[MemoryRecord])
async def update_memory(
    memory_id: UUID, body: MemoryUpdate, access: MemoryAccess
) -> ApiResponse[MemoryRecord]:
    service, user_id = access
    return api_success(await service.update_memory(str(memory_id), user_id, body.text))


@router.delete("/{memory_id}", response_model=ApiResponse[MemoryDeleteResult])
async def delete_memory(memory_id: UUID, access: MemoryAccess) -> ApiResponse[MemoryDeleteResult]:
    service, user_id = access
    return api_success(await service.delete_memory(str(memory_id), user_id))


@router.get("/{memory_id}/history", response_model=ApiResponse[MemoryHistory])
async def memory_history(
    memory_id: UUID,
    access: MemoryAccess,
    limit: PageLimit = 50,
    cursor: Annotated[int | None, Query(ge=1, le=9223372036854775807)] = None,
) -> ApiResponse[MemoryHistory]:
    service, user_id = access
    return api_success(await service.memory_history(str(memory_id), user_id, limit, cursor))
