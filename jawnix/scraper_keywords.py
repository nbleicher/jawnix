"""Typed projection of the current GMS/OPS keyword-management surface.

The private Scraper service still owns keyword files, AI generation, winner
ranking, enqueue triggers, and automatic rollover. Its current contract for
those actions is server-rendered HTML and form posts. Jawnix deliberately does
not reimplement those actions; this module only projects the operator-visible
HTML into a small native JSON contract and provides the exact line-list parsing
used for previews and optimistic concurrency checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_KEYWORD_TEXT_LENGTH = 1_000_000


class KeywordRollover(BaseModel):
    enabled: bool
    state: Literal["off", "working", "draining", "ready"]
    label: str
    detail: str
    percent_complete: int = Field(ge=0, le=100)
    posted_jobs: int | None = None
    expected_jobs: int | None = None
    last_status: Literal["generated", "error"] | None = None
    last_event: str | None = None


class KeywordWinner(BaseModel):
    rank: int = Field(ge=1)
    keyword: str
    phone_businesses: int = Field(ge=0)
    businesses: int = Field(ge=0)
    posted_cells: int = Field(ge=0)
    phones_per_cell: float = Field(ge=0)
    phone_rate: float = Field(ge=0)
    last_used: str


class KeywordWorkspace(BaseModel):
    current: list[str]
    version: str
    ai_enabled: bool
    rollover: KeywordRollover
    winners: list[KeywordWinner]
    idle_expires_in: int = Field(gt=0)


class KeywordTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_KEYWORD_TEXT_LENGTH)


class KeywordSaveRequest(KeywordTextRequest):
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_token: str = Field(min_length=1, max_length=2000)
    enqueue: bool = False
    generation_id: str | None = None

    @field_validator("generation_id")
    @classmethod
    def generation_id_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return value


class KeywordGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["broad", "adjacent"] = "broad"
    seed_keyword: str | None = Field(default=None, max_length=200)

    @field_validator("seed_keyword")
    @classmethod
    def normalize_seed(cls, value: str | None) -> str | None:
        value = value.strip() if value else ""
        return value or None


class KeywordRolloverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["enable", "disable"]


class KeywordDiff(BaseModel):
    proposed: list[str]
    added: list[str]
    removed: list[str]
    unchanged: list[str]
    expected_version: str
    review_token: str = ""


class KeywordSaveResult(BaseModel):
    saved: bool = True
    enqueued: bool
    current: list[str]
    version: str
    diff: KeywordDiff


class KeywordGenerationDraft(BaseModel):
    generation_id: str
    mode: Literal["broad", "adjacent"]
    seed_keyword: str | None = None
    keywords: list[str]
    excluded_count: int = Field(ge=0)
    notice: str


def parse_keyword_text(text: str) -> list[str]:
    """Apply the Scraper's supported text-file format exactly.

    Blank lines and ``#`` comments are ignored. Whitespace is trimmed and the
    first spelling of a case-insensitive duplicate wins.
    """

    keywords: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        keyword = line.strip()
        key = keyword.casefold()
        if keyword and not keyword.startswith("#") and key not in seen:
            seen.add(key)
            keywords.append(keyword)
    return keywords


def keyword_version(keywords: list[str]) -> str:
    canonical = "\n".join(keywords) + ("\n" if keywords else "")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_keywords(current: list[str], text: str) -> KeywordDiff:
    proposed = parse_keyword_text(text)
    current_keys = {item.casefold() for item in current}
    proposed_keys = {item.casefold() for item in proposed}
    return KeywordDiff(
        proposed=proposed,
        added=[
            item for item in proposed if item.casefold() not in current_keys
        ],
        removed=[
            item for item in current if item.casefold() not in proposed_keys
        ],
        unchanged=[
            item for item in proposed if item.casefold() in current_keys
        ],
        expected_version=keyword_version(current),
    )


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list["_Node | str"] = field(default_factory=list)

    def text(self) -> str:
        raw = "".join(
            child.text() if isinstance(child, _Node) else child
            for child in self.children
        )
        return " ".join(raw.split())

    def raw_text(self) -> str:
        return "".join(
            child.raw_text() if isinstance(child, _Node) else child
            for child in self.children
        )

    def descendants(self) -> list["_Node"]:
        values: list[_Node] = []
        for child in self.children:
            if isinstance(child, _Node):
                values.append(child)
                values.extend(child.descendants())
        return values

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())


class _TreeParser(HTMLParser):
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
        self.root = _Node("document", {})
        self.stack = [self.root]

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        node = _Node(
            tag,
            {key: value if value is not None else "" for key, value in attrs},
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
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _tree(document: str) -> _Node:
    parser = _TreeParser()
    parser.feed(document)
    parser.close()
    return parser.root


def _find(
    node: _Node,
    *,
    tag: str | None = None,
    node_id: str | None = None,
    class_name: str | None = None,
) -> _Node | None:
    for candidate in node.descendants():
        if tag is not None and candidate.tag != tag:
            continue
        if node_id is not None and candidate.attrs.get("id") != node_id:
            continue
        if class_name is not None and class_name not in candidate.classes():
            continue
        return candidate
    return None


def _find_all(
    node: _Node,
    *,
    tag: str | None = None,
    class_name: str | None = None,
) -> list[_Node]:
    values = []
    for candidate in node.descendants():
        if tag is not None and candidate.tag != tag:
            continue
        if class_name is not None and class_name not in candidate.classes():
            continue
        values.append(candidate)
    return values


def _required(node: _Node | None, label: str) -> _Node:
    if node is None:
        raise ValueError(f"Scraper keyword response omitted {label}.")
    return node


def _rollover_from_tree(root: _Node) -> KeywordRollover:
    section = _required(
        _find(root, tag="section", class_name="keyword-rollover"),
        "automatic rollover status",
    )
    state_class = next(
        (
            value.removeprefix("rollover-")
            for value in section.classes()
            if value.startswith("rollover-")
            and value != "rollover-control"
        ),
        "",
    )
    if state_class not in {"off", "working", "draining", "ready"}:
        raise ValueError("Scraper keyword response has an unknown rollover state.")

    summary = _required(
        _find(section, class_name="rollover-summary"),
        "the rollover summary",
    )
    label = _required(_find(summary, tag="strong"), "the rollover label").text()
    detail = _required(_find(summary, tag="small"), "rollover detail").text()

    meter = _required(
        _find(section, class_name="rollover-meter"),
        "rollover progress",
    )
    percent_text = _required(
        _find(meter, tag="strong"),
        "rollover completion",
    ).text()
    try:
        percent_complete = int(percent_text.rstrip("%"))
    except ValueError as error:
        raise ValueError(
            "Scraper keyword response has invalid rollover completion."
        ) from error

    event = _required(
        _find(section, class_name="rollover-event"),
        "the latest rollover event",
    )
    event_status = _find(event, tag="strong")
    last_status: Literal["generated", "error"] | None = None
    if event_status is not None:
        if "event-generated" in event_status.classes():
            last_status = "generated"
        elif "event-error" in event_status.classes():
            last_status = "error"
    event_time = _find(event, tag="small")
    last_event = event_time.text() if last_status and event_time else None

    control = _required(
        _find(section, tag="button", class_name="rollover-control"),
        "the rollover control",
    )
    enabled = "Disable" in control.text()

    jobs = re.search(
        r"([\d,]+)\s+of\s+([\d,]+)\s+coverage jobs enqueued",
        detail,
        flags=re.IGNORECASE,
    )
    return KeywordRollover(
        enabled=enabled,
        state=state_class,
        label=label,
        detail=detail,
        percent_complete=percent_complete,
        posted_jobs=(
            int(jobs.group(1).replace(",", "")) if jobs else None
        ),
        expected_jobs=(
            int(jobs.group(2).replace(",", "")) if jobs else None
        ),
        last_status=last_status,
        last_event=last_event,
    )


def parse_rollover(document: str) -> KeywordRollover:
    return _rollover_from_tree(_tree(document))


def parse_editor(document: str) -> tuple[list[str], bool, KeywordRollover]:
    root = _tree(document)
    editor = _required(
        _find(root, tag="textarea", node_id="keyword-text"),
        "the keyword editor",
    )
    current = parse_keyword_text(editor.raw_text())

    generate = next(
        (
            node
            for node in _find_all(root, tag="button")
            if node.attrs.get("hx-post") == "/keywords/generate"
            and "Generate 25" in node.text()
        ),
        None,
    )
    ai_enabled = generate is not None and "disabled" not in generate.attrs
    return current, ai_enabled, _rollover_from_tree(root)


def _integer(value: str) -> int:
    return int(value.replace(",", "").strip())


def parse_winners(document: str) -> list[KeywordWinner]:
    root = _tree(document)
    table = _find(root, tag="div", class_name="winners-table")
    if table is None:
        if "No keyword has at least 100 posted cells yet" in root.text():
            return []
        raise ValueError("Scraper keyword response omitted winner rankings.")

    winners: list[KeywordWinner] = []
    tbody = _required(_find(table, tag="tbody"), "winner ranking rows")
    for row in _find_all(tbody, tag="tr"):
        cells = [
            child
            for child in row.children
            if isinstance(child, _Node) and child.tag == "td"
        ]
        if len(cells) != 8:
            raise ValueError("Scraper keyword response has an invalid winner row.")
        button = _required(
            _find(cells[7], tag="button"),
            "the adjacent generation action",
        )
        try:
            values = json.loads(button.attrs["hx-vals"])
            keyword = str(values["seed_keyword"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                "Scraper keyword response has an invalid winner action."
            ) from error
        last_used = _required(
            _find(cells[1], tag="small"),
            "winner last-used time",
        ).text()
        winners.append(
            KeywordWinner(
                rank=_integer(cells[0].text()),
                keyword=keyword,
                phone_businesses=_integer(cells[2].text()),
                businesses=_integer(cells[3].text()),
                posted_cells=_integer(cells[4].text()),
                phones_per_cell=float(cells[5].text()),
                phone_rate=float(cells[6].text().rstrip("%")) / 100,
                last_used=last_used.removeprefix("Last used ").strip(),
            )
        )
    return winners


def parse_generation_draft(
    document: str,
    *,
    generation_id: str,
    mode: Literal["broad", "adjacent"],
    seed_keyword: str | None,
) -> KeywordGenerationDraft:
    root = _tree(document)
    editor = _required(
        _find(root, tag="textarea", node_id="keyword-text"),
        "the generated keyword draft",
    )
    hidden = next(
        (
            node
            for node in _find_all(root, tag="input")
            if node.attrs.get("name") == "generation_id"
        ),
        None,
    )
    if hidden is None or hidden.attrs.get("value") != generation_id:
        raise ValueError("Scraper returned a different keyword generation.")
    notice_node = _required(
        _find(root, class_name="ai-notice"),
        "the generation review notice",
    )
    notice = notice_node.text()
    excluded = re.search(r"(\d+)\s+candidates were filtered", notice)
    keywords = parse_keyword_text(editor.raw_text())
    if not keywords:
        raise ValueError("Scraper returned an empty keyword generation.")
    return KeywordGenerationDraft(
        generation_id=generation_id,
        mode=mode,
        seed_keyword=seed_keyword,
        keywords=keywords,
        excluded_count=int(excluded.group(1)) if excluded else 0,
        notice=notice,
    )


def parse_feedback_error(document: str) -> str | None:
    root = _tree(document)
    error = _find(root, class_name="error")
    return error.text() if error is not None else None
