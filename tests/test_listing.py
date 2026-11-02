from datetime import datetime, timezone

from minimalftp import RemoteEntry, parse_list_line, parse_mlsd_line

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def utc(*args, **kwargs):
  return datetime(*args, tzinfo=timezone.utc, **kwargs)


def test_mlsd_file_and_dir():
  e = parse_mlsd_line("type=file;size=12;modify=20260301120000; a b.txt")
  assert e == RemoteEntry("a b.txt", False, 12, utc(2026, 3, 1, 12, 0, 0), False)
  assert e.mtime.tzinfo is timezone.utc
  d = parse_mlsd_line("Type=DIR;Modify=20260301120000; sub;dir")
  assert d.is_dir is True and d.name == "sub;dir" and d.size == 0


def test_mlsd_fractional_seconds_and_other_types():
  e = parse_mlsd_line("type=file;size=1;modify=20260301120000.123; f")
  assert e.mtime == utc(2026, 3, 1, 12, 0, 0, microsecond=123000)
  link = parse_mlsd_line("type=OS.unix=slink:/x;size=1; l")
  assert link.is_dir is False and link.name == "l"
  nofacts = parse_mlsd_line("type=file; nodate")
  assert nofacts.mtime is None and nofacts.size == 0


def test_mlsd_skips_cdir_pdir():
  assert parse_mlsd_line("type=cdir;modify=20260301120000; .") is None
  assert parse_mlsd_line("type=pdir;modify=20260301120000; ..") is None
  assert parse_mlsd_line("type=dir; .") is None
  assert parse_mlsd_line("type=dir; ..") is None
  assert parse_mlsd_line("garbage") is None
  assert parse_mlsd_line("") is None


def test_list_unix_with_time_infers_year():
  e = parse_list_line("-rw-r--r--   1 user group     1234 Mar  5 10:30 file.txt", NOW)
  assert e == RemoteEntry("file.txt", False, 1234, utc(2026, 3, 5, 10, 30), True)
  d = parse_list_line("drwxr-xr-x   2 user group     4096 Oct 31 23:00 docs", NOW)
  assert d.is_dir is True and d.mtime == utc(2026, 10, 31, 23, 0)


def test_list_unix_future_month_uses_previous_year():
  e = parse_list_line("-rw-r--r-- 1 u g 10 Dec 25 10:30 old.txt", NOW)
  assert e.mtime == utc(2025, 12, 25, 10, 30)
  # within one day in the future the current year is kept (timezone offsets)
  ok = parse_list_line("-rw-r--r-- 1 u g 10 Nov  2 10:00 soon.txt", NOW)
  assert ok.mtime == utc(2026, 11, 2, 10, 0)


def test_list_unix_leap_day_without_year():
  now = datetime(2027, 3, 10, tzinfo=timezone.utc)
  # Feb 29 2027 does not exist: the last previous leap year is used
  e = parse_list_line("-rw-r--r-- 1 u g 10 Feb 29 10:30 leap.txt", now)
  assert e.mtime == utc(2024, 2, 29, 10, 30)
  now2 = datetime(2028, 3, 10, tzinfo=timezone.utc)
  e2 = parse_list_line("-rw-r--r-- 1 u g 10 Feb 29 10:30 leap.txt", now2)
  assert e2.mtime == utc(2028, 2, 29, 10, 30)


def test_list_unix_with_year():
  e = parse_list_line("-rw-r--r--   1 user group      99 Jan  1  2020 vecchio.txt", NOW)
  assert e == RemoteEntry("vecchio.txt", False, 99, utc(2020, 1, 1, 0, 0), True)


def test_list_unix_symlink_strips_target():
  e = parse_list_line("lrwxrwxrwx 1 u g 5 Mar  5 10:30 a -> b", NOW)
  assert e.name == "a" and e.is_dir is False


def test_list_windows_dir_and_file():
  d = parse_list_line("11-05-26  09:15AM       <DIR>          Cartella", NOW)
  assert d == RemoteEntry("Cartella", True, 0, utc(2026, 11, 5, 9, 15), True)
  f = parse_list_line("03-01-99  12:05PM              1048576 big.bin", NOW)
  assert f == RemoteEntry("big.bin", False, 1048576, utc(1999, 3, 1, 12, 5), True)
  mid = parse_list_line("03-01-70  12:00AM 5 x.txt", NOW)
  assert mid.mtime == utc(1970, 3, 1, 0, 0)
  assert parse_list_line("03-01-69  01:00PM 5 x.txt", NOW).mtime == utc(2069, 3, 1, 13, 0)


def test_list_name_with_spaces_and_unicode():
  e = parse_list_line("-rw-r--r-- 1 user group 7 Mar  5 10:30 relazione finale è.txt", NOW)
  assert e.name == "relazione finale è.txt"
  w = parse_list_line("03-01-26  12:05PM <DIR> Mia cartella ñ", NOW)
  assert w.name == "Mia cartella ñ" and w.is_dir is True


def test_list_garbage_returns_none():
  assert parse_list_line("", NOW) is None
  assert parse_list_line("total 24", NOW) is None
  assert parse_list_line("hello world this is not a listing", NOW) is None
  assert parse_list_line("-rw-r--r-- 1 u g 10 Zzz 25 10:30 x", NOW) is None


def test_list_unix_without_group_column():
  e = parse_list_line("-rw-r--r-- 1 ftp 1234 Nov  1 11:00 name.txt", NOW)
  assert e == RemoteEntry("name.txt", False, 1234, utc(2026, 11, 1, 11, 0), True)
  d = parse_list_line("drwxr-xr-x 2 1000 4096 Jan 10  2025 cartella", NOW)
  assert d.is_dir is True and d.size == 4096 and d.name == "cartella"
  # with a numeric group the size stays correct
  g = parse_list_line("-rw-r--r-- 1 user 1000 77 Mar  5 10:30 f.txt", NOW)
  assert g.size == 77 and g.name == "f.txt"
