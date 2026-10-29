from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class Page(Schema, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
        q: Annotated[str | None, Query(max_length=100)] = None,
        sort: Annotated[str | None, Query(max_length=40, pattern=r"^-?[a-z_]+$")] = None,
    ) -> None:
        self.page = page
        self.page_size = page_size
        self.q = q.strip() if q else None
        self.sort = sort

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def order_by(self, allowed: dict[str, object], default: object) -> object:
        if not self.sort:
            return default
        key = self.sort.lstrip("-")
        col = allowed.get(key)
        if col is None:
            return default
        return col.desc() if self.sort.startswith("-") else col.asc()  # type: ignore[attr-defined]


PageDep = Annotated[PageParams, Depends()]


async def paginate(db: AsyncSession, stmt: Select, params: PageParams) -> tuple[list, int]:  # type: ignore[type-arg]
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset(params.offset).limit(params.page_size))).scalars().all()
    return list(rows), int(total)


class Message(Schema):
    message: str
