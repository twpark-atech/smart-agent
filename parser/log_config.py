"""통합 로그 설정 모듈

포맷: 2026-04-10 10:45:12.101 --- [DONE] format_converter
"""
import logging
import time


class ProjectFormatter(logging.Formatter):
    """밀리초 포함 통합 로그 포맷터."""

    converter = time.localtime

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
        return f"{t}.{int(record.msecs):03d}"

    def format(self, record):
        record.asctime = self.formatTime(record)
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            message = f"{message}\n{record.exc_text}"
        return f"{record.asctime} --- {message}"


def setup_logging(level: int = logging.INFO, suppress_access_log: bool = False) -> None:
    """루트 로거에 통합 포맷터를 적용.

    Args:
        level: 루트 로거 레벨 (기본 INFO)
        suppress_access_log: True면 uvicorn.access 로그 비활성화
    """
    formatter = ProjectFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    if suppress_access_log:
        logging.getLogger("uvicorn.access").disabled = True
