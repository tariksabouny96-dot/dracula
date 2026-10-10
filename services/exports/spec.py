"""Strict, validated document specification.

A DocumentSpec is the single input every renderer consumes. It is deliberately
small and closed: unknown fields are rejected, control characters and lone
surrogates are refused, and hard size limits bound every dimension so a spec can
never ask a renderer to allocate without bound.
"""
from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Hard limits (a spec that exceeds any of these is rejected before rendering).
MAX_BLOCKS = 2000
MAX_ROWS = 5000
MAX_COLUMNS = 50
MAX_CELLS = 100_000
MAX_TEXT_CHARS = 100_000
MAX_SPEC_BYTES = 2 * 1024 * 1024


def _clean(text: str, *, field: str, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(text, str):
        raise ValueError(f"{field} must be a string")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    for ch in text:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError(f"{field} contains a lone surrogate")
        # Allow tab/newline/carriage-return; reject other C0/C1 control chars.
        if (code < 0x20 and ch not in "\t\n\r") or 0x7F <= code <= 0x9F:
            raise ValueError(f"{field} contains a disallowed control character (U+{code:04X})")
    return text


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Heading(_Block):
    type: Literal["heading"]
    level: int = Field(ge=1, le=6)
    text: str

    @field_validator("text")
    @classmethod
    def _v(cls, v):
        return _clean(v, field="heading.text", limit=2000)


class Paragraph(_Block):
    type: Literal["paragraph"]
    text: str

    @field_validator("text")
    @classmethod
    def _v(cls, v):
        return _clean(v, field="paragraph.text")


class BulletList(_Block):
    type: Literal["bullet_list"]
    items: List[str] = Field(min_length=1, max_length=1000)

    @field_validator("items")
    @classmethod
    def _v(cls, v):
        return [_clean(x, field="bullet_list.item", limit=5000) for x in v]


class NumberedList(_Block):
    type: Literal["numbered_list"]
    items: List[str] = Field(min_length=1, max_length=1000)

    @field_validator("items")
    @classmethod
    def _v(cls, v):
        return [_clean(x, field="numbered_list.item", limit=5000) for x in v]


class Code(_Block):
    type: Literal["code"]
    language: str = "text"
    text: str

    @field_validator("language")
    @classmethod
    def _vl(cls, v):
        v = _clean(v, field="code.language", limit=40)
        if not all(c.isalnum() or c in "-+._" for c in v):
            raise ValueError("code.language has invalid characters")
        return v

    @field_validator("text")
    @classmethod
    def _vt(cls, v):
        return _clean(v, field="code.text")


class Table(_Block):
    type: Literal["table"]
    title: Optional[str] = None
    columns: List[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: List[List[str]] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _vtitle(cls, v):
        return None if v is None else _clean(v, field="table.title", limit=2000)

    @field_validator("columns")
    @classmethod
    def _vcols(cls, v):
        return [_clean(x, field="table.column", limit=2000) for x in v]

    @model_validator(mode="after")
    def _vrows(self):
        if len(self.rows) > MAX_ROWS:
            raise ValueError(f"table exceeds {MAX_ROWS} rows")
        ncols = len(self.columns)
        if len(self.rows) * ncols > MAX_CELLS:
            raise ValueError(f"table exceeds {MAX_CELLS} cells")
        cleaned = []
        for r in self.rows:
            if len(r) != ncols:
                raise ValueError("every table row must match the number of columns")
            cleaned.append([_clean(c, field="table.cell", limit=10000) for c in r])
        self.rows = cleaned
        return self


class PageBreak(_Block):
    type: Literal["page_break"]


Block = Annotated[
    Union[Heading, Paragraph, BulletList, NumberedList, Code, Table, PageBreak],
    Field(discriminator="type"),
]


class DocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = "Untitled"
    author: Optional[str] = None
    date: Optional[str] = None
    blocks: List[Block] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _vt(cls, v):
        return _clean(v, field="title", limit=2000)

    @field_validator("author")
    @classmethod
    def _va(cls, v):
        return None if v is None else _clean(v, field="author", limit=2000)

    @field_validator("date")
    @classmethod
    def _vd(cls, v):
        return None if v is None else _clean(v, field="date", limit=100)

    @model_validator(mode="after")
    def _vblocks(self):
        if len(self.blocks) > MAX_BLOCKS:
            raise ValueError(f"spec exceeds {MAX_BLOCKS} blocks")
        return self

    @classmethod
    def parse(cls, data: dict) -> "DocumentSpec":
        """Build a spec from untrusted JSON-like data, enforcing the byte ceiling."""
        import json
        encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_SPEC_BYTES:
            raise ValueError(f"spec exceeds {MAX_SPEC_BYTES} bytes")
        return cls.model_validate(data)


# Formats a renderer can produce.
FORMATS = ("md", "html", "pdf", "docx", "xlsx", "csv", "zip")
