"""Headless tests for the pane logic of the GUI (no Tk window)."""
import os
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minimalftp_gui as gui  # noqa: E402
from minimalftp import RemoteEntry  # noqa: E402

T0 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def lang_restore():
  """Restores the global translator language after the test."""
  before = gui.translator.lang
  yield gui.translator
  gui.translator.set_language(before)


def _file(name, size=10, mtime=T0):
  return RemoteEntry(name, False, size, mtime)


def _dir(name):
  return RemoteEntry(name, True, 0, T0)


def test_list_local_sorts_dirs_first_and_utc_mtime(tmp_path):
  (tmp_path / "b.txt").write_text("hello")
  (tmp_path / "A.txt").write_text("x")
  (tmp_path / "zdir").mkdir()
  (tmp_path / "Adir").mkdir()
  entries = gui.list_local(str(tmp_path))
  assert [e.name for e in entries] == ["Adir", "zdir", "A.txt", "b.txt"]
  assert [e.is_dir for e in entries] == [True, True, False, False]
  by_name = {e.name: e for e in entries}
  assert by_name["b.txt"].size == 5
  assert by_name["b.txt"].mtime.utcoffset() == timedelta(0)
  assert abs(by_name["b.txt"].mtime.timestamp() - os.stat(tmp_path / "b.txt").st_mtime) < 1


def test_list_local_broken_symlink_listed_with_unknown_mtime(tmp_path):
  link = tmp_path / "broken"
  try:
    os.symlink(str(tmp_path / "missing"), str(link))
  except (OSError, NotImplementedError):
    pytest.skip("symlinks not available")
  entries = gui.list_local(str(tmp_path))
  assert len(entries) == 1
  assert entries[0].name == "broken"
  assert entries[0].size == 0 and entries[0].mtime is None and not entries[0].is_dir


@pytest.mark.skipif(sys.platform.startswith("win") or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="permissions not enforced")
def test_list_local_unreadable_raises_oserror(tmp_path):
  locked = tmp_path / "locked"
  locked.mkdir()
  locked.chmod(0)
  try:
    with pytest.raises(OSError):
      gui.list_local(str(locked))
  finally:
    locked.chmod(0o700)


def test_sort_entries_dirs_first_casefold():
  entries = [_file("b"), _dir("Z"), _file("A"), _dir("a")]
  assert [e.name for e in gui.sort_entries(entries)] == ["a", "Z", "A", "b"]


def test_join_and_parent_remote():
  assert gui.join_remote("/", "a") == "/a"
  assert gui.join_remote("/a", "b c") == "/a/b c"
  assert gui.join_remote("/a/b/", "è.txt") == "/a/b/è.txt"
  assert gui.join_remote("a\\b", "c") == "a/b/c"
  assert gui.parent_remote("/") == "/"
  assert gui.parent_remote("/a") == "/"
  assert gui.parent_remote("/a/b/") == "/a"
  assert gui.parent_remote("a\\b") == "a"
  assert gui.parent_remote("/a b/è") == "/a b"


def test_parent_local(tmp_path):
  child = tmp_path / "sub"
  assert gui.parent_local(str(child)) == str(tmp_path)
  root = os.path.abspath(os.sep)
  assert gui.parent_local(root) == root


def test_describe_when_none_and_value(lang_restore):
  lang_restore.set_language("en")
  assert gui.describe_when(None) == "unknown date"
  lang_restore.set_language("it")
  assert gui.describe_when(None) == "data ignota"
  expected = T0.astimezone().strftime("%Y-%m-%d %H:%M")
  assert gui.describe_when(T0) == expected


def test_build_conflict_newer_markers():
  later = T0 + timedelta(hours=1)
  assert gui.build_conflict("f", _file("f", mtime=later), _file("f")).newer == "local"
  assert gui.build_conflict("f", _file("f"), _file("f", mtime=later)).newer == "remote"
  near = T0 + timedelta(seconds=2)
  assert gui.build_conflict("f", _file("f", mtime=near), _file("f")).newer == "same"
  assert gui.build_conflict("f", _file("f", mtime=None), _file("f")).newer == "unknown"
  assert gui.build_conflict("f", _file("f"), _file("f", mtime=None)).newer == "unknown"
  assert gui.build_conflict("f", _file("f", 5), _file("f", 5)).same_size is True
  assert gui.build_conflict("f", _file("f", 5), _file("f", 6)).same_size is False


