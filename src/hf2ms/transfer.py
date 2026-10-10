"""Transfer engine and public Python entry point."""

import base64
import fnmatch
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

import requests

from .http import CheckedStream, HTTPError, RetryableTransferError, TransferError
from .hubs import HuggingFace, ModelScope
from .state import Cache
from .progress import Progress

log = logging.getLogger("hf2ms")
INLINE_LIMIT = 1024 * 1024
METADATA = {"README.MD", ".GITATTRIBUTES", "CONFIGURATION.JSON"}


@dataclass
class TransferOptions:
    source: str
    destination: str
    repo_type: str = "auto"
    hf_revision: str = "main"
    ms_revision: str = "master"
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "hf2ms")
    cache_max_size: int = 256 * 1024 * 1024
    visibility: str = "auto"
    on_conflict: str = "overwrite"
    restart: bool = False
    dry_run: bool = False
    hf_endpoint: str = "https://huggingface.co"
    ms_endpoint: str = "https://modelscope.cn"
    chunk_size: int = 1024 * 1024
    attempts: int = 3
    timeout: int = 120
    progress: str = "auto"
    include: Tuple[str, ...] = ()
    exclude: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.progress not in ("auto", "on", "off"):
            raise ValueError("progress must be auto, on or off")
        if self.repo_type not in ("auto", "model", "dataset"):
            raise ValueError("repo_type must be auto, model or dataset")
        if self.visibility not in ("auto", "private", "public"):
            raise ValueError("Invalid visibility")
        if self.on_conflict not in ("overwrite", "error", "skip"):
            raise ValueError("Invalid conflict policy")
        if self.cache_max_size < 65536:
            raise ValueError("cache_max_size must be at least 64 KiB")
        if not self.hf_revision or not self.ms_revision:
            raise ValueError("Revisions cannot be empty")
        if self.chunk_size <= 0 or self.attempts <= 0 or self.timeout <= 0:
            raise ValueError("chunk_size, attempts and timeout must be positive")


