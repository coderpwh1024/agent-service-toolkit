from functools import cache

from langchain_openai import OpenAIEmbeddings

from core.settings import settings


@cache
def get_embedding_model() -> OpenAIEmbeddings:
    if settings.DASHSCOPE_API_KEY is None:
        raise ValueError("DASHSCOPE_API_KEY must be configured for RAG embeddings")
    return OpenAIEmbeddings(
        model=settings.DASHSCOPE_EMBEDDING_MODEL,
        base_url=settings.DASHSCOPE_BASE_URL,
        api_key=settings.DASHSCOPE_API_KEY,
        check_embedding_ctx_length=False,
        chunk_size=10,
    )
