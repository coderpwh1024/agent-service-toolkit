import asyncio
import logging
import sys

import uvicorn

from core import settings

if __name__ == "__main__":
    root_logger = logging.getLogger()
    if root_logger.handlers:
        print(
            f"警告：根日志记录器已配置 {len(root_logger.handlers)} 个处理器。"
            f"basicConfig() 将被忽略。当前日志级别：{logging.getLevelName(root_logger.level)}"
        )
    logging.basicConfig(level=settings.LOG_LEVEL.to_logging_level())

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run(
        "service:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.is_dev(),
        timeout_graceful_shutdown=settings.GRACEFUL_SHUTDOWN_TIMEOUT,
    )
