# hf2ms

**English** | [简体中文](README_CN.md)

Stream Hugging Face models and datasets to ModelScope without saving large file contents to disk. Supports file-level recovery, automatic destination repository creation, and incremental updates. Requires Python 3.9+ and provides a pure Python wheel for Linux, macOS, and Windows.

## Installation and quick start

```bash
uv sync
uv run hf2ms org/source user/destination --ms-token "$MODELSCOPE_API_TOKEN"
```

`--ms-token` **must be passed explicitly** and must have permission to write to or create the destination repository. Even when `MODELSCOPE_API_TOKEN` is set, pass it to the option as shown above. `--hf-token` is optional and defaults to `HF_TOKEN`; public sources usually need no token, while private or gated sources require an authorized token.

```bash
# Private or gated HF source
uv run hf2ms org/source user/destination \
  --ms-token "$MODELSCOPE_API_TOKEN" --hf-token "$HF_TOKEN"

# Read-only preview
uv run hf2ms org/source user/destination \
  --ms-token "$MODELSCOPE_API_TOKEN" --dry-run

# Select revisions and filter paths
uv run hf2ms org/source user/destination \
  --ms-token "$MODELSCOPE_API_TOKEN" \
  --hf-revision v1.2 --ms-revision release \
  --include '*.json' '*.safetensors' --exclude 'original/*'
```

Windows PowerShell:

```powershell
$env:MODELSCOPE_API_TOKEN = 'your MS token'
hf2ms org/source user/destination --ms-token $env:MODELSCOPE_API_TOKEN
```

Install the wheel to use the `hf2ms` console script directly:

```bash
uv tool install ./dist/hf2ms-0.2.2-py3-none-any.whl
hf2ms --help
# Or install into an existing Python environment:
pip install ./dist/hf2ms-0.2.2-py3-none-any.whl
```

HTTPS requires a Python build using OpenSSL 1.1.1+. Older macOS/Xcode Python builds may use LibreSSL; a uv-managed Python is recommended, for example `uv sync --python 3.11`.

## Options

| Option | Default / behavior |
|---|---|
| `SOURCE DESTINATION` | Required; both use `owner/repository` |
| `--ms-token TOKEN` | **Required**; does not implicitly read an MS token environment variable |
| `--hf-token TOKEN` | Optional; defaults to `HF_TOKEN`, with the explicit option taking precedence |
| `--repo-type auto\|model\|dataset` | `auto`: query HF to identify the source as a model or dataset; select the unique match, or require an explicit type if both exist |
| `--hf-revision REVISION` | `main`; source branch, tag, or commit |
| `--ms-revision BRANCH` | `master`; writable destination branch, independent of the HF revision |
| `--ms-visibility auto\|private\|public` | `auto`; applies only when creating a destination repository |
| `--on-conflict overwrite\|error\|skip` | `overwrite`; handles different or unverified content at an existing path |
| `--include PATTERN [PATTERN ...]` | Include globs; repeatable |
| `--exclude PATTERN [PATTERN ...]` | Exclude globs; repeatable and takes precedence over include |
| `--cache-dir PATH` | `~/.cache/hf2ms/`; supports `~` expansion |
| `--cache-max-size SIZE` | `256MiB`; minimum `64KiB` |
| `--restart` | Ignore local progress and resolve the source again; identical remote content can still be reused |
| `--dry-run` | Read-only preflight; outputs a per-file plan and a JSON summary |
| `--chunk-size SIZE` | `1MiB`; streaming read buffer size |
| `--attempts N` | `3`; maximum attempts per operation |
| `--timeout SECONDS` | `120`; socket read timeout |
| `--hf-endpoint URL` | `HF_ENDPOINT` or `https://huggingface.co` |
| `--ms-endpoint URL` | `MODELSCOPE_ENDPOINT` or `https://modelscope.cn` |
| `--version` | Print the package version |

Sizes accept integer bytes or KiB/MiB/GiB and KB/MB/GB suffixes. Patterns use case-sensitive `fnmatchcase` matching against repository-relative paths, not regular expressions. Quote patterns to prevent shell expansion. Put the source and destination positional arguments before multi-value filter options.

### What `--repo-type auto` means

`auto` detects the repository type by querying HF, rather than guessing from file extensions. It checks both the model and dataset namespaces for the supplied repository ID:

- Exactly one accessible type: use it automatically, and use the same type for the destination.
- Both a model and a dataset exist under the same `owner/repository`: report ambiguity and require `--repo-type model` or `--repo-type dataset`.
- Neither can be found: report that the repository is missing or inaccessible with the current token.
- HF may return 401/403 for a missing or hidden namespace. Auto checks both namespaces and selects the unique successful match. If neither succeeds, authentication/permission errors are reported. Network and server errors still abort detection.

Private repositories may hide their existence with 401/403/404 responses, so detection is limited to what the current HF token can see. An explicit repository type queries only that namespace.

## Repositories, revisions, and updates

