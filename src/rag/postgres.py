from collections.abc import Sequence
from math import sqrt
from typing import Any

import psycopg
from langchain_core.documents import Document
from psycopg.types.json import Jsonb

from core.settings import DatabaseType, settings
from memory.postgres import get_postgres_connection_string, validate_postgres_config


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Embedding dimensions do not match")
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class PostgresRAGStore:
    def __init__(self, conninfo: str, collection_name: str) -> None:
        self.conninfo = conninfo
        self.collection_name = collection_name

    @staticmethod
    def _setup_cursor(cursor: psycopg.Cursor[Any]) -> None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_documents (
                id BIGSERIAL PRIMARY KEY,
                collection_name TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata JSONB NOT NULL,
                embedding DOUBLE PRECISION[] NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS rag_documents_collection_idx
            ON rag_documents (collection_name)
            """
        )

    def setup(self) -> None:
        with psycopg.connect(self.conninfo) as connection, connection.cursor() as cursor:
            self._setup_cursor(cursor)

    def replace_documents(
        self, documents: Sequence[Document], embeddings: Sequence[Sequence[float]]
    ) -> int:
        if len(documents) != len(embeddings):
            raise ValueError("Documents and embeddings must have the same length")
        with psycopg.connect(self.conninfo) as connection, connection.cursor() as cursor:
            self._setup_cursor(cursor)
            cursor.execute(
                "DELETE FROM rag_documents WHERE collection_name = %s",
                (self.collection_name,),
            )
            cursor.executemany(
                """
                INSERT INTO rag_documents (collection_name, content, metadata, embedding)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (
                        self.collection_name,
                        document.page_content,
                        Jsonb(document.metadata),
                        list(embedding),
                    )
                    for document, embedding in zip(documents, embeddings, strict=True)
                ],
            )
        return len(documents)

    def similarity_search(self, query_embedding: Sequence[float], k: int) -> list[Document]:
        with psycopg.connect(self.conninfo) as connection:
            with connection.cursor() as cursor:
                self._setup_cursor(cursor)
                cursor.execute(
                    """
                    SELECT id, content, metadata, embedding
                    FROM rag_documents
                    WHERE collection_name = %s
                    """,
                    (self.collection_name,),
                )
                rows = cursor.fetchall()

        ranked = sorted(
            rows,
            key=lambda row: cosine_similarity(query_embedding, row[3]),
            reverse=True,
        )[:k]
        return [
            Document(
                page_content=row[1],
                metadata={
                    **row[2],
                    "document_id": row[0],
                    "similarity": cosine_similarity(query_embedding, row[3]),
                },
            )
            for row in ranked
        ]


def get_rag_store(collection_name: str | None = None) -> PostgresRAGStore:
    if settings.DATABASE_TYPE != DatabaseType.POSTGRES:
        raise ValueError("RAG requires DATABASE_TYPE=postgres")
    validate_postgres_config()
    return PostgresRAGStore(
        get_postgres_connection_string(),
        collection_name or settings.RAG_COLLECTION_NAME,
    )
