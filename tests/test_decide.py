from datetime import datetime, timedelta, timezone

import pytest

from minimalftp import Action, RemoteEntry, Rules, decide

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def entry(size=100, mtime=NOW, approximate=False):
  return RemoteEntry(name="f.txt", is_dir=False, size=size, mtime=mtime,
                     approximate=approximate)


def ago(**kwargs):
  return NOW - timedelta(**kwargs)


def run(src, dest, rules=None, rel="a/f.txt", direction="up"):
  return decide(rel, src, dest, rules or Rules(), NOW, direction)


def test_missing_dest_uploads():
  act = run(entry(size=42), None)
  assert act == Action("upload", "a/f.txt", "missing", 42)


def test_old_missing_file_skipped_by_age_filter():
  act = run(entry(mtime=ago(days=10)), None, Rules(max_age_days=5))
  assert (act.kind, act.reason) == ("skip", "too-old")


def test_dest_newer_and_size_differs_is_conflict_skip():
  act = run(entry(size=1, mtime=ago(hours=2)), entry(size=2, mtime=ago(hours=1)))
  assert (act.kind, act.reason) == ("skip", "conflict-dest-newer")


def test_overwrite_newer_true_transfers():
  act = run(entry(size=1, mtime=ago(hours=2)), entry(size=2, mtime=ago(hours=1)),
            Rules(overwrite_newer=True))
  assert (act.kind, act.reason) == ("upload", "size-differs")


def test_overwrite_newer_same_size_unchanged():
  act = run(entry(size=5, mtime=ago(hours=2)), entry(size=5, mtime=ago(hours=1)),
            Rules(overwrite_newer=True))
  assert (act.kind, act.reason) == ("skip", "unchanged")


def test_src_newer_beyond_tolerance_transfers():
  act = run(entry(mtime=ago(hours=1)), entry(mtime=ago(hours=2)))
  assert (act.kind, act.reason) == ("upload", "newer")


def test_within_tolerance_same_size_unchanged():
  act = run(entry(mtime=ago(seconds=1)), entry(mtime=ago(seconds=2)))
  assert (act.kind, act.reason) == ("skip", "unchanged")


def test_size_differs_only_transfers():
  act = run(entry(size=1), entry(size=2))
  assert (act.kind, act.reason) == ("upload", "size-differs")


def test_flags_off_means_unchanged():
  rules = Rules(newer_than_dest=False, size_differs=False)
  act = run(entry(size=1, mtime=ago(hours=1)), entry(size=2, mtime=ago(hours=2)), rules)
  assert (act.kind, act.reason) == ("skip", "unchanged")


def test_exclude_beats_include():
  rules = Rules(include=("*.txt",), exclude=("a/*",))
  act = run(entry(), None, rules)
  assert (act.kind, act.reason) == ("skip", "excluded")


def test_not_included():
  act = run(entry(), None, Rules(include=("*.csv",)))
  assert (act.kind, act.reason) == ("skip", "not-included")


def test_pattern_is_case_sensitive_and_star_crosses_slash():
  assert run(entry(), None, Rules(exclude=("*.TXT",))).kind == "upload"
  assert run(entry(), None, Rules(exclude=("a/*.txt",)), rel="a/b/c.txt").reason == "excluded"


def test_approximate_widens_tolerance():
  src = entry(mtime=ago(seconds=30))
  dest = entry(mtime=ago(minutes=5))
  assert run(src, dest).reason == "newer"
  dest_approx = entry(mtime=ago(minutes=5), approximate=True)
  src_close = entry(mtime=ago(minutes=5) + timedelta(seconds=40))
  assert run(src_close, dest_approx).reason == "unchanged"
  assert run(src_close, entry(mtime=ago(minutes=5))).reason == "newer"


def test_age_unknown_when_mtime_missing():
  act = run(entry(mtime=None), None, Rules(max_age_days=5))
  assert (act.kind, act.reason) == ("skip", "age-unknown")


def test_modified_after_cutoff_uses_later_value():
  rules = Rules(max_age_days=30, modified_after=ago(days=2))
  act = run(entry(mtime=ago(days=5)), None, rules)
  assert (act.kind, act.reason) == ("skip", "too-old")


def test_down_direction_kind():
  assert run(entry(), None, direction="down").kind == "download"


def test_invalid_direction_raises():
  with pytest.raises(ValueError):
    run(entry(), None, direction="sideways")


def test_unknown_mtime_same_size_unchanged():
  assert run(entry(size=5), entry(size=5, mtime=None)).reason == "unchanged"
  assert run(entry(size=5, mtime=None), entry(size=5)).reason == "unchanged"


def test_unknown_mtime_size_differs_is_conflict():
  for src, dest in ((entry(size=1), entry(size=2, mtime=None)),
                    (entry(size=1, mtime=None), entry(size=2)),
                    (entry(size=1, mtime=None), entry(size=2, mtime=None))):
    act = run(src, dest)
    assert (act.kind, act.reason) == ("skip", "conflict-dest-newer")


def test_unknown_mtime_size_differs_with_overwrite_newer_transfers():
  act = run(entry(size=1), entry(size=2, mtime=None), Rules(overwrite_newer=True))
  assert (act.kind, act.reason) == ("upload", "size-differs")
  act = run(entry(size=1), entry(size=2, mtime=None),
            Rules(overwrite_newer=True, size_differs=False))
  assert (act.kind, act.reason) == ("skip", "unchanged")
