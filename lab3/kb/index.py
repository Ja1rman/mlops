import argparse
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

STATUS = {"active", "archived", "draft"}
VISIBILITY = {"cloud_ok", "local_only"}
TOKEN = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
SUFFIXES = (
    "иями", "ами", "ями", "ого", "ему", "ому", "ыми", "ими",
    "ется", "ются", "ить", "ать", "ять", "ены", "ает", "яет", "ует",
    "лся", "ают", "яют", "тых",
    "ов", "ев", "ей", "ам", "ям", "ах", "ях", "ой", "ый", "ий",
    "ая", "ое", "ую", "ые", "ие", "ия", "ки", "ы", "и", "а",
    "я", "е", "у", "ю", "о", "ь",
)
STOPWORDS = set("а без бы в во для до за и из или к как какой кто ли на не но о об от по под при с со у чем что это".split())


def normalized(value: str) -> str:
    return value.casefold().replace("ё", "е")


def terms(value: str) -> list[str]:
    result = []
    for word in TOKEN.findall(normalized(value)):
        if word in STOPWORDS:
            continue
        if word == "push" or word.startswith(("пуш", "уведомлен")):
            word = "уведомлен"
        elif word.startswith("канал"):
            word = "канал"
        elif word.startswith("подписчик"):
            word = "подписчик"
        elif word.startswith("приемк"):
            word = "приемк"
        elif any("а" <= letter <= "я" for letter in word):
            for suffix in SUFFIXES:
                if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                    word = word[:-len(suffix)]
                    break
        result.append(word)
    return result


def validate(docs: list[dict]) -> None:
    if not isinstance(docs, list) or len(docs) != 36:
        raise ValueError("В корпусе должно быть 36 карточек")
    versions = {}
    active_ids = set()
    topics = set()
    for card in docs:
        doc_id, version = card["doc_id"], card["version"]
        key = (doc_id, version)
        if not re.fullmatch(r"[A-Z]+-[0-9]+", doc_id) or not re.fullmatch(r"\d+\.\d+", version) or key in versions:
            raise ValueError(f"Некорректная или повторяющаяся редакция {key}")
        if card["status"] not in STATUS or card["visibility"] not in VISIBILITY:
            raise ValueError(f"Недопустимая метка статуса или видимости у {key}")
        if card["status"] == "active":
            if doc_id in active_ids:
                raise ValueError(f"Несколько действующих редакций у {doc_id}")
            active_ids.add(doc_id)
        if date.fromisoformat(card["date"]).isoformat() != card["date"]:
            raise ValueError(f"Некорректная дата редакции {key}")
        path = card["path"].split(" / ")
        if len(path) != 2 or any(not part.strip() or "/" in part for part in path):
            raise ValueError(f"Некорректный путь {key}")
        if not card["title"].strip():
            raise ValueError(f"Отсутствует название {key}")
        topics.add(tuple(path))
        anchors = set()
        if not card["sections"]:
            raise ValueError(f"Отсутствуют секции {key}")
        for section in card["sections"]:
            anchor = section["anchor"]
            if not re.fullmatch(r"s\d{2,}", anchor) or anchor in anchors:
                raise ValueError(f"Недопустимый или повторяющийся якорь {key}#{anchor}")
            if not section["heading"].strip() or not section["text"].strip() or type(section.get("allow_rag")) is not bool:
                raise ValueError(f"Неполная секция или отсутствует метка обработки {key}#{anchor}")
            anchors.add(anchor)
        versions[key] = card

    if (len({key[0] for key in versions}), len({topic[0] for topic in topics}), len(topics)) != (35, 6, 12):
        raise ValueError("Ожидаются 35 ID, 6 разделов и 12 подразделов")
    if not {("M-05", "1.1"), ("M-05", "2.0")} <= versions.keys():
        raise ValueError("В корпусе должны быть обе редакции M-05")
    for (doc_id, version), card in versions.items():
        replaces = card["replaces"]
        if replaces is not None:
            previous = versions.get((doc_id, replaces))
            if previous is None or tuple(map(int, replaces.split("."))) >= tuple(map(int, version.split("."))):
                raise ValueError(f"Некорректная ссылка replaces у {(doc_id, version)}")
            if previous["date"] > card["date"]:
                raise ValueError(f"Дата предыдущей редакции позже даты {(doc_id, version)}")
        for link in card.get("links", []):
            linked = versions.get((link["doc_id"], link["version"]))
            if linked is None or all(section["anchor"] != link["anchor"] for section in linked["sections"]):
                raise ValueError(f"Некорректная ссылка в {(doc_id, version)}: {link}")
    if not any(card["status"] == "archived" for card in docs):
        raise ValueError("В корпусе отсутствуют архивные карточки")


