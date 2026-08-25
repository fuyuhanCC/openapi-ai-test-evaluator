"""Deterministic in-memory implementation of the demo Items OpenAPI contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Annotated, Self

from fastapi import FastAPI, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator


class APIModel(BaseModel):
    """Strict model shared by the fixture API's request and response bodies."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ItemStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


NonEmptyName = Annotated[str, Field(min_length=1, max_length=100)]
Category = Annotated[str, Field(min_length=1, max_length=50)]
NonNegativePrice = Annotated[float, Field(ge=0, strict=True)]
ItemId = Annotated[int, Path(ge=1)]


class ItemCreate(APIModel):
    name: NonEmptyName
    price: NonNegativePrice
    status: ItemStatus
    category: Category | None = None


class ItemUpdate(APIModel):
    name: NonEmptyName | None = None
    price: NonNegativePrice | None = None
    status: ItemStatus | None = None
    category: Category | None = None

    @model_validator(mode="after")
    def require_at_least_one_property(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("update body must contain at least one property")
        for field_name in ("name", "price", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class Item(APIModel):
    id: int = Field(ge=1)
    name: NonEmptyName
    price: NonNegativePrice
    status: ItemStatus
    category: Category | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ItemList(APIModel):
    items: list[Item]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    total: int = Field(ge=0)


class _DemoAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class _ItemStore:
    """Small locked store with a deterministic logical clock."""

    _BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._items: dict[int, Item] = {}
            self._next_id = 1
            self._clock_tick = 0

    def create(self, payload: ItemCreate) -> Item:
        with self._lock:
            item_id = self._next_id
            self._next_id += 1
            timestamp = self._timestamp()
            item = Item(
                id=item_id,
                name=payload.name,
                price=payload.price,
                status=payload.status,
                category=payload.category,
                createdAt=timestamp,
                updatedAt=timestamp,
            )
            self._items[item_id] = item
            return item.model_copy(deep=True)

    def list(
        self,
        *,
        status: ItemStatus | None,
        category: str | None,
        offset: int,
        limit: int,
    ) -> ItemList:
        with self._lock:
            items = sorted(self._items.values(), key=lambda item: item.id)
            if status is not None:
                items = [item for item in items if item.status is status]
            if category is not None:
                items = [item for item in items if item.category == category]
            total = len(items)
            page = [item.model_copy(deep=True) for item in items[offset : offset + limit]]
            return ItemList(items=page, offset=offset, limit=limit, total=total)

    def get(self, item_id: int) -> Item:
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise _not_found(item_id)
            return item.model_copy(deep=True)

    def replace(self, item_id: int, payload: ItemCreate) -> Item:
        with self._lock:
            existing = self._items.get(item_id)
            if existing is None:
                raise _not_found(item_id)
            item = Item(
                id=item_id,
                name=payload.name,
                price=payload.price,
                status=payload.status,
                category=payload.category,
                createdAt=existing.created_at,
                updatedAt=self._timestamp(),
            )
            self._items[item_id] = item
            return item.model_copy(deep=True)

    def update(self, item_id: int, payload: ItemUpdate) -> Item:
        with self._lock:
            existing = self._items.get(item_id)
            if existing is None:
                raise _not_found(item_id)
            changes = payload.model_dump(exclude_unset=True)
            changes["updated_at"] = self._timestamp()
            item = existing.model_copy(update=changes)
            self._items[item_id] = item
            return item.model_copy(deep=True)

    def delete(self, item_id: int) -> None:
        with self._lock:
            if item_id not in self._items:
                raise _not_found(item_id)
            del self._items[item_id]

    def _timestamp(self) -> datetime:
        value = self._BASE_TIME + timedelta(seconds=self._clock_tick)
        self._clock_tick += 1
        return value


def _not_found(item_id: int) -> _DemoAPIError:
    return _DemoAPIError(404, "not_found", f"item {item_id} was not found")


app = FastAPI(title="OATE Demo Items API", version="1.0.0")
_store = _ItemStore()


@app.exception_handler(_DemoAPIError)
async def handle_demo_api_error(_request: Request, error: _DemoAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"code": "bad_request", "message": "request validation failed"},
    )


@app.get("/items", operation_id="listItems", response_model=ItemList)
def list_items(
    status: ItemStatus | None = None,
    category: Annotated[str | None, Query(min_length=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ItemList:
    return _store.list(status=status, category=category, offset=offset, limit=limit)


@app.post("/items", operation_id="createItem", response_model=Item, status_code=201)
def create_item(payload: ItemCreate) -> Item:
    return _store.create(payload)


@app.get("/items/{item_id}", operation_id="getItem", response_model=Item)
def get_item(item_id: ItemId) -> Item:
    return _store.get(item_id)


@app.put("/items/{item_id}", operation_id="replaceItem", response_model=Item)
def replace_item(item_id: ItemId, payload: ItemCreate) -> Item:
    return _store.replace(item_id, payload)


@app.patch("/items/{item_id}", operation_id="updateItem", response_model=Item)
def update_item(item_id: ItemId, payload: ItemUpdate) -> Item:
    return _store.update(item_id, payload)


@app.delete("/items/{item_id}", operation_id="deleteItem", status_code=204)
def delete_item(item_id: ItemId) -> Response:
    _store.delete(item_id)
    return Response(status_code=204)


@app.get("/__test__/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/__test__/reset", include_in_schema=False, status_code=204)
def reset() -> Response:
    _store.reset()
    return Response(status_code=204)


def reset_state() -> None:
    """Reset the fixture directly from integration tests."""
    _store.reset()
