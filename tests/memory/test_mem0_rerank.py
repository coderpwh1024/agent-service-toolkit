import json

import httpx
import pytest

from memory.mem0_rerank import Mem0Reranker


def test_rerank_uses_provider_indices_and_scores() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 0, "relevance_score": 0.1},
                        {"index": 2, "relevance_score": 0.9},
                        {"index": 1, "relevance_score": 0.5},
                    ]
                }
            },
        )

    reranker = Mem0Reranker("qwen3.7-text-rerank", "https://example.test/rerank", "test", 2)
    headers = reranker.client.headers.copy()
    reranker.client.close()
    reranker.client = httpx.Client(transport=httpx.MockTransport(respond), headers=headers)
    candidates = [{"memory": text} for text in ("tea", "coffee", "water")]
    try:
        assert reranker.rerank("preferred drink", candidates, 2) == [candidates[2], candidates[1]]
        assert len(requests) == 1
        assert requests[0].headers["authorization"] == "Bearer test"
        body = json.loads(requests[0].read())
        assert body["parameters"]["top_n"] == 3
        assert body["input"]["documents"] == ["tea", "coffee", "water"]
    finally:
        reranker.close()


@pytest.mark.parametrize(
    "results",
    [
        [{"index": 0, "relevance_score": 0.5}],
        [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}],
        [{"index": 0, "relevance_score": 0.5}, {"index": 9, "relevance_score": 0.4}],
    ],
)
def test_rerank_rejects_incomplete_or_invalid_results(results) -> None:
    reranker = Mem0Reranker("rerank", "https://example.test/rerank", "test", 2)
    reranker.client.close()
    reranker.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"output": {"results": results}})
        )
    )
    try:
        with pytest.raises(ValueError, match="Mem0 rerank response"):
            reranker.rerank("query", [{"memory": "a"}, {"memory": "b"}], 2)
    finally:
        reranker.close()
