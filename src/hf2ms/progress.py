"""Two transient terminal rows, independent of checkpoint identity and payload storage."""

import logging
import sys
from contextlib import contextmanager

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm


class Progress:
    def __init__(self, mode="auto"):
        self.enabled = mode == "on" or (mode == "auto" and sys.stderr.isatty())
        self.total = None
        self.current = None
        self.path = ""
        self._logs = None

    def start(self, count):
        if not self.enabled:
            return
        self._logs = logging_redirect_tqdm()
        self._logs.__enter__()
        self.total = tqdm(
            total=count,
            desc="Files",
            unit="file",
            position=0,
            dynamic_ncols=True,
            file=sys.stderr,
            leave=True,
        )

    @contextmanager
    def file(self, path, size):
        # Escape control characters in repository-controlled filenames.
        self.path = "".join(c if c.isprintable() else repr(c)[1:-1] for c in path)
        if self.enabled:
            self.current = tqdm(
                total=size,
                desc=self.path,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                position=1,
                dynamic_ncols=True,
                file=sys.stderr,
                leave=False,
            )
        try:
            yield
        finally:
            if self.current is not None:
                self.current.close()
                self.current = None

    def phase(self, label, reset=False):
        if self.current is not None:
            if reset:
                self.current.reset()
            self.current.set_description_str(f"{self.path} [{label}]")

    def update(self, size):
        if self.current is not None:
            self.current.update(size)

    def advance(self):
        if self.total is not None:
            self.total.update(1)

    def info(self, message, *args):
        if not self.enabled:
            logging.getLogger("hf2ms").info(message, *args)

    def close(self):
        if self.current is not None:
            self.current.close()
            self.current = None
        if self.total is not None:
            self.total.close()
            self.total = None
        if self._logs is not None:
            self._logs.__exit__(None, None, None)
            self._logs = None
