"""Parse and serialize `#table(...)` calls inside Typst content.

Scope: just enough to round-trip table editing in the UI. The parser
preserves named args (columns/align/stroke/fill/inset/...) verbatim as
strings and exposes the cells as an ordered list. Each cell remembers
whether it was a bracketed content block ([text]) or a raw expression
(strong("..."), "1", function calls, etc.) so serialization round-trips
both forms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TableCell:
    content: str
    bracketed: bool = True

    def as_source(self) -> str:
        return f"[{self.content}]" if self.bracketed else self.content


@dataclass
class TableSpec:
    prelude_args: list[str] = field(default_factory=list)
    columns: int = 1
    cells: list[TableCell] = field(default_factory=list)
    raw_start: int = 0
    raw_end: int = 0
    has_hash: bool = True


_TABLE_HEAD_RE = re.compile(r"(?<![\w-])#?table\s*\(")
_NAMED_ARG_RE = re.compile(r"^([a-zA-Z_][\w-]*)\s*:")


def _find_matching(source: str, start: int, open_c: str, close_c: str) -> int | None:
    """Return index of the close char that balances source[start-1] (an open).

    `start` is the index AFTER the opening char. Skips string literals and
    line comments. Handles balanced parens, brackets, and braces along the way.
    """
    depth = 1
    paren = bracket = brace = 0
    if open_c == "(":
        paren = 1
    elif open_c == "[":
        bracket = 1
    elif open_c == "{":
        brace = 1
    i = start
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == '"':
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif c == "[":
            bracket += 1
        elif c == "]":
            bracket -= 1
        elif c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
        if c == close_c and paren == 0 and bracket == 0 and brace == 0:
            return i
        i += 1
    return None


def _split_top_level(source: str) -> list[str]:
    """Split source by top-level commas, respecting (), [], {} and strings."""
    args: list[str] = []
    paren = bracket = brace = 0
    in_string = False
    start = 0
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if in_string:
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
        elif c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif c == "[":
            bracket += 1
        elif c == "]":
            bracket -= 1
        elif c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
        elif c == "," and paren == bracket == brace == 0:
            piece = source[start:i].strip()
            if piece:
                args.append(piece)
            start = i + 1
        i += 1
    tail = source[start:].strip()
    if tail:
        args.append(tail)
    return args


def _parse_columns(value: str) -> int:
    v = value.strip()
    if v.isdigit():
        return int(v)
    if v.startswith("(") and v.endswith(")"):
        items = _split_top_level(v[1:-1])
        return max(1, len(items))
    return 1


def _make_cell(raw: str) -> TableCell:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        # confirm the brackets balance as outer pair
        inner_end = _find_matching(raw, 1, "[", "]")
        if inner_end == len(raw) - 1:
            return TableCell(content=raw[1:-1], bracketed=True)
    return TableCell(content=raw, bracketed=False)


def find_tables(body: str) -> list[TableSpec]:
    """Locate every #table(...) call in body and parse each into a TableSpec."""
    specs: list[TableSpec] = []
    for m in _TABLE_HEAD_RE.finditer(body):
        open_paren_idx = m.end() - 1
        close_idx = _find_matching(body, open_paren_idx + 1, "(", ")")
        if close_idx is None:
            continue
        args_text = body[open_paren_idx + 1 : close_idx]
        tokens = _split_top_level(args_text)

        prelude: list[str] = []
        cells_started = False
        cells_raw: list[str] = []
        columns_count = 1
        columns_seen = False

        for tok in tokens:
            named = _NAMED_ARG_RE.match(tok)
            if named and not cells_started:
                prelude.append(tok)
                if named.group(1) == "columns":
                    columns_seen = True
                    columns_count = _parse_columns(tok[named.end():])
            else:
                cells_started = True
                cells_raw.append(tok)
        if not columns_seen:
            prelude.insert(0, f"columns: {max(1, len(cells_raw))}")
            columns_count = max(1, len(cells_raw))

        spec = TableSpec(
            prelude_args=prelude,
            columns=columns_count,
            cells=[_make_cell(c) for c in cells_raw],
            raw_start=m.start(),
            raw_end=close_idx + 1,
            has_hash=m.group(0).startswith("#"),
        )
        specs.append(spec)
    return specs


