import math
import re

import numexpr
from langchain_core.documents import Document
from langchain_core.tools import BaseTool, tool

from core.settings import settings
from rag import get_embedding_model, get_rag_store


def calculator_func(expression: str) -> str:
    """Calculates a math expression using numexpr.

    Useful for when you need to answer questions about math using numexpr.
    This tool is only for math questions and nothing else. Only input
    math expressions.

    Args:
        expression (str): A valid numexpr formatted math expression.

    Returns:
        str: The result of the math expression.
    """

    try:
        local_dict = {"pi": math.pi, "e": math.e}
        output = str(
            numexpr.evaluate(
                expression.strip(),
                global_dict={},  # restrict access to globals
                local_dict=local_dict,  # add common mathematical functions
            )
        )
        return re.sub(r"^\[|\]$", "", output)
    except Exception as e:
        raise ValueError(
            f'calculator("{expression}") raised error: {e}.'
            " Please try again with a valid numerical expression"
        )


calculator: BaseTool = tool(calculator_func)
calculator.name = "Calculator"


def format_contexts(documents: list[Document]) -> str:
    contexts = []
    for document in documents:
        source = document.metadata.get("source", "unknown")
        page = document.metadata.get("page")
        location = f"{source}#page={page + 1}" if isinstance(page, int) else str(source)
        contexts.append(f"Source: {location}\n{document.page_content}")
    return "\n\n".join(contexts)


def database_search_func(query: str) -> str:
    """Searches PostgreSQL for information in the company's handbook."""
    query_embedding = get_embedding_model().embed_query(query)
    documents = get_rag_store().similarity_search(query_embedding, settings.RAG_TOP_K)
    if not documents:
        return "No relevant documents were found in the PostgreSQL knowledge base."
    return format_contexts(documents)


database_search: BaseTool = tool(database_search_func)
database_search.name = "Database_Search"  # Update name with the purpose of your database
