"""Cooperative cancellation shared by catalog, download and local file scans."""
import threading
from contextlib import contextmanager

class Cancelled(BaseException):
    pass

_current = threading.local()

@contextmanager
def scope(event):
    previous = getattr(_current, 'event', None)
    _current.event = event
    try:
        check()
        yield
    finally:
        _current.event = previous

def check():
    event = getattr(_current, 'event', None)
    if event is not None and event.is_set():
        raise Cancelled('任务已取消，已完成文件保留')