def section_chunks(text: str, max_words: int = 100):
    words = text.split()
    if len(words) <= max_words:
        yield text
        return
    for offset in range(0, len(words), max_words):
        yield " ".join(words[offset:offset + max_words])


def build_index(source: Path, destination: Path) -> tuple[str, str]:
    raw = source.read_bytes()
    docs = json.loads(raw)
    validate(docs)
    digest = hashlib.sha256(raw).hexdigest()
    index_version = hashlib.sha256(f"fts-v1:{digest}".encode()).hexdigest()[:12]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".sqlite", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE metadata (index_version TEXT NOT NULL, corpus_sha256 TEXT NOT NULL);
                CREATE TABLE docs (
                    id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, version TEXT NOT NULL,
                    title TEXT NOT NULL, path TEXT NOT NULL, path_key TEXT NOT NULL,
                    status TEXT NOT NULL, visibility TEXT NOT NULL, date TEXT NOT NULL,
                    replaces TEXT, links TEXT NOT NULL,
                    UNIQUE (doc_id, version)
                );
                CREATE TABLE sections (
                    doc INTEGER NOT NULL REFERENCES docs(id), anchor TEXT NOT NULL,
                    heading TEXT NOT NULL, text TEXT NOT NULL,
                    PRIMARY KEY (doc, anchor)
                );
                CREATE TABLE chunks (
                    id INTEGER PRIMARY KEY, doc INTEGER NOT NULL REFERENCES docs(id),
                    chunk_id TEXT NOT NULL UNIQUE, anchor TEXT NOT NULL, heading TEXT NOT NULL, text TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE search USING fts5(title, path, heading, text);
            """)
            db.execute("INSERT INTO metadata VALUES (?, ?)", (index_version, digest))
            for card in docs:
                doc = db.execute("""
                    INSERT INTO docs (doc_id, version, title, path, path_key, status, visibility, date, replaces, links)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    card["doc_id"], card["version"], card["title"], card["path"], normalized(card["path"]),
                    card["status"], card["visibility"], card["date"], card["replaces"], json.dumps(card.get("links", [])),
                )).lastrowid
                for section in card["sections"]:
                    anchor = section["anchor"]
                    db.execute("INSERT INTO sections VALUES (?, ?, ?, ?)", (doc, anchor, section["heading"], section["text"]))
                    if not section["allow_rag"]:
                        continue
                    for n, text in enumerate(section_chunks(section["text"]), 1):
                        chunk_id = f'{card["doc_id"]}@{card["version"]}#{anchor}-{n}'
                        row = db.execute(
                            "INSERT INTO chunks (doc, chunk_id, anchor, heading, text) VALUES (?, ?, ?, ?, ?)",
                            (doc, chunk_id, anchor, section["heading"], text),
                        ).lastrowid
                        db.execute(
                            "INSERT INTO search (rowid, title, path, heading, text) VALUES (?, ?, ?, ?, ?)",
                            (row, *(" ".join(terms(part)) for part in (card["title"], card["path"], section["heading"], text))),
                        )
            db.commit()
            db.execute("INSERT INTO search(search) VALUES ('integrity-check')")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Индекс корпуса не прошёл проверку целостности")
            if not db.execute("SELECT rowid FROM search WHERE search MATCH ?", ('"подписчик"',)).fetchone():
                raise ValueError("Индекс не прошёл пробный полнотекстовый поиск")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return index_version, digest


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверить корпус и атомарно собрать SQLite FTS5")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    index_version, digest = build_index(args.source, args.destination)
    print(json.dumps({"index_version": index_version, "corpus_sha256": digest}))


if __name__ == "__main__":
    main()