def _update_columns_in_prelude(spec: TableSpec) -> None:
    for i, arg in enumerate(spec.prelude_args):
        if not arg.lstrip().startswith("columns:"):
            continue
        value = arg.split(":", 1)[1].strip()
        if value.startswith("(") and value.endswith(")"):
            items = _split_top_level(value[1:-1])
            while len(items) < spec.columns:
                items.append("auto")
            while len(items) > spec.columns:
                items.pop()
            spec.prelude_args[i] = f"columns: ({', '.join(items)})"
        else:
            spec.prelude_args[i] = f"columns: {spec.columns}"
        return
    spec.prelude_args.insert(0, f"columns: {spec.columns}")


_CSS_LENGTH_RE = re.compile(r"^\d+(?:\.\d+)?(fr|cm|mm|pt|em|px|in|%)$")


def _typst_col_to_css(item: str) -> str:
    item = item.strip()
    if item == "auto":
        # Cap at ~10em so a wide-content auto column can't starve fr columns.
        # Short content stays content-sized; long content wraps inside the cell.
        return "fit-content(10em)"
    m = _CSS_LENGTH_RE.match(item)
    if m:
        if m.group(1) == "fr":
            return f"minmax(0, {item})"
        return item
    return "minmax(0, 1fr)"


def columns_to_css(prelude_args: list[str], cols: int) -> str:
    """Translate the columns prelude into a CSS grid-template-columns value.

    Tuple form (e.g. `columns: (auto, 1fr, 2cm)`) maps entry-by-entry so the
    UI grid mirrors the document's column proportions. Integer form or any
    spec that doesn't parse cleanly falls back to equal-width tracks.
    """
    spec = next(
        (a.split(":", 1)[1].strip() for a in prelude_args
         if a.lstrip().startswith("columns:")),
        None,
    )
    if spec and spec.startswith("(") and spec.endswith(")"):
        items = _split_top_level(spec[1:-1])
        if len(items) == cols:
            return " ".join(_typst_col_to_css(it) for it in items)
    return f"repeat({cols}, minmax(0, 1fr))"


def add_row(spec: TableSpec) -> None:
    spec.cells.extend([TableCell(content="", bracketed=True) for _ in range(spec.columns)])


def remove_row(spec: TableSpec) -> None:
    if len(spec.cells) > spec.columns:
        del spec.cells[-spec.columns :]


def add_column(spec: TableSpec) -> None:
    new_cells: list[TableCell] = []
    for r in range(0, len(spec.cells), spec.columns):
        new_cells.extend(spec.cells[r : r + spec.columns])
        new_cells.append(TableCell(content="", bracketed=True))
    spec.cells = new_cells
    spec.columns += 1
    _update_columns_in_prelude(spec)


def remove_column(spec: TableSpec) -> None:
    if spec.columns <= 1:
        return
    new_cells: list[TableCell] = []
    for r in range(0, len(spec.cells), spec.columns):
        row = spec.cells[r : r + spec.columns]
        new_cells.extend(row[:-1])
    spec.cells = new_cells
    spec.columns -= 1
    _update_columns_in_prelude(spec)


def serialize_table(spec: TableSpec) -> str:
    """Render a TableSpec back to canonical `#table(...)` source."""
    # Pad cells to a multiple of columns so the last row is rectangular.
    while spec.cells and len(spec.cells) % spec.columns != 0:
        spec.cells.append(TableCell(content="", bracketed=True))

    lines: list[str] = ["#table(" if spec.has_hash else "table("]
    for arg in spec.prelude_args:
        lines.append(f"  {arg},")
    rows = [
        spec.cells[i : i + spec.columns]
        for i in range(0, len(spec.cells), spec.columns)
    ]
    for row in rows:
        line = "  " + ", ".join(c.as_source() for c in row) + ","
        lines.append(line)
    lines.append(")")
    return "\n".join(lines)


def new_table(rows: int = 2, columns: int = 2) -> TableSpec:
    cells = [TableCell(content="", bracketed=True) for _ in range(rows * columns)]
    return TableSpec(
        prelude_args=[f"columns: {columns}", "stroke: 0.5pt", "inset: 0.5em"],
        columns=columns,
        cells=cells,
    )


def replace_table_in_body(body: str, spec: TableSpec, new_source: str) -> str:
    """Replace the table at spec.raw_start..raw_end in body with new_source."""
    return body[: spec.raw_start] + new_source + body[spec.raw_end :]


def append_table_to_body(body: str, new_source: str) -> str:
    sep = "" if body.endswith("\n\n") else ("\n" if body.endswith("\n") else "\n\n")
    return body + sep + new_source + "\n"
