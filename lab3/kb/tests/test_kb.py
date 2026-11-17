import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kb.index import build_index, validate
from kb.search import KnowledgeBase

DATA = Path(__file__).resolve().parents[2] / "data"


class KnowledgeBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.source = DATA / "corpus.json"
        cls.corpus = json.loads(cls.source.read_text())
        cls.destination = Path(temporary.name) / "index.sqlite"
        cls.index_version, cls.sha256 = build_index(cls.source, cls.destination)
        cls.kb = KnowledgeBase(cls.destination)

    def test_control_questions(self):
        cases = json.loads((DATA / "control_questions.json").read_text())
        for case in cases:
            with self.subTest(case=case["id"]):
                hits = self.kb.search_docs(case["query"], **case.get("filters", {}))
                keys = [(card.doc_id, card.version) for card in hits.cards]
                self.assertLessEqual(len(keys), 5)
                self.assertEqual(len(keys), len(set(keys)))
                self.assertTrue({tuple(pair) for pair in case["expected"]}.issubset(keys), keys)

    def test_separate_revisions_and_default_filters(self):
        current = self.kb.get_doc("M-05", "2.0")
        archived = self.kb.get_doc("M-05", "1.1")
        self.assertEqual((current.status, archived.status), ("active", "archived"))
        self.assertEqual(current.replaces, "1.1")
        self.assertEqual(current.links[0].model_dump(), {"doc_id": "M-05", "version": "1.1", "anchor": "s01"})
        with self.assertRaises(ValueError):
            self.kb.get_doc("M-05", "9.9")
        result = self.kb.search_docs("M-05", path="инженерия/Группы и каналы")
        self.assertEqual([card.version for card in result.cards], ["2.0"])
        self.assertTrue(all(card.status == "active" for card in self.kb.search_docs("публичные каналы").cards))

    def test_visibility_and_prompt_injection(self):
        self.assertEqual(self.kb.search_docs("SRE-09").cards[0].visibility, "local_only")
        doc = self.kb.get_doc("S-04", "1.0")
        self.assertIn("LINE-INJECTION-DEMO", doc.sections[1].text)
        hit = self.kb.search_docs("S-04").cards[0]
        self.assertNotIn("LINE-INJECTION-DEMO", hit.excerpt)
        self.assertTrue(all(chunk.anchor != "s02" for chunk in hit.chunks))
        for card in self.kb.search_docs("LINE-INJECTION-DEMO").cards:
            self.assertTrue(all("LINE-INJECTION-DEMO" not in chunk.text for chunk in card.chunks))

    def test_tree_and_metadata(self):
        tree = self.kb.list_sections(include_archive=True, include_drafts=True)
        paths = [entry for topic in tree.sections for entry in topic.subsections]
        self.assertEqual(len(tree.sections), 6)
        self.assertEqual(len(paths), 12)
        self.assertEqual(sum(len(path.documents) for path in paths), 36)
        self.assertEqual(self.kb.metadata(), {"index_version": self.index_version, "corpus_sha256": self.sha256})

    def test_missing_processing_label_is_rejected(self):
        invalid = copy.deepcopy(self.corpus)
        invalid[0]["sections"][0].pop("allow_rag")
        with self.assertRaises(ValueError):
            validate(invalid)

    def test_missing_link_target_is_rejected(self):
        invalid = copy.deepcopy(self.corpus)
        invalid[0]["links"][0]["anchor"] = "s99"
        with self.assertRaises(ValueError):
            validate(invalid)

    def test_failed_rebuild_preserves_previous_index(self):
        keys = self.kb.metadata()
        broken = self.destination.with_name("broken.json")
        broken.write_text("[]")
        with self.assertRaises(ValueError):
            build_index(broken, self.destination)
        self.assertEqual(self.kb.metadata(), keys)