class Transfer:
    def __init__(self, options: TransferOptions, ms_token: str, hf_token: Optional[str] = None):
        if not ms_token or not ms_token.strip():
            raise ValueError("A non-empty ms_token is required")
        self.options = options
        self.progress = Progress(options.progress)
        self.source = HuggingFace(
            options.source, options.repo_type, hf_token, options.hf_endpoint, options.timeout
        )
        self.target = ModelScope(
            options.destination,
            options.repo_type,
            ms_token,
            options.ms_revision,
            options.ms_endpoint,
            options.timeout,
        )

    def _retry(self, operation):
        for attempt in range(self.options.attempts):
            try:
                return operation()
            except (HTTPError, RetryableTransferError, requests.RequestException) as exc:
                retryable = not isinstance(exc, HTTPError) or exc.status in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                )
                if not retryable or attempt + 1 == self.options.attempts:
                    raise
                delay = min(2**attempt, 30)
                if isinstance(exc, HTTPError) and exc.retry_after:
                    try:
                        delay = min(max(delay, float(exc.retry_after)), 300)
                    except ValueError:
                        pass
                reason = str(exc) if isinstance(exc, TransferError) else "Network request failure"
                log.warning(
                    "%s; retrying attempt %s/%s in %ss",
                    reason,
                    attempt + 2,
                    self.options.attempts,
                    delay,
                )
                time.sleep(delay)

    def _identity(self):
        opt = self.options
        identity = {
            key: getattr(opt, key)
            for key in (
                "source",
                "destination",
                "hf_revision",
                "ms_revision",
                "hf_endpoint",
                "ms_endpoint",
                "visibility",
                "on_conflict",
            )
        }
        identity.update(
            repo_type=self.source.repo_type, include=list(opt.include), exclude=list(opt.exclude)
        )
        return identity

    def _selected(self, commit):
        opt = self.options
        for file in self.source.files(commit):
            if opt.include and not any(fnmatch.fnmatchcase(file.path, p) for p in opt.include):
                continue
            if any(fnmatch.fnmatchcase(file.path, p) for p in opt.exclude):
                continue
            if file.path.rsplit("/", 1)[-1].upper() in METADATA and file.size > INLINE_LIMIT:
                raise TransferError("Repository metadata exceeds the 1 MiB inline limit")
            yield file

    def _comparison(self, file, remote, commit, state, dry_run=False):
        if remote is None:
            return "create", file.sha256
        if remote.get("directory"):
            return "directory", file.sha256
        record = state.file(file.path) if state else None
        sha = file.sha256 or (record[1] if record and record[0] == file.size else "")
        if remote.get("size") is not None and remote["size"] != file.size:
            return "overwrite", sha
        if not remote.get("sha256"):
            return "unknown", sha
        if not sha:
            if dry_run:
                return "unknown", ""
            sha, _ = self._retry(lambda: self._read(file, commit))
            state.mark(file.path, file.size, sha, "hashed")
        return ("same" if sha == remote["sha256"] else "overwrite"), sha

    def _visibility(self):
        if self.options.visibility != "auto":
            return self.options.visibility
        # If metadata is incomplete, never infer that the source is public.
        info = self.source.info
        return "public" if info.get("private") is False and not info.get("gated") else "private"

    def run(self):
        opt = self.options
        cache = Cache(opt.cache_dir, opt.cache_max_size)
        try:
            self.target.repo_type = self._retry(self.source.detect)
            identity = self._identity()
            exists = self._retry(self.target.repo_exists)
            if exists:
                self._retry(self.target.check_revision)
            elif opt.ms_revision != "master":
                raise TransferError(
                    "New MS repositories start on master; create the requested "
                    "branch first or use --ms-revision master"
                )
            context = cache.read(identity) if opt.dry_run else cache.open(identity)
            with context as state:
                pinned = state.get("source_commit") if state else None
                complete = state.get("complete") if state else None
                commit = (
                    pinned
                    if pinned and not complete and not opt.restart
                    else self._retry(lambda: self.source.resolve(opt.hf_revision))
                )
                if state and not opt.dry_run:
                    if commit != pinned or opt.restart:
                        state.reset(commit)
                    state.set("complete", False)
                # Completed state for an older source commit must not inform comparisons.
                read_state = state if commit == pinned and not opt.restart else None
                log.info(
                    "HF %s %s @ %s -> MS %s @ %s",
                    self.source.repo_type,
                    opt.source,
                    commit,
                    opt.destination,
                    opt.ms_revision,
                )
                if opt.dry_run:
                    return self._preview(commit, exists, read_state)
                if opt.on_conflict == "error" and exists:
                    conflicts = 0
                    for file in self._selected(commit):
                        remote = self._retry(lambda file=file: self.target.file(file.path))
                        decision, _ = self._comparison(file, remote, commit, state)
                        if decision not in ("create", "same"):
                            conflicts += 1
                            log.error("Conflict (%s): %s", decision, file.path)
                        cache.enforce(state.path)
                    if conflicts:
                        raise TransferError(f"{conflicts} conflict(s); no remote writes performed")
                    self.target._cached_root = None
                if not exists:
                    card = self.source.info.get("cardData")
                    license_name = card.get("license") if isinstance(card, dict) else None
                    if not isinstance(license_name, str):
                        license_name = "Other"
                    self._retry(lambda: self.target.create_repo(self._visibility(), license_name))
                    log.info("Created MS repository: %s (%s)", opt.destination, self._visibility())
                if self.progress.enabled:
                    self.progress.start(self._retry(lambda: sum(1 for _ in self._selected(commit))))
                counts = {"committed": 0, "skipped": 0}
                for file in self._selected(commit):
                    with self.progress.file(file.path, file.size):
                        remote = (
                            self._retry(lambda file=file: self.target.file(file.path))
                            if exists
                            else None
                        )
                        decision, sha = self._comparison(file, remote, commit, state)
                        if decision == "same":
                            state.mark(file.path, file.size, sha, "committed")
                            counts["skipped"] += 1
                            self.progress.phase("Skipped")
                            self.progress.info("Skip identical: %s", file.path)
                        elif decision != "create" and opt.on_conflict == "skip":
                            counts["skipped"] += 1
                            self.progress.phase("Skipped conflict")
                            self.progress.info("Skip conflict (%s): %s", decision, file.path)
                        elif decision == "directory":
                            raise TransferError("Destination is a directory: " + file.path)
                        elif decision != "create" and opt.on_conflict == "error":
                            raise TransferError("Destination changed after preflight: " + file.path)
                        else:
                            self.progress.info(
                                "Transfer (%s): %s (%s bytes)", decision, file.path, file.size
                            )
                            self._retry(lambda file=file: self._file(file, commit, state))
                            counts["committed"] += 1
                        cache.enforce(state.path)
                    self.progress.advance()
                state.set("complete", True)
                cache.enforce(state.path)
                return counts
        finally:
            self.progress.close()
            self.source.close()
            self.target.close()

    def _preview(self, commit, exists, state):
        report = {
            "dry_run": True,
            "source_commit": commit,
            "repo_type": self.source.repo_type,
            "ms_revision": self.options.ms_revision,
            "create_repo": not exists,
            "visibility": self._visibility() if not exists else None,
            "files": 0,
            "bytes": 0,
            "create": 0,
            "same": 0,
            "overwrite": 0,
            "unknown": 0,
            "directory": 0,
            "would_commit": 0,
            "would_skip": 0,
            "would_error": 0,
        }
        if not exists:
            log.info("Would create MS repository (%s)", report["visibility"])
        for file in self._selected(commit):
            remote = self._retry(lambda file=file: self.target.file(file.path)) if exists else None
            decision, _ = self._comparison(file, remote, commit, state, dry_run=True)
            report[decision] += 1
            report["files"] += 1
            report["bytes"] += file.size
            policy = self.options.on_conflict
            if decision == "same" or (decision != "create" and policy == "skip"):
                action = "skip"
            elif decision == "directory" or (decision != "create" and policy == "error"):
                action = "error"
            else:
                action = "commit"
            report["would_" + action] += 1
            log.info("Plan %s -> %s: %s (%s bytes)", decision, action, file.path, file.size)
        report["on_conflict"] = self.options.on_conflict
        return report

    def _read(self, file, commit, sha256="", collect=False):
        self.progress.phase("HF read" if collect else "Hashing", reset=True)
        with self.source.open(file, commit) as response:
            stream = CheckedStream(
                response.raw,
                file.size,
                sha256,
                self.options.chunk_size,
                on_read=self.progress.update,
            )
            chunks = []
            while True:
                data = stream.read()
                if not data:
                    break
                if collect:
                    chunks.append(data)
            stream.verify()
            return stream.hash.hexdigest(), b"".join(chunks) if collect else None

    def _file(self, file, commit, state):
        self.progress.phase("Preparing", reset=True)
        record = state.file(file.path)
        sha256 = file.sha256 or (record[1] if record else "")
        inline = file.size <= INLINE_LIMIT and not file.sha256
        if file.path.rsplit("/", 1)[-1].upper() in METADATA:
            if file.size > INLINE_LIMIT:
                raise TransferError("Repository metadata exceeds the 1 MiB inline limit")
            inline = True
        operation = {"action": "create", "path": file.path, "size": file.size}
        if inline:
            sha256, data = self._read(file, commit, sha256, collect=True)
            operation.update(
                type="normal",
                sha256="",
                encoding="base64",
                content=base64.b64encode(data).decode("ascii"),
            )
        else:
            if not sha256:
                self.progress.info("No HF SHA-256; streaming hash pass: %s", file.path)
                sha256, _ = self._read(file, commit)
                state.mark(file.path, file.size, sha256, "hashed")
            actions = self.target.negotiate(sha256, file.size)
            if actions is not None:
                self.progress.phase("HF -> MS", reset=True)
                with self.source.open(file, commit) as response:
                    stream = CheckedStream(
                        response.raw,
                        file.size,
                        sha256,
                        self.options.chunk_size,
                        on_read=self.progress.update,
                    )
                    self.target.upload(actions, stream, sha256, file.size)
            else:
                self.progress.phase("Reused")
                self.progress.info("Reuse complete blob: %s", file.path)
            state.mark(file.path, file.size, sha256, "uploaded")
            operation.update(type="lfs", sha256=sha256, encoding="", content="")
        self.progress.phase("Committing")
        self.target.commit(operation)
        state.mark(file.path, file.size, sha256, "committed")
