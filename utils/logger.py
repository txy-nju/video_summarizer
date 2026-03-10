
import logging
import sys

def setup_logger(name: str = "video_summarizer", level: int = logging.INFO) -> logging.Logger:
    """
    配置并返回一个日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
