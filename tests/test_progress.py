import io

import pytest
from tqdm import tqdm

from hf2ms import Transfer
from hf2ms.cli import build_parser
from hf2ms.http import HTTPError
from hf2ms.progress import Progress
from test_transfer import BLOB, options, server


@pytest.fixture
def bars(monkeypatch):
    records = []

    class Recorded(tqdm):
        def __init__(self, **kwargs):
            self.phases = []
            self.before_reset = []
            self.requested_leave = kwargs["leave"]
            self.output = io.StringIO()
            kwargs.update(file=self.output, mininterval=0, dynamic_ncols=False, ncols=100)
            super().__init__(**kwargs)
            records.append(self)

        def reset(self, *args, **kwargs):
            self.before_reset.append(self.n)
            return super().reset(*args, **kwargs)

        def set_description_str(self, desc, refresh=True):
            self.phases.append(desc)
            return super().set_description_str(desc, refresh=refresh)

    monkeypatch.setattr("hf2ms.progress.tqdm", Recorded)
    return records


def test_two_rows_count_skips_and_clear_file_rows(tmp_path, bars):
    with server() as (base, data):
        opt = options(tmp_path, base, progress="on")
        Transfer(opt, "ms-secret").run()
        assert [bar.total for bar in bars] == [2, len(BLOB), 5]
        assert [bar.n for bar in bars] == [2, len(BLOB), 5]
        assert [bar.requested_leave for bar in bars] == [True, False, False]
        assert all(bar.disable for bar in bars)  # All closed.
        assert any("Committing" in p for p in bars[1].phases)
        assert "100%" in bars[0].output.getvalue()
        bars.clear()
        Transfer(opt, "ms-secret").run()
        assert bars[0].n == 2
        assert all(bar.n == 0 for bar in bars[1:])
        assert all(any("Skipped" in p for p in bar.phases) for bar in bars[1:])


def test_commit_failure_does_not_advance_total(tmp_path, bars):
    with server() as (base, data):
        data["fail_commit"] = True
        with pytest.raises(HTTPError):
            Transfer(options(tmp_path, base, progress="on"), "ms-secret").run()
        assert bars[0].n == 0
        assert bars[1].n == len(BLOB)
        assert bars[1].disable and bars[0].disable


def test_retry_resets_bytes_without_double_counting(tmp_path, bars, monkeypatch):
    with server() as (base, data):
        data["fail_commit"] = True
        monkeypatch.setattr("hf2ms.transfer.time.sleep", lambda _: data.update(fail_commit=False))
        opt = options(tmp_path, base, progress="on")
        opt.attempts = 2
        Transfer(opt, "ms-secret").run()
        assert bars[0].n == 2
        assert len(BLOB) in bars[1].before_reset
        assert bars[1].n == 0  # Retry reused the blob, no invented transferred bytes.
        assert any("Reused" in p for p in bars[1].phases)
        assert data["puts"] == 1


def test_hash_pass_is_separate_from_upload(tmp_path, bars):
    with server() as (base, data):
        data["omit_hash"] = True
        Transfer(options(tmp_path, base, progress="on"), "ms-secret").run()
        assert bars[1].n == len(BLOB)
        assert len(BLOB) in bars[1].before_reset
        assert any("Hashing" in p for p in bars[1].phases)
        assert any("HF -> MS" in p for p in bars[1].phases)


def test_filtered_total_and_dry_run(tmp_path, bars):
    with server() as (base, data):
        opt = options(tmp_path, base, progress="on", include=("README.md",))
        Transfer(opt, "ms-secret").run()
        assert bars[0].total == bars[0].n == 1
        bars.clear()
        opt.dry_run = True
        Transfer(opt, "ms-secret").run()
        assert bars == []


def test_auto_off_for_redirected_stderr(monkeypatch, capsys):
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    progress = Progress()
    assert not progress.enabled
    progress.start(1)
    with progress.file("file", 3):
        progress.update(3)
    progress.advance()
    progress.close()
    assert capsys.readouterr().err == ""
    assert build_parser().parse_args(["a/b", "c/d", "--ms-token", "x"]).progress == "auto"


def test_progress_choice_does_not_change_checkpoint_identity(tmp_path):
    opt = options(tmp_path, "http://localhost", progress="off")
    first = Transfer(opt, "secret")
    other = Transfer(options(tmp_path, "http://localhost", progress="on"), "secret")
    try:
        assert first._identity() == other._identity()
    finally:
        first.source.close()
        first.target.close()
        other.source.close()
        other.target.close()
