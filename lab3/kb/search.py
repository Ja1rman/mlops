import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from kb.index import normalized, terms

Status = Literal["active", "archived", "draft"]
Visibility = Literal["cloud_ok", "local_only"]
Query = Annotated[str, Field(min_length=1, max_length=600)]
Limit = Annotated[int, Field(ge=1, le=5)]
EXACT_CODE = re.compile(r"(?<![\w-])[a-z]+-\d+(?![\w-])", re.IGNORECASE | re.ASCII)


class Section(BaseModel):
    anchor: str
    heading: str
    text: str


class Chunk(Section):
    chunk_id: str


class DocSummary(BaseModel):
    doc_id: str
    version: str
    title: str
    path: str
    status: Status
    visibility: Visibility
    date: str


class DocLink(BaseModel):
    doc_id: str
    version: str
    anchor: str


class Document(DocSummary):
    replaces: str | None
    links: list[DocLink]
    sections: list[Section]


class CardHit(DocSummary):
    anchor: str
    excerpt: str
    chunks: list[Chunk]


class SearchResults(BaseModel):
    index_version: str
    cards: list[CardHit]


class Subsection(BaseModel):
    title: str
    path: str
    documents: list[DocSummary]


class Topic(BaseModel):
    title: str
    subsections: list[Subsection]


class SectionTree(BaseModel):
    sections: list[Topic]


def statuses(status: Status | None, version: str | None, include_archive: bool, include_drafts: bool) -> tuple[str, ...]:
    if status:
        return (status,)
    if version:
        return ("active", "archived", "draft")
    visible = ["active"]
    if include_archive:
        visible.append("archived")
    if include_drafts:
        visible.append("draft")
    return tuple(visible)


class KnowledgeBase:
    def __init__(self, db_path: Path):
        self.db_path = db_path.resolve()

    def connect(self):
        db = sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return closing(db)

    def metadata(self) -> dict[str, str]:
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM metadata").fetchone())

    def search_docs(
        self,
        query: Query,
        path: str | None = None,
        version: str | None = None,
        status: Status | None = None,
        include_archive: bool = False,
        include_drafts: bool = False,
        limit: Limit = 5,
    ) -> SearchResults:
        allowed = statuses(status, version, include_archive, include_drafts)
        filters = [f"d.status IN ({','.join('?' for _ in allowed)})"]
        params = list(allowed)
        if version is not None:
            filters.append("d.version = ?")
            params.append(version.strip())
        if path is not None:
            path_key = " / ".join(normalized(part.strip()) for part in path.split("/"))
            filters.append("(d.path_key = ? OR instr(d.path_key, ?) = 1)")
            params.extend((path_key, f"{path_key} / "))

        codes = tuple(dict.fromkeys(code.upper() for code in EXACT_CODE.findall(query)))
        tokens = dict.fromkeys(terms(query))
        match = " OR ".join(f'"{token}"' for token in tokens)
        with self.connect() as db:
            db.execute("BEGIN")
            index_version = db.execute("SELECT index_version FROM metadata").fetchone()[0]
            if not match:
                return SearchResults(index_version=index_version, cards=[])

            where = " AND ".join(filters)
            hits = []
            if codes:
                code_slots = ",".join("?" for _ in codes)
                hits = db.execute(f"""
                    SELECT d.*, c.chunk_id, c.anchor, c.heading, c.text
                    FROM docs AS d JOIN chunks AS c ON c.doc = d.id
                    WHERE {where} AND d.doc_id IN ({code_slots})
                    ORDER BY d.doc_id, d.status != 'active', d.version, c.id
                """, [*params, *codes]).fetchall()
                if not hits:
                    phrases = [" ".join(terms(code)) for code in codes]
                    match = " OR ".join(f'"{phrase}"' for phrase in phrases)

            if not hits:
                # FTS expressions are constructed from lexical tokens. SQL values stay parameterized.
                hits = db.execute(f"""
                    SELECT d.*, c.chunk_id, c.anchor, c.heading, c.text
                    FROM search JOIN chunks AS c ON c.id = search.rowid
                    JOIN docs AS d ON d.id = c.doc
                    WHERE {where} AND search MATCH ?
                    ORDER BY bm25(search, 4.0, 0.25, 2.0, 1.0), d.doc_id, d.version, c.id
                """, [*params, match]).fetchall()

        cards = {}
        for row in hits:
            key = (row["doc_id"], row["version"])
            if key not in cards:
                if len(cards) >= limit:
                    continue
                cards[key] = CardHit.model_validate({**dict(row), "excerpt": row["text"], "chunks": []})
            chunks = cards[key].chunks
            if len(chunks) < 3:
                chunks.append(Chunk.model_validate(dict(row)))
        return SearchResults(index_version=index_version, cards=list(cards.values()))

    def get_doc(self, doc_id: str, version: str) -> Document:
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT * FROM docs WHERE doc_id = ? AND version = ?", (doc_id.upper(), version)
            ).fetchone()
            if row is None:
                raise ValueError(f"Редакция {doc_id}@{version} не найдена")
            sections = db.execute(
                "SELECT anchor, heading, text FROM sections WHERE doc = ? ORDER BY rowid", (row["id"],)
            ).fetchall()
            return Document.model_validate({
                **dict(row),
                "links": json.loads(row["links"]),
                "sections": [dict(section) for section in sections],
            })

    def list_sections(self, include_archive: bool = False, include_drafts: bool = False) -> SectionTree:
        allowed = statuses(None, None, include_archive, include_drafts)
        with self.connect() as db:
            rows = db.execute("""
                SELECT doc_id, version, title, path, status, visibility, date
                FROM docs ORDER BY path, title, doc_id, version
            """).fetchall()
        tree = {}
        for row in rows:
            topic, title = row["path"].split(" / ")
            documents = tree.setdefault(topic, {}).setdefault(title, [])
            if row["status"] in allowed:
                documents.append(DocSummary.model_validate(dict(row)))
        return SectionTree(sections=[
            Topic(title=topic, subsections=[
                Subsection(title=title, path=f"{topic} / {title}", documents=documents)
                for title, documents in subsections.items()
            ])
            for topic, subsections in tree.items()
        ])
