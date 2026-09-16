import pytest

from rag.postgres import cosine_similarity


def test_cosine_similarity():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_rejects_dimension_mismatch():
    with pytest.raises(ValueError, match="Embedding dimensions do not match"):
        cosine_similarity([1.0], [1.0, 0.0])
