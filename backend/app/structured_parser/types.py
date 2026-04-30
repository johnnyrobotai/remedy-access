from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

BlockId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]
LanguageCode = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",
    ),
]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
AssetId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
PlacementHint = Literal["lead", "inline", "rail", "before", "after", "footer"]
SourceFormat = Literal["pdf", "docx", "xlsx"]
DocKind = Literal[
    "scientific_article",
    "memo",
    "brochure",
    "reference_doc",
    "form_doc",
    "report",
]
FormControl = Literal[
    "text",
    "email",
    "tel",
    "url",
    "number",
    "date",
    "textarea",
    "checkbox",
    "radio",
    "select",
    "signature",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BlockBase(StrictModel):
    id: BlockId
    type: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    placement: PlacementHint = "inline"

    @model_validator(mode="after")
    def validate_page_range(self) -> BlockBase:
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class ParagraphBlock(BlockBase):
    type: Literal["paragraph"] = "paragraph"
    text: NonEmptyText


class ListItem(StrictModel):
    text: NonEmptyText | None = None
    term: NonEmptyText | None = None
    description: NonEmptyText | None = None
    children: list[ListItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> ListItem:
        has_text = self.text is not None
        has_definition = self.term is not None or self.description is not None
        if has_text == has_definition:
            raise ValueError("list items must define either text or term/description")
        if has_definition and not (self.term and self.description):
            raise ValueError("definition list items require both term and description")
        if self.term and self.children:
            raise ValueError("definition list items cannot contain nested children")
        return self


class ListBlock(BlockBase):
    type: Literal["list"] = "list"
    list_style: Literal["unordered", "ordered", "definition"] = "unordered"
    items: list[ListItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_items(self) -> ListBlock:
        for item in self.items:
            if self.list_style == "definition":
                if item.text is not None:
                    raise ValueError("definition lists require term/description items")
            elif item.text is None:
                raise ValueError("ordered and unordered lists require text items")
        return self


class TableCell(StrictModel):
    text: str = ""
    kind: Literal["header", "data"] = "data"
    scope: Literal["row", "col", "rowgroup", "colgroup"] | None = None
    colspan: int = Field(default=1, ge=1)
    rowspan: int = Field(default=1, ge=1)
    footnote_refs: list[BlockId] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantics(self) -> TableCell:
        if self.kind == "header" and self.scope is None:
            raise ValueError("header cells must declare a scope")
        if self.kind == "data" and self.scope is not None:
            raise ValueError("data cells cannot declare a header scope")
        return self


class TableRow(StrictModel):
    cells: list[TableCell] = Field(min_length=1)


class TableBlock(BlockBase):
    type: Literal["table"] = "table"
    caption: NonEmptyText | None = None
    header_rows: list[TableRow] = Field(default_factory=list)
    body_rows: list[TableRow] = Field(min_length=1)
    footer_rows: list[TableRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_grid(self) -> TableBlock:
        widths = {
            sum(cell.colspan for cell in row.cells)
            for row in [*self.header_rows, *self.body_rows, *self.footer_rows]
        }
        if len(widths) > 1:
            raise ValueError("table rows must span a consistent column count")
        for row in self.header_rows:
            if any(cell.kind != "header" for cell in row.cells):
                raise ValueError("header_rows may only contain header cells")
        return self


class FigureBlock(BlockBase):
    type: Literal["figure"] = "figure"
    asset_id: AssetId
    caption: NonEmptyText
    alt_text_hint: NonEmptyText | None = None
    description_hint: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_accessibility_hints(self) -> FigureBlock:
        if not (self.alt_text_hint or self.description_hint):
            raise ValueError("figures require alt_text_hint or description_hint")
        return self


class CalloutBlock(BlockBase):
    type: Literal["callout"] = "callout"
    tone: Literal["note", "important", "warning", "tip"] = "note"
    title: NonEmptyText | None = None
    body: list[NonEmptyText] = Field(min_length=1)


class QuoteBlock(BlockBase):
    type: Literal["quote"] = "quote"
    text: NonEmptyText
    attribution: NonEmptyText | None = None
    citation: NonEmptyText | None = None


class FieldOption(StrictModel):
    id: BlockId
    label: NonEmptyText
    value: NonEmptyText
    selected: bool = False


class FormFieldSpec(StrictModel):
    id: BlockId
    label: NonEmptyText
    control: FormControl
    name: BlockId | None = None
    help_text: NonEmptyText | None = None
    required: bool = False
    placeholder: NonEmptyText | None = None
    value_hint: NonEmptyText | None = None
    options: list[FieldOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_options(self) -> FormFieldSpec:
        needs_options = self.control in {"radio", "select"}
        allows_options = self.control in {"checkbox", "radio", "select"}
        if needs_options and not self.options:
            raise ValueError(f"{self.control} fields require options")
        if not allows_options and self.options:
            raise ValueError(f"{self.control} fields cannot define options")
        option_ids = {option.id for option in self.options}
        if len(option_ids) != len(self.options):
            raise ValueError(f"{self.id} defines duplicate option ids")
        return self


class FormGroupBlock(BlockBase):
    type: Literal["form_group"] = "form_group"
    legend: NonEmptyText
    caption: NonEmptyText | None = None
    fields: list[FormFieldSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_field_ids(self) -> FormGroupBlock:
        field_ids = {field.id for field in self.fields}
        if len(field_ids) != len(self.fields):
            raise ValueError(f"{self.id} defines duplicate field ids")
        return self


class ReferenceEntry(StrictModel):
    id: BlockId
    label: NonEmptyText | None = None
    text: NonEmptyText
    href: NonEmptyText | None = None


class ReferenceListBlock(BlockBase):
    type: Literal["reference_list"] = "reference_list"
    title: NonEmptyText | None = None
    list_style: Literal["ordered", "unordered"] = "ordered"
    entries: list[ReferenceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entry_ids(self) -> ReferenceListBlock:
        entry_ids = {entry.id for entry in self.entries}
        if len(entry_ids) != len(self.entries):
            raise ValueError(f"{self.id} defines duplicate reference ids")
        return self


class FootnoteEntry(StrictModel):
    id: BlockId
    label: NonEmptyText | None = None
    text: NonEmptyText


class FootnotesBlock(BlockBase):
    type: Literal["footnotes"] = "footnotes"
    title: NonEmptyText | None = "Footnotes"
    entries: list[FootnoteEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entry_ids(self) -> FootnotesBlock:
        entry_ids = {entry.id for entry in self.entries}
        if len(entry_ids) != len(self.entries):
            raise ValueError(f"{self.id} defines duplicate footnote ids")
        return self


class SectionBlock(BlockBase):
    type: Literal["section"] = "section"
    heading: NonEmptyText
    level: int = Field(ge=2, le=6)
    caption: NonEmptyText | None = None
    blocks: list[BodyBlock] = Field(default_factory=list)


BodyBlock: TypeAlias = Annotated[
    SectionBlock
    | ParagraphBlock
    | ListBlock
    | TableBlock
    | FigureBlock
    | CalloutBlock
    | QuoteBlock
    | FormGroupBlock
    | ReferenceListBlock
    | FootnotesBlock,
    Field(discriminator="type"),
]


class ParsedDocument(StrictModel):
    """Canonical AST for structured-render-v2.

    `body` may begin with prose blocks before the first `section` when the
    source document opens directly under the document title.
    """

    schema_version: Literal["structured-render-v2"] = "structured-render-v2"
    source_format: SourceFormat
    title: NonEmptyText
    kicker: NonEmptyText | None = None
    subtitle: NonEmptyText | None = None
    summary: NonEmptyText | None = None
    language: LanguageCode = "en"
    page_count: int = Field(ge=1)
    doc_kind: DocKind = "report"
    body: list[BodyBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document(self) -> ParsedDocument:
        block_ids: set[str] = set()
        footnote_ids: set[str] = set()
        table_footnote_refs: set[str] = set()
        field_ids: set[str] = set()

        def walk(blocks: list[BodyBlock], *, parent_level: int | None = None) -> None:
            for block in blocks:
                if block.id in block_ids:
                    raise ValueError(f"duplicate block id: {block.id}")
                block_ids.add(block.id)

                if isinstance(block, SectionBlock):
                    if parent_level is None:
                        if block.level != 2:
                            raise ValueError("top-level sections must use level 2")
                    elif block.level <= parent_level or block.level > parent_level + 1:
                        raise ValueError(
                            f"section {block.id} must nest exactly one level below its parent"
                        )
                    walk(block.blocks, parent_level=block.level)
                    continue

                if isinstance(block, FormGroupBlock):
                    for field in block.fields:
                        if field.id in field_ids:
                            raise ValueError(f"duplicate form field id: {field.id}")
                        field_ids.add(field.id)
                    continue

                if isinstance(block, FootnotesBlock):
                    for entry in block.entries:
                        if entry.id in footnote_ids:
                            raise ValueError(f"duplicate footnote id: {entry.id}")
                        footnote_ids.add(entry.id)
                    continue

                if isinstance(block, TableBlock):
                    for row in [*block.header_rows, *block.body_rows, *block.footer_rows]:
                        for cell in row.cells:
                            table_footnote_refs.update(cell.footnote_refs)

        walk(self.body)
        missing_footnotes = sorted(table_footnote_refs - footnote_ids)
        if missing_footnotes:
            joined = ", ".join(missing_footnotes)
            raise ValueError(f"unknown footnote refs: {joined}")
        return self


ListItem.model_rebuild()
SectionBlock.model_rebuild()
ParsedDocument.model_rebuild()
