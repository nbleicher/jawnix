"""Typed state and grid coverage projected from the GMS/OPS fragments.

The live Scraper exposes this surface as three independently refreshed HTML
fragments. Jawnix parses those fragments at its backend boundary so the React
application receives a narrow JSON contract and never learns upstream routes,
credentials, or markup details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Callable, Generic, Literal, TypeVar

from pydantic import BaseModel, Field


STATE_CARDS_REFRESH_SECONDS = 20
STATE_KEYWORDS_REFRESH_SECONDS = 10
STATE_CELLS_REFRESH_SECONDS = 15

CoverageStatus = Literal["covered", "partial", "uncovered"]
CellStatus = Literal["posted", "reserved", "failed", "uncovered"]
FeedState = Literal["ok", "unavailable"]


class StateCoverageCard(BaseModel):
    state: str
    businesses: int
    posted_cells: int
    total_cells: int
    active_keywords: int
    coverage: int = Field(ge=0, le=100)
    status: CoverageStatus


class StateKeywordActivity(BaseModel):
    keyword: str
    businesses: int
    posted_cells: int
    total_cells: int
    coverage: int = Field(ge=0, le=100)
    empty_rate: float = Field(ge=0)
    last_enqueued: str | None = None


class StateGridCell(BaseModel):
    index: int = Field(ge=1)
    cell: str
    status: CellStatus


class StateGridCoverage(BaseModel):
    cells: list[StateGridCell]
    posted: int
    reserved: int
    failed: int
    uncovered: int


DataT = TypeVar("DataT")


class CoverageFeed(BaseModel, Generic[DataT]):
    state: FeedState
    refresh_seconds: int
    fetched_at: datetime | None = None
    data: DataT | None = None


class StateCoverageSnapshot(BaseModel):
    service_state: Literal["connected", "unavailable"]
    last_successful_at: datetime | None = None
    idle_expires_in: int
    states: CoverageFeed[list[StateCoverageCard]]


class StateCoverageDetail(BaseModel):
    state: str
    service_state: Literal["connected", "degraded", "unavailable"]
    last_successful_at: datetime | None = None
    idle_expires_in: int
    keywords: CoverageFeed[list[StateKeywordActivity]]
    cells: CoverageFeed[StateGridCoverage]


class CoverageContractError(ValueError):
    """The upstream fragment no longer matches the reviewed parity contract."""


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


class _FragmentTree(HTMLParser):
    """Small, dependency-free tree builder for trusted upstream fragments."""

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
        node = _Node(
            tag,
            {key: value or "" for key, value in attrs},
        )
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
    parser = _FragmentTree()
    parser.feed(markup)
    parser.close()
    return parser.root


def _integer(value: str, *, field_name: str) -> int:
    try:
        return int(value.replace(",", "").strip())
    except ValueError:
        raise CoverageContractError(
            f"Scraper coverage {field_name} is not an integer."
        ) from None


def _percentage(value: str, *, field_name: str) -> float:
    try:
        return float(value.strip().removesuffix("%")) / 100
    except ValueError:
        raise CoverageContractError(
            f"Scraper coverage {field_name} is not a percentage."
        ) from None


def _split_count(value: str, *, field_name: str) -> tuple[int, int]:
    parts = value.split("/", 1)
    if len(parts) != 2:
        raise CoverageContractError(
            f"Scraper coverage {field_name} is malformed."
        )
    return (
        _integer(parts[0], field_name=field_name),
        _integer(parts[1], field_name=field_name),
    )


def _coverage_status(posted: int, total: int) -> CoverageStatus:
    if total > 0 and posted >= total:
        return "covered"
    if posted > 0:
        return "partial"
    return "uncovered"


def parse_state_cards(markup: str) -> list[StateCoverageCard]:
    root = _tree(markup)
    grids = root.descendants(
        lambda node: "state-grid" in node.classes
    )
    if len(grids) != 1:
        raise CoverageContractError(
            "Scraper state coverage grid is missing."
        )
    cards = grids[0].descendants(
        lambda node: node.tag == "a" and "state-card" in node.classes
    )
    parsed: list[StateCoverageCard] = []
    for card in cards:
        codes = card.descendants(lambda node: "state-code" in node.classes)
        businesses = card.descendants(lambda node: node.tag == "strong")
        coverage = card.descendants(
            lambda node: "progress-meta" in node.classes
        )
        footers = card.descendants(
            lambda node: "state-card-foot" in node.classes
        )
        if not codes or not businesses or not coverage or not footers:
            raise CoverageContractError(
                "Scraper state card is missing required coverage fields."
            )
        coverage_values = coverage[0].descendants(
            lambda node: node.tag == "b"
        )
        footer_values = footers[0].descendants(
            lambda node: node.tag == "span"
        )
        if not coverage_values or len(footer_values) != 2:
            raise CoverageContractError(
                "Scraper state card counts are malformed."
            )
        posted, total = _split_count(
            footer_values[0].text().removesuffix(" cells"),
            field_name="cell count",
        )
        keyword_text = footer_values[1].text().removesuffix(" keywords")
        percent = round(
            _percentage(
                coverage_values[0].text(),
                field_name="state coverage",
            )
            * 100
        )
        parsed.append(
            StateCoverageCard(
                state=codes[0].text().upper(),
                businesses=_integer(
                    businesses[0].text(),
                    field_name="business count",
                ),
                posted_cells=posted,
                total_cells=total,
                active_keywords=_integer(
                    keyword_text,
                    field_name="active keyword count",
                ),
                coverage=percent,
                status=_coverage_status(posted, total),
            )
        )
    return parsed


def parse_state_keywords(markup: str) -> list[StateKeywordActivity]:
    root = _tree(markup)
    bodies = root.descendants(lambda node: node.tag == "tbody")
    if len(bodies) != 1:
        raise CoverageContractError(
            "Scraper keyword activity table is missing."
        )
    rows = bodies[0].descendants(lambda node: node.tag == "tr")
    parsed: list[StateKeywordActivity] = []
    for row in rows:
        cells = [
            child
            for child in row.children
            if child.tag == "td"
        ]
        if len(cells) == 1 and "table-empty" in cells[0].classes:
            continue
        if len(cells) != 6:
            raise CoverageContractError(
                "Scraper keyword activity row is malformed."
            )
        posted, total = _split_count(
            cells[2].text(),
            field_name="keyword cell count",
        )
        coverage_labels = cells[3].descendants(
            lambda node: node.tag == "small"
        )
        if len(coverage_labels) != 1:
            raise CoverageContractError(
                "Scraper keyword coverage label is missing."
            )
        last_enqueued = cells[5].text()
        parsed.append(
            StateKeywordActivity(
                keyword=cells[0].text(),
                businesses=_integer(
                    cells[1].text(),
                    field_name="keyword business count",
                ),
                posted_cells=posted,
                total_cells=total,
                coverage=round(
                    _percentage(
                        coverage_labels[0].text(),
                        field_name="keyword coverage",
                    )
                    * 100
                ),
                empty_rate=_percentage(
                    cells[4].text(),
                    field_name="keyword empty rate",
                ),
                last_enqueued=(
                    None if last_enqueued == "—" else last_enqueued
                ),
            )
        )
    return parsed


def parse_state_cells(markup: str) -> StateGridCoverage:
    root = _tree(markup)
    grids = root.descendants(
        lambda node: "cell-grid" in node.classes
    )
    if len(grids) != 1:
        raise CoverageContractError(
            "Scraper grid-cell coverage is missing."
        )
    nodes = grids[0].descendants(
        lambda node: node.tag == "span" and "cell" in node.classes
    )
    parsed: list[StateGridCell] = []
    counts = {
        "posted": 0,
        "reserved": 0,
        "failed": 0,
        "uncovered": 0,
    }
    for index, node in enumerate(nodes, start=1):
        statuses = [
            status
            for status in counts
            if f"cell-{status}" in node.classes
        ]
        title = node.attrs.get("title", "")
        if len(statuses) != 1 or " · " not in title:
            raise CoverageContractError(
                "Scraper grid cell is missing its coordinate or status."
            )
        cell, title_status = title.rsplit(" · ", 1)
        status = statuses[0]
        if title_status != status or not cell.strip():
            raise CoverageContractError(
                "Scraper grid cell status does not match its label."
            )
        counts[status] += 1
        parsed.append(
            StateGridCell(
                index=index,
                cell=cell.strip(),
                status=status,
            )
        )
    return StateGridCoverage(cells=parsed, **counts)
