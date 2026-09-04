"""Stable terminal table rendering for qfw-slurm inspection commands."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Render a compact left-aligned table with no hidden terminal state."""

    normalized = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in normalized:
        if len(row) != len(headers):
            raise ValueError("table row width differs from header width")
        widths = [
            max(width, len(value))
            for width, value in zip(widths, row, strict=True)
        ]
    lines = [_line(headers, widths)]
    lines.extend(_line(row, widths) for row in normalized)
    return "\n".join(line.rstrip() for line in lines)


def _line(values: Sequence[object], widths: Sequence[int]) -> str:
    return "  ".join(
        str(value).ljust(width)
        for value, width in zip(values, widths, strict=True)
    )
