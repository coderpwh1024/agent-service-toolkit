from langchain_core.documents import Document

from agents import tools


class FakeEmbeddings:
    def embed_query(self, query: str) -> list[float]:
        assert query == "company mission"
        return [1.0, 0.0]


class FakeStore:
    def similarity_search(self, query_embedding, k):
        assert query_embedding == [1.0, 0.0]
        assert k == tools.settings.RAG_TOP_K
        return [
            Document(
                page_content="Build reliable products.",
                metadata={"source": "handbook.pdf", "page": 2},
            )
        ]


def test_database_search_uses_postgres_and_dashscope_embeddings(monkeypatch):
    monkeypatch.setattr(tools, "get_embedding_model", lambda: FakeEmbeddings())
    monkeypatch.setattr(tools, "get_rag_store", lambda: FakeStore())

    result = tools.database_search_func("company mission")

    assert "handbook.pdf#page=3" in result
    assert "Build reliable products." in result


def test_format_contexts_without_page_number():
    result = tools.format_contexts(
        [Document(page_content="Policy text", metadata={"source": "policy.docx"})]
    )
    assert result == "Source: policy.docx\nPolicy text"
