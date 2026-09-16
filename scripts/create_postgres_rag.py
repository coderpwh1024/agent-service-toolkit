"""Build the PostgreSQL RAG collection from local PDF and Word documents."""

import argparse
import sys
from pathlib import Path

from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core.settings import settings  # noqa: E402
from rag import get_embedding_model, get_rag_store  # noqa: E402


def load_documents(folder_path: Path):
    documents = []
    for file_path in sorted(folder_path.iterdir()):
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            loader = PyPDFLoader(str(file_path))
        elif suffix == ".docx":
            loader = Docx2txtLoader(str(file_path))
        else:
            continue
        documents.extend(loader.load())
    return documents


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", default="data")
    parser.add_argument("--collection", default=settings.RAG_COLLECTION_NAME)
    parser.add_argument("--chunk-size", type=int, default=2000)
    parser.add_argument("--overlap", type=int, default=500)
    args = parser.parse_args()

    folder_path = Path(args.folder)
    documents = load_documents(folder_path)
    if not documents:
        raise SystemExit(f"No PDF or DOCX documents found in {folder_path}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
    )
    chunks = splitter.split_documents(documents)
    embeddings = get_embedding_model().embed_documents(
        [document.page_content for document in chunks]
    )
    count = get_rag_store(args.collection).replace_documents(chunks, embeddings)
    print(f"Stored {count} chunks in PostgreSQL collection {args.collection!r}")


if __name__ == "__main__":
    main()
