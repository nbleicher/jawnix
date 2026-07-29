"""Typed projection of the current Scraper database and export surface.

The private Scraper still owns its acquired business records, Niche groupings,
CSV materialization, and stored export files. Its current read contract is
server-rendered HTML. Jawnix parses that reviewed markup at the backend
boundary so the React application receives data, never private routes,
credentials, or upstream error text.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


DATABASE_PAGE_SIZE = 50
STORED_EXPORT_NAME = re.compile(
    r"^[A-Z0-9_-]+(?:_[0-9-]+_[0-9]+)?\.csv$"
)
GENERATED_EXPORT_NAME = re.compile(
    r"^(?:"
    r"[A-Z]{2}-(?:all|uncategorized|[0-9]+-niches|"
    r"[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"|[A-Z]{2}(?:-[A-Z]{2}){0,3}"
    r"|[0-9]+-states"
    r")-phone-leads-[0-9]{4}-[0-9]{2}-[0-9]{2}\.csv$"
)


class DatabaseTotals(BaseModel):
    businesses: int = Field(ge=0)
    unique_phones: int = Field(ge=0)


class DatabaseStateSummary(DatabaseTotals):
    state: str = Field(pattern=r"^[A-Z]{2}$")
    niches: int = Field(ge=0)


class DatabaseBusiness(BaseModel):
    title: str
    phone: str | None = None
    website: str | None = None
    state: str | None = None
    niche: str | None = None
    last_seen: str


class DatabaseBrowsePage(BaseModel):
    records: list[DatabaseBusiness]
    search: str
    state: str
    page: int = Field(ge=1)
    page_size: int = Field(default=DATABASE_PAGE_SIZE, ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
    has_previous: bool
    has_next: bool


class StoredExport(BaseModel):
    filename: str
    size_label: str


class DatabaseWorkspace(BaseModel):
    service_state: Literal["connected", "unavailable"]
    last_successful_at: str | None = None
    idle_expires_in: int = Field(gt=0)
    totals: DatabaseTotals | None = None
    states: list[DatabaseStateSummary] = Field(default_factory=list)
    browse: DatabaseBrowsePage | None = None
    stored_exports: list[StoredExport] = Field(default_factory=list)


class DatabaseNiche(DatabaseTotals):
    key: str
    label: str


class DatabaseStateDetail(BaseModel):
    service_state: Literal["connected", "unavailable"]
    last_successful_at: str | None = None
    idle_expires_in: int = Field(gt=0)
    state: str
    totals: DatabaseStateSummary | None = None
    niches: list[DatabaseNiche] = Field(default_factory=list)


class ExportRegeneration(BaseModel):
    generated: str
    stored_exports: list[StoredExport]


class DatabaseContractError(ValueError):
    """The upstream markup no longer matches the reviewed parity contract."""


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list["_Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        parts = [*self.text_parts]
        for child in self.children:
            parts.append(child.text())
        return " ".join(" ".join(parts).split())

    def descendants(self, predicate: Callable[["_Node"], bool]) -> list["_Node"]:
        found: list[_Node] = []
        for child in self.children:
            if predicate(child):
                found.append(child)
            found.extend(child.descendants(predicate))
        return found


class _MarkupTree(HTMLParser):
    _VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].text_parts.append(data)


def _tree(markup: str) -> _Node:
    parser = _MarkupTree()
    parser.feed(markup)
    parser.close()
    return parser.root


def _integer(value: str, *, field_name: str) -> int:
    match = re.search(r"[0-9][0-9,]*", value)
    if match is None:
        raise DatabaseContractError(
            f"Scraper database {field_name} is not an integer."
        )
    return int(match.group(0).replace(",", ""))


def _direct_cells(row: _Node) -> list[_Node]:
    return [child for child in row.children if child.tag == "td"]


def _safe_website(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _parse_exports(root: _Node) -> list[StoredExport]:
    cards = root.descendants(lambda node: "export-card" in node.classes)
    exports: list[StoredExport] = []
    for card in cards:
        names = card.descendants(lambda node: node.tag == "strong")
        sizes = card.descendants(lambda node: node.tag == "small")
        if len(names) != 1 or len(sizes) != 1:
            raise DatabaseContractError(
                "Scraper stored export card is malformed."
            )
        filename = names[0].text()
        if not STORED_EXPORT_NAME.fullmatch(filename):
            raise DatabaseContractError(
                "Scraper stored export filename is invalid."
            )
        exports.append(
            StoredExport(filename=filename, size_label=sizes[0].text())
        )
    return exports


def parse_database_workspace(
    markup: str,
    *,
    search: str,
    state: str,
    requested_page: int,
) -> tuple[
    DatabaseTotals,
    list[DatabaseStateSummary],
    DatabaseBrowsePage,
    list[StoredExport],
]:
    root = _tree(markup)
    total_regions = root.descendants(
        lambda node: "lead-totals" in node.classes
    )
    if len(total_regions) != 1:
        raise DatabaseContractError(
            "Scraper database totals are missing."
        )
    total_values = total_regions[0].descendants(
        lambda node: node.tag == "b"
    )
    if len(total_values) != 2:
        raise DatabaseContractError(
            "Scraper database totals are malformed."
        )
    totals = DatabaseTotals(
        businesses=_integer(
            total_values[0].text(),
            field_name="business total",
        ),
        unique_phones=_integer(
            total_values[1].text(),
            field_name="unique phone total",
        ),
    )

    state_cards = root.descendants(
        lambda node: (
            node.tag == "a"
            and "database-state-card" in node.classes
        )
    )
    states: list[DatabaseStateSummary] = []
    for card in state_cards:
        codes = card.descendants(lambda node: "state-code" in node.classes)
        businesses = card.descendants(lambda node: node.tag == "strong")
        stats = card.descendants(
            lambda node: "database-card-stats" in node.classes
        )
        if len(codes) != 1 or not businesses or len(stats) != 1:
            raise DatabaseContractError(
                "Scraper database state summary is malformed."
            )
        values = stats[0].descendants(lambda node: node.tag == "b")
        if len(values) != 2:
            raise DatabaseContractError(
                "Scraper database state counts are malformed."
            )
        states.append(
            DatabaseStateSummary(
                state=codes[0].text().upper(),
                businesses=_integer(
                    businesses[0].text(),
                    field_name="state business count",
                ),
                unique_phones=_integer(
                    values[0].text(),
                    field_name="state unique phone count",
                ),
                niches=_integer(
                    values[1].text(),
                    field_name="state Niche count",
                ),
            )
        )

    browse_regions = root.descendants(
        lambda node: node.attrs.get("id") == "database-browse"
    )
    if len(browse_regions) != 1:
        raise DatabaseContractError(
            "Scraper database browse region is missing."
        )
    browse_region = browse_regions[0]
    bodies = browse_region.descendants(lambda node: node.tag == "tbody")
    if len(bodies) != 1:
        raise DatabaseContractError(
            "Scraper database browse table is missing."
        )
    records: list[DatabaseBusiness] = []
    for row in bodies[0].descendants(lambda node: node.tag == "tr"):
        cells = _direct_cells(row)
        if len(cells) == 1 and "table-empty" in cells[0].classes:
            continue
        if len(cells) != 5:
            raise DatabaseContractError(
                "Scraper database business row is malformed."
            )
        titles = cells[0].descendants(lambda node: node.tag == "strong")
        websites = cells[0].descendants(lambda node: node.tag == "a")
        if len(titles) != 1 or len(websites) > 1:
            raise DatabaseContractError(
                "Scraper database business identity is malformed."
            )
        website = websites[0].attrs.get("href") if websites else None
        records.append(
            DatabaseBusiness(
                title=titles[0].text(),
                phone=None if cells[1].text() == "—" else cells[1].text(),
                state=None if cells[2].text() == "—" else cells[2].text(),
                niche=None if cells[3].text() == "—" else cells[3].text(),
                last_seen=cells[4].text(),
                website=_safe_website(website),
            )
        )

    browse_sections = [
        section
        for section in root.descendants(lambda node: node.tag == "section")
        if "Browse records" in section.text()
    ]
    if len(browse_sections) != 1:
        raise DatabaseContractError(
            "Scraper database browse total is missing."
        )
    count_pills = browse_sections[0].descendants(
        lambda node: "count-pill" in node.classes
    )
    if len(count_pills) != 1:
        raise DatabaseContractError(
            "Scraper database browse total is malformed."
        )
    browse_total = _integer(
        count_pills[0].text(),
        field_name="browse total",
    )
    page_labels = browse_region.descendants(
        lambda node: node.tag == "span" and node.text().startswith("Page ")
    )
    if len(page_labels) != 1:
        raise DatabaseContractError(
            "Scraper database pagination is missing."
        )
    rendered_page = _integer(
        page_labels[0].text(),
        field_name="page number",
    )
    if rendered_page != max(1, requested_page):
        raise DatabaseContractError(
            "Scraper database pagination did not honor the requested page."
        )
    pages = max(1, math.ceil(browse_total / DATABASE_PAGE_SIZE))
    browse = DatabaseBrowsePage(
        records=records,
        search=search.strip(),
        state=state.strip().upper(),
        page=rendered_page,
        total=browse_total,
        pages=pages,
        has_previous=rendered_page > 1,
        has_next=rendered_page < pages,
    )
    return totals, states, browse, _parse_exports(root)


def parse_database_state(
    markup: str,
    *,
    expected_state: str,
) -> tuple[DatabaseStateSummary, list[DatabaseNiche]]:
    root = _tree(markup)
    expected_state = expected_state.upper()
    headings = root.descendants(
        lambda node: node.tag == "h1" and node.text().endswith(" database")
    )
    if (
        len(headings) != 1
        or headings[0].text() != f"{expected_state} database"
    ):
        raise DatabaseContractError(
            "Scraper database state detail did not match the requested state."
        )
    summaries = root.descendants(
        lambda node: "database-summary" in node.classes
    )
    if len(summaries) != 1:
        raise DatabaseContractError(
            "Scraper database state totals are missing."
        )
    values = summaries[0].descendants(lambda node: node.tag == "strong")
    if len(values) != 3:
        raise DatabaseContractError(
            "Scraper database state totals are malformed."
        )
    totals = DatabaseStateSummary(
        state=expected_state,
        businesses=_integer(
            values[0].text(),
            field_name="state business total",
        ),
        unique_phones=_integer(
            values[1].text(),
            field_name="state unique phone total",
        ),
        niches=_integer(values[2].text(), field_name="state Niche total"),
    )

    bodies = root.descendants(lambda node: node.tag == "tbody")
    if len(bodies) != 1:
        raise DatabaseContractError(
            "Scraper database Niche table is missing."
        )
    niches: list[DatabaseNiche] = []
    for row in bodies[0].descendants(lambda node: node.tag == "tr"):
        cells = _direct_cells(row)
        if len(cells) == 1 and "table-empty" in cells[0].classes:
            continue
        if len(cells) != 5:
            raise DatabaseContractError(
                "Scraper database Niche row is malformed."
            )
        inputs = cells[0].descendants(
            lambda node: (
                node.tag == "input"
                and "niche-checkbox" in node.classes
            )
        )
        if len(inputs) != 1 or not inputs[0].attrs.get("value"):
            raise DatabaseContractError(
                "Scraper database Niche key is missing."
            )
        niches.append(
            DatabaseNiche(
                key=inputs[0].attrs["value"],
                label=cells[1].text(),
                businesses=_integer(
                    cells[2].text(),
                    field_name="Niche business count",
                ),
                unique_phones=_integer(
                    cells[3].text(),
                    field_name="Niche unique phone count",
                ),
            )
        )
    return totals, niches


def parse_export_regeneration(markup: str) -> ExportRegeneration:
    root = _tree(markup)
    notices = root.descendants(
        lambda node: "notice" in node.classes and "success" in node.classes
    )
    if len(notices) != 1:
        raise DatabaseContractError(
            "Scraper export regeneration confirmation is missing."
        )
    match = re.fullmatch(
        rf"({STORED_EXPORT_NAME.pattern[1:-1]}) regenerated",
        notices[0].text(),
    )
    if match is None:
        raise DatabaseContractError(
            "Scraper export regeneration filename is invalid."
        )
    return ExportRegeneration(
        generated=match.group(1),
        stored_exports=_parse_exports(root),
    )