- Missing destination repositories are created automatically. With `--ms-visibility auto`, a public source creates a public destination; a private/gated source or unknown source visibility creates a private destination. Existing repository visibility is unchanged.
- Creation uses HF `cardData.license` when a single license string is available; otherwise it submits ModelScope's `Other` value rather than inventing Apache/MIT licensing. License files are transferred normally. A license value rejected by the server produces an error.
- New repositories start on `master`. To target another branch, the repository and branch must already exist. This version does not create branches or tags automatically.
- Destination file size and SHA-256 are compared with the source. Identical content is skipped; different content is updated by default through a new commit. Unverified equality follows the conflict policy. An existing repository or different cross-platform commit IDs are not errors by themselves.
- `--on-conflict error` checks selected paths before uploading and rejects any conflict, including unverified equality. A real run may stream the source to compute a missing SHA-256; dry-run never downloads file contents.
- `--on-conflict skip` preserves different content at existing paths while uploading missing files.
- Destination-only files are retained. File/directory conflicts never implicitly delete directories.
- Each file is committed separately; there is no repository-wide atomic transaction. Preflight cannot lock the remote branch, so avoid concurrent writers to the same destination branch.

## Recovery and cache

Run **the same command** after an interruption to resume; no `--resume` flag is needed. SQLite checkpoints are named by a hash of the task identity and contain metadata only, never tokens, signed URLs, or file contents. Repositories, revisions, filters, and conflict policies have separate state. Concurrent processes cannot run the same task.

- Unfinished tasks keep their pinned HF commit even if the source branch advances.
- Running a completed task again resolves the source revision anew to pick up updates.
- Current remote files are checked on each run. External edits or deletions are handled according to the conflict policy rather than trusting local completion records alone.
- If a blob was uploaded but its commit failed, renegotiation reuses the complete blob when the server still retains it.
- An interrupted file is transferred again from the beginning. **Byte-offset recovery within a file is not supported.**
- `--restart` clears the task's local progress and resolves the source again; it does not force the server to accept identical blob bytes again.

When the cache exceeds its budget, the least recently used **completed, unlocked task databases** are removed first. Running and unfinished tasks are protected. If insufficient space can be reclaimed, checkpoints are preserved and the command fails; increase the budget or manually remove unwanted task state. The budget is checked after checkpoint operations, so the current SQLite transaction and journal can temporarily exceed it; this is not a hard filesystem quota. Cache cleanup never deletes remote content.

`--dry-run` does not create cache directories, prune cache, modify checkpoints, create repositories, or call upload negotiation/PUT/commit. With an unfinished checkpoint it previews the pinned source version; `--restart --dry-run` previews the current source version. Files whose equality cannot be established from metadata are marked `unknown`. The JSON `bytes` field is the total selected file size, not guaranteed network traffic. `would_commit`, `would_skip`, and `would_error` count planned file outcomes under the current policy. A successful preview does not prove upload permission.

## Memory and protocol limitations

Large files flow from an HF HTTP response into a ModelScope PUT through a bounded read buffer, 1 MiB by default. This buffer is not a server-side multipart chunk. HF LFS SHA-256 metadata avoids a preliminary hashing pass; large files without a hash require a streaming hash pass followed by a streaming upload pass, both without writing contents to disk.

Non-LFS small files (≤1 MiB) are encoded and committed in memory. README.md, .gitattributes, and configuration.json remain regular files and fail if they exceed the inline limit. Memory also includes HTTP/TLS buffers, directory pages, and small-file Base64/JSON, so the read buffer size is not a process RSS limit. Destination metadata caches at most one directory with 3000 entries. ModelScope model directories that cannot be listed completely fail explicitly instead of treating truncated results as missing files.

Network failures, HTTP 429, and selected 5xx responses receive bounded retries. Large-file length and SHA-256 are checked during streaming. Serial, per-file commits can encounter rate limits for repositories containing many small files. HF HTTP resolve may restrict very large files; this version does not reconstruct Xet files or fall back to downloading whole files to disk.

## Python API

```python
import os
from hf2ms import Transfer, TransferOptions

result = Transfer(
    TransferOptions(
        source="org/model",
        destination="user/model",
        repo_type="auto",
        hf_revision="main",
        ms_revision="master",
        on_conflict="overwrite",
    ),
    ms_token=os.environ["MODELSCOPE_API_TOKEN"],
    hf_token=os.getenv("HF_TOKEN"),
).run()
print(result)
```

Version 0.2.2 handles metadata API redirects within the same origin, between explicit official domain aliases, and from `hf-mirror.com` to official Hugging Face (one-way). Redirected POST requests preserve their method and body only for 307/308; consumed upload streams are never automatically replayed. HTTP errors include the method and hostname without exposing signed query parameters. Both HF namespaces are checked even when one returns 401/403.

Version 0.2.1 renames the CLI option `--visibility` to `--ms-visibility`. The Python API's `visibility` field and existing cache identity remain unchanged.

Version 0.2.0 replaced `--revision`, `--destination-revision`, `--state`, and `--recheck` with `--hf-revision`, `--ms-revision`, `--cache-dir`, and `--restart`, respectively, with corresponding Python option changes. Legacy `.hf2ms/state.sqlite3` files are not migrated automatically; complete remote blobs can still be reused.

## Development and validation

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv build
```

Tests use local HTTP servers to exercise actual streaming PUTs, interrupted transfers, repository creation, conflict policies, previews, cache protection, and token arguments. CI is configured for Linux/macOS/Windows and Python 3.9–3.14. Live authenticated HF → ModelScope end-to-end validation is still pending; no real ModelScope writes have been performed. The HF mirror may redirect metadata requests to the official HF host, which must be reachable from your network.

Protocol references: [ModelScope REST/LFS](https://github.com/modelscope/modelscope_hub/blob/main/src/modelscope_hub/_legacy_api.py), [HF Hub API](https://huggingface.co/docs/hub/api).