def test_conflict_texts_contains_sizes_and_dates(lang_restore):
  row = gui.build_conflict("f", _file("f", 84211, T0 + timedelta(hours=1)), _file("f", 2048, T0))
  lang_restore.set_language("en")
  texts = gui.conflict_texts(row)
  assert set(texts) == {"local_size", "remote_size", "local_when", "remote_when", "verdict"}
  assert "84,211 bytes" in texts["local_size"]
  assert "82.2 KB" in texts["local_size"]
  assert "2,048 bytes" in texts["remote_size"]
  assert texts["local_when"] == gui.describe_when(row.local.mtime)
  assert texts["remote_when"] == gui.describe_when(row.remote.mtime)
  assert "local" in texts["verdict"].lower()
  lang_restore.set_language("it")
  texts_it = gui.conflict_texts(row)
  assert "84 211 byte" in texts_it["local_size"]
  assert texts_it["verdict"] != texts["verdict"]


def test_conflict_texts_verdict_variants(lang_restore):
  lang_restore.set_language("en")
  same = gui.conflict_texts(gui.build_conflict("f", _file("f", 5), _file("f", 5)))["verdict"]
  unknown = gui.conflict_texts(gui.build_conflict("f", _file("f", 5, None), _file("f", 5)))["verdict"]
  remote = gui.conflict_texts(gui.build_conflict(
    "f", _file("f", 5), _file("f", 9, T0 + timedelta(days=1))))["verdict"]
  assert len({same, unknown, remote}) == 3
  assert "remote" in remote.lower()


def test_find_conflicts_exact_and_case_nfc():
  nfc = unicodedata.normalize("NFC", "caffè.txt")
  nfd = unicodedata.normalize("NFD", "caffè.txt")
  selected = [_file("same.txt"), _file("README.TXT"), _file(nfc), _file("new.txt")]
  dest = [_file("same.txt"), _file("readme.txt"), _file(nfd), _file("other.txt")]
  rows = gui.find_conflicts(selected, dest, "local")
  assert [r.name for r in rows] == ["same.txt", "README.TXT", nfc]
  assert rows[1].local.name == "README.TXT" and rows[1].remote.name == "readme.txt"
  rows_remote = gui.find_conflicts(selected, dest, "remote")
  assert rows_remote[1].remote.name == "README.TXT" and rows_remote[1].local.name == "readme.txt"


def test_find_conflicts_ignores_dotdot_and_dirs():
  selected = [_file(".."), _dir("sub"), _file("a")]
  dest = [_dir(".."), _dir("sub"), _file("b")]
  assert gui.find_conflicts(selected, dest, "local") == []


def test_find_conflicts_type_mismatch_raises(lang_restore):
  lang_restore.set_language("en")
  with pytest.raises(ValueError) as info:
    gui.find_conflicts([_file("x")], [_dir("x")], "local")
  assert "x" in str(info.value)
  with pytest.raises(ValueError):
    gui.find_conflicts([_dir("x")], [_file("x")], "remote")


def _rows(*names):
  return [gui.build_conflict(n, _file(n), _file(n)) for n in names]


def test_resolver_apply_to_all_asks_once():
  calls = []

  def ask(row, remaining):
    calls.append((row.name, remaining))
    return "overwrite", True

  overwrite, skip, cancelled = gui.ConflictResolver(ask).resolve(_rows("a", "b", "c"))
  assert overwrite == {"a", "b", "c"} and skip == set() and cancelled is False
  assert calls == [("a", 3)]


def test_resolver_cancel_stops():
  answers = iter([("overwrite", False), ("cancel", False)])
  calls = []

  def ask(row, remaining):
    calls.append(row.name)
    return next(answers)

  overwrite, skip, cancelled = gui.ConflictResolver(ask).resolve(_rows("a", "b", "c"))
  assert cancelled is True
  assert calls == ["a", "b"]


def test_resolver_default_skip_semantics():
  overwrite, skip, cancelled = gui.ConflictResolver(
    lambda row, remaining: ("skip", False)).resolve(_rows("a", "b"))
  assert overwrite == set() and skip == {"a", "b"} and cancelled is False
