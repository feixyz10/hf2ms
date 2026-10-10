"""The hf2ms console entry point."""

import argparse
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

import requests
from filelock import Timeout

from . import __version__
from .http import TransferError
from .transfer import Transfer, TransferOptions

log = logging.getLogger("hf2ms")


def byte_size(value):
    match = re.fullmatch(r"([0-9]+)\s*(B|KiB|MiB|GiB|KB|MB|GB)?", value, re.IGNORECASE)
    if not match:
        raise argparse.ArgumentTypeError("Use bytes or an integer with KiB/MiB/GiB (e.g. 256MiB)")
    unit = (match[2] or "B").upper()
    multiplier = {
        "B": 1,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
    }[unit]
    result = int(match[1]) * multiplier
    if result <= 0:
        raise argparse.ArgumentTypeError("Size must be positive")
    return result


def nonempty_token(value):
    if not value.strip() or any(c in value for c in "\r\n"):
        raise argparse.ArgumentTypeError("Token must be non-empty and contain no line breaks")
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        description="Stream HF models/datasets to ModelScope; create missing repos and update files."
    )
    parser.add_argument("source", help="HF owner/repository")
    parser.add_argument("destination", help="ModelScope owner/repository")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--ms-token",
        required=True,
        type=nonempty_token,
        help='Required MS write token; e.g. --ms-token "$MODELSCOPE_API_TOKEN"',
    )
    parser.add_argument(
        "--hf-token",
        type=nonempty_token,
        default=os.getenv("HF_TOKEN") or None,
        help="Optional HF read token (default: HF_TOKEN); needed for private/gated repos",
    )
    parser.add_argument(
        "--repo-type",
        choices=("auto", "model", "dataset"),
        default="auto",
        help="Source repository type (default: auto). Auto probes HF models and datasets, "
        "uses the unique accessible match, and requires an explicit type if both exist.",
    )
    parser.add_argument(
        "--hf-revision", default="main", help="HF branch/tag/commit (default: main)"
    )
    parser.add_argument(
        "--ms-revision", default="master", help="MS writable branch (default: master)"
    )
    parser.add_argument(
        "--ms-visibility",
        dest="visibility",
        choices=("auto", "private", "public"),
        default="auto",
        help="New repo only; auto: public source -> public, private/gated -> private",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "hf2ms",
        help="Checkpoint directory (default: ~/.cache/hf2ms)",
    )
    parser.add_argument(
        "--cache-max-size",
        type=byte_size,
        default=256 * 1024**2,
        help="Cache budget; evict oldest completed jobs only (default: 256MiB)",
    )
    parser.add_argument("--hf-endpoint", default=os.getenv("HF_ENDPOINT", "https://huggingface.co"))
    parser.add_argument(
        "--ms-endpoint", default=os.getenv("MODELSCOPE_ENDPOINT", "https://modelscope.cn")
    )
    parser.add_argument(
        "--chunk-size",
        type=byte_size,
        default=1024**2,
        help="Streaming read buffer size (default: 1MiB)",
    )
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120, help="Socket read timeout seconds")
    parser.add_argument(
        "--include", action="extend", nargs="+", default=[], help="Include glob(s); repeatable"
    )
    parser.add_argument(
        "--exclude", action="extend", nargs="+", default=[], help="Exclude glob(s); takes priority"
    )
    parser.add_argument(
        "--on-conflict",
        choices=("overwrite", "error", "skip"),
        default="overwrite",
        help="Different/unknown existing content (default: overwrite)",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Ignore local progress, resolve source again; remote identical blobs still reused",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only plan; no payload downloads, remote writes or cache changes",
    )
    return parser


def main(argv=None):
    args = vars(build_parser().parse_args(argv))
    ms_token, hf_token = args.pop("ms_token"), args.pop("hf_token")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = Transfer(TransferOptions(**args), ms_token=ms_token, hf_token=hf_token).run()
    except KeyboardInterrupt:
        log.info("Interrupted. Run the same command to resume.")
        return 130
    except Timeout:
        log.error("Another process is using this task/cache; retry after it finishes")
        return 1
    except requests.RequestException:
        log.error("Network request failed. Run the same command to resume.")
        return 1
    except sqlite3.Error:
        log.error("Checkpoint database error; check cache permissions and available disk space")
        return 1
    except (TransferError, ValueError, OSError) as exc:
        log.error("%s", exc)
        return 1
    if result.get("dry_run"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        log.info("Done: %s committed, %s skipped", result["committed"], result["skipped"])
    return 0
