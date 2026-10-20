"""Bounded text reranking for user-scoped Mem0 retrieval candidates."""

import math
from typing import Any

import httpx


class Mem0Reranker:
    def __init__(self, model: str, url: str, api_key: str, timeout: float) -> None:
        self.model = model
        self.url = url
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            follow_redirects=False,
        )

    def rerank(
        self, query: str, candidates: list[dict[str, Any]], top_k: int
    ) -> list[dict[str, Any]]:
        if len(candidates) < 2:
            return candidates[:top_k]

        documents = [candidate["memory"][:2000] for candidate in candidates]
        response = self.client.post(
            self.url,
            json={
                "model": self.model,
                "input": {"query": query[:2000], "documents": documents},
                "parameters": {"top_n": len(documents)},
            },
        )
        response.raise_for_status()
        payload = response.json()
        results = payload["output"]["results"]
        if not isinstance(results, list) or len(results) != len(candidates):
            raise ValueError("Incomplete Mem0 rerank response")

        scores: dict[int, float] = {}
        for result in results:
            index = result["index"]
            score = result["relevance_score"]
            if (
                type(index) is not int
                or index < 0
                or index >= len(candidates)
                or index in scores
                or type(score) not in (int, float)
                or not math.isfinite(score)
            ):
                raise ValueError("Invalid Mem0 rerank response")
            scores[index] = float(score)

        ranked_indices = sorted(scores, key=lambda index: scores[index], reverse=True)
        return [candidates[index] for index in ranked_indices[:top_k]]

    def close(self) -> None:
        self.client.close()
