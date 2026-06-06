from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def row_to_mapping(row: Any, columns: Sequence[str] | None = None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {str(key): row[key] for key in keys()}
    if columns is not None:
        values = list(row)
        if len(values) != len(columns):
            raise ValueError("row length does not match columns")
        return {str(column): value for column, value in zip(columns, values)}
    raise TypeError(f"Cannot convert row of type {type(row).__name__} to a mapping")


def rows_to_mappings(rows: Sequence[Any], columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
    return [mapping for row in rows if (mapping := row_to_mapping(row, columns)) is not None]
