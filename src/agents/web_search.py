"""Bound synchronous searches without blocking the request or cancellation path."""

import asyncio
from functools import partial
from math import ceil
from threading import BoundedSemaphore
from typing import Any

from ddgs import DDGS
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
from langchain_core.callbacks import AsyncCallbackManagerForToolRun

from core import settings

_SEARCH_CAPACITY = BoundedSemaphore(4)


class SearchBusyError(RuntimeError):
    pass


class TimedSearchAPIWrapper(DuckDuckGoSearchAPIWrapper):
    def _ddgs_text(self, query: str, max_results: int | None = None) -> list[dict[str, str]]:
        with DDGS(timeout=ceil(settings.RESEARCH_SEARCH_TIMEOUT_SECONDS)) as search:
            return search.text(
                query,
                region=self.region,
                safesearch=self.safesearch,
                timelimit=self.time,
                max_results=max_results or self.max_results,
                backend=self.backend,
            )


class BoundedWebSearch(DuckDuckGoSearchResults):
    async def _arun(
        self,
        query: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> tuple[Any, list[dict[str, str]]]:
        if not _SEARCH_CAPACITY.acquire(blocking=False):
            raise SearchBusyError("Web search capacity is temporarily exhausted")
        try:
            future = asyncio.get_running_loop().run_in_executor(None, partial(self._run, query))
        except BaseException:
            _SEARCH_CAPACITY.release()
            raise

        # A cancelled await cannot stop DDGS's worker; reserve capacity until it actually exits.
        def release(done: asyncio.Future) -> None:
            _SEARCH_CAPACITY.release()
            if not done.cancelled():
                done.exception()

        future.add_done_callback(release)
        async with asyncio.timeout(settings.RESEARCH_SEARCH_TIMEOUT_SECONDS):
            return await asyncio.shield(future)
