import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


class SearchEngine:
    def __init__(self, artifact_dir: Path, ranking_mode: str = "normal") -> None:
        bundle = joblib.load(artifact_dir / "search-index.joblib")
        self.vectorizer = bundle["vectorizer"]
        self.matrix = bundle["matrix"]
        self.documents = bundle["documents"]
        self.ranking_mode = ranking_mode
        self.by_id = {document["id"]: index for index, document in enumerate(self.documents)}
        self.metadata = json.loads((artifact_dir / "metadata.json").read_text())

    def _rank(self, scores: np.ndarray) -> np.ndarray:
        if self.ranking_mode == "reverse":
            return np.argsort(scores)
        return np.argsort(-scores)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        query_vector = self.vectorizer.transform([query])
        scores = cosine_similarity(query_vector, self.matrix).ravel()
        return [self._result(index, scores[index]) for index in self._rank(scores)[:limit]]

    def recommend(self, document_id: str, limit: int = 3) -> list[dict]:
        if document_id not in self.by_id:
            raise KeyError(document_id)
        index = self.by_id[document_id]
        scores = cosine_similarity(self.matrix[index], self.matrix).ravel()
        ranked = self._rank(scores)
        return [self._result(i, scores[i]) for i in ranked[ranked != index][:limit]]

    def _result(self, index: int, score: float) -> dict:
        document = self.documents[int(index)]
        return {
            "id": document["id"],
            "title": document["title"],
            "tags": document["tags"],
            "score": round(float(score), 6),
            "summary": document["body"][:240],
        }
