from enum import StrEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[StrEnum], name: str | None = None) -> Enum:
    """VARCHAR + CHECK constraint; avoids native PG enums that are painful to migrate.

    ``name`` must be unique per table when one enum backs several columns, because
    it becomes the CHECK constraint name.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
        name=name or enum_cls.__name__.lower(),
    )
