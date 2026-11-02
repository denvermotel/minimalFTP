"""Sync: plan, execution, upload_dir, download_dir."""
import os
import threading
import time

import pytest

from minimalftp import (Action, FtpConfig, FtpTransferError, Rules, SyncResult,
                        connect)


@pytest.fixture
def client(ftp_server):
  cfg = FtpConfig(host=ftp_server.host, port=ftp_server.port,
                  user=ftp_server.user, password=ftp_server.password, timeout=10)
  c = connect(cfg)
  try:
    yield c
  finally:
    c.close()


@pytest.fixture
def local(tmp_path):
  d = tmp_path / "local"
  d.mkdir()
  return d


def write(path, data=b"x", mtime=None):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_bytes(data)
  if mtime is not None:
    os.utime(path, (mtime, mtime))
  return path


def kinds(actions):
  return {a.rel_path: a.kind for a in actions}


def test_plan_does_not_transfer(client, ftp_server, local):
  write(local / "a.txt", b"aaa")
  write(local / "sub" / "b.txt", b"bbb")
  plan = client.plan_sync("up", str(local), "/dest")
  assert kinds(plan) == {".": "mkdir", "a.txt": "upload", "sub": "mkdir",
                         "sub/b.txt": "upload"}
  assert not (ftp_server.root / "dest").exists()


def test_dry_run_returns_plan_only(client, ftp_server, local):
  write(local / "a.txt", b"aaa")
  res = client.sync("up", str(local), "/dest", dry_run=True)
  assert isinstance(res, SyncResult)
  assert res.transferred == 0 and not res.cancelled and res.errors == []
  assert kinds(res.actions)["a.txt"] == "upload"
  assert not (ftp_server.root / "dest").exists()


def test_second_sync_transfers_nothing(client, ftp_server, local, tmp_path):
  write(local / "a.txt", b"aaa")
  write(local / "sub" / "b.txt", b"bbbb")
  first = client.upload_dir(str(local), "/dest")
  assert first.transferred == 2 and first.errors == []
  assert (ftp_server.root / "dest" / "sub" / "b.txt").read_bytes() == b"bbbb"
  second = client.upload_dir(str(local), "/dest")
  assert second.transferred == 0
  assert not [a for a in second.actions if a.kind in ("upload", "download")]
  # idempotence also after a download into a new directory
  out = tmp_path / "out"
  d1 = client.download_dir("/dest", str(out))
  assert d1.transferred == 2
  assert (out / "sub" / "b.txt").read_bytes() == b"bbbb"
  d2 = client.download_dir("/dest", str(out))
  assert d2.transferred == 0
  assert not [a for a in d2.actions if a.kind in ("upload", "download")]


def test_second_sync_without_mfmt_has_no_upload(client, local):
  client.features.discard("MFMT")
  write(local / "a.txt", b"aaa")
  assert client.upload_dir(str(local), "/dest").transferred == 1
  plan = client.plan_sync("up", str(local), "/dest", Rules())
  assert not [a for a in plan if a.kind in ("upload", "download")]


def test_only_recent_files_uploaded(client, ftp_server, local):
  now = time.time()
  write(local / "new.txt", b"n", now)
  write(local / "old.txt", b"o", now - 40 * 86400)
  res = client.sync("up", str(local), "/dest", rules=Rules(max_age_days=7))
  assert kinds(res.actions)["old.txt"] == "skip"
  assert res.transferred == 1
  assert (ftp_server.root / "dest" / "new.txt").exists()
  assert not (ftp_server.root / "dest" / "old.txt").exists()


def test_size_difference_triggers_upload(client, ftp_server, local):
  now = time.time()
  write(local / "a.txt", b"short", now)
  client.upload_dir(str(local), "/dest")
  write(local / "a.txt", b"much longer content", now)
  plan = client.plan_sync("up", str(local), "/dest", Rules())
  assert [(a.kind, a.reason) for a in plan if a.rel_path == "a.txt"] == [
    ("upload", "size-differs")]
  client.upload_dir(str(local), "/dest")
  assert (ftp_server.root / "dest" / "a.txt").read_bytes() == b"much longer content"


def test_dest_newer_is_never_overwritten(client, ftp_server, local):
  now = time.time()
  write(local / "a.txt", b"local-old", now - 10 * 86400)
  write(ftp_server.root / "dest" / "a.txt", b"remote-newer-version", now)
  res = client.upload_dir(str(local), "/dest")
  assert res.transferred == 0
  assert [a.reason for a in res.actions if a.rel_path == "a.txt"] == [
    "conflict-dest-newer"]
  assert (ftp_server.root / "dest" / "a.txt").read_bytes() == b"remote-newer-version"


def test_never_deletes_extra_files(client, ftp_server, local, tmp_path):
  write(local / "a.txt", b"aaa")
  write(local / "local-only.txt", b"keep")
  write(ftp_server.root / "dest" / "remote-only.txt", b"keep too")
  client.upload_dir(str(local), "/dest")
  assert (ftp_server.root / "dest" / "remote-only.txt").read_bytes() == b"keep too"
  out = tmp_path / "out"
  write(out / "extra-local.txt", b"keep3")
  client.download_dir("/dest", str(out))
  assert (out / "extra-local.txt").read_bytes() == b"keep3"
  assert (out / "remote-only.txt").exists()
  assert (local / "local-only.txt").exists()


def test_missing_remote_dir_is_created(client, ftp_server, local):
  write(local / "sub" / "a.txt", b"aaa")
  res = client.upload_dir(str(local), "/deep/er/dest")
  assert res.errors == []
  assert (ftp_server.root / "deep" / "er" / "dest" / "sub" / "a.txt").exists()


def test_empty_local_dir_created_remote(client, ftp_server, local):
  (local / "empty").mkdir()
  res = client.upload_dir(str(local), "/dest")
  assert res.transferred == 0
  assert (ftp_server.root / "dest" / "empty").is_dir()


def test_download_creates_local_dirs_incl_empty(client, ftp_server, tmp_path):
  write(ftp_server.root / "r" / "sub" / "a.txt", b"aaa")
  (ftp_server.root / "r" / "vuota").mkdir()
  out = tmp_path / "new" / "out"
  res = client.download_dir("/r", str(out))
  assert res.transferred == 1
  assert (out / "sub" / "a.txt").read_bytes() == b"aaa"
  assert (out / "vuota").is_dir()


def test_download_missing_remote_root_propagates(client, tmp_path):
  with pytest.raises(FtpTransferError):
    client.download_dir("/non-esiste", str(tmp_path / "out"))


def test_symlink_loop_terminates(client, ftp_server, local):
  write(local / "a.txt", b"aaa")
  try:
    os.symlink(str(local), str(local / "loop"))
    os.symlink(str(local / "a.txt"), str(local / "link.txt"))
  except (OSError, NotImplementedError):
    pytest.skip("link simbolici non supportati")
  res = client.upload_dir(str(local), "/dest")
  k = kinds(res.actions)
  assert k["loop"] == "skip" and k["link.txt"] == "skip"
  assert [a.reason for a in res.actions if a.rel_path == "loop"] == ["symlink"]
  assert res.transferred == 1
  assert not (ftp_server.root / "dest" / "loop").exists()


def test_error_on_one_file_collected_and_sync_continues(client, ftp_server, local,
                                                        monkeypatch):
  write(local / "a.txt", b"aaa")
  write(local / "b.txt", b"bbb")
  write(local / "c.txt", b"ccc")
  real = client.upload

  def flaky(src, dst, on_progress=None):
    if dst.endswith("b.txt"):
      raise FtpTransferError("boom", 550)
    return real(src, dst, on_progress)

  monkeypatch.setattr(client, "upload", flaky)
  res = client.upload_dir(str(local), "/dest")
  assert res.transferred == 2
  assert [e[0] for e in res.errors] == ["b.txt"] and "boom" in res.errors[0][1]
  assert (ftp_server.root / "dest" / "c.txt").exists()
  assert not res.cancelled


def test_stops_when_connection_unusable_after_error(client, local, monkeypatch):
  write(local / "a.txt", b"aaa")
  write(local / "b.txt", b"bbb")

  def dying(src, dst, on_progress=None):
    client.close()
    raise FtpTransferError("caduta", None)

  monkeypatch.setattr(client, "upload", dying)
  # remote mkdir happens first: the directory already exists
  client.mkdir("/dest", parents=True)
  res = client.sync("up", str(local), "/dest", rules=Rules())
  assert len(res.errors) == 1 and res.transferred == 0


def test_cancel_event_stops_and_marks_cancelled(client, ftp_server, local):
  for name in ("a.txt", "b.txt", "c.txt"):
    write(local / name, name.encode() * 10)
  cancel = threading.Event()

  def progress(action, done, total):
    cancel.set()

  res = client.sync("up", str(local), "/dest", on_progress=progress, cancel=cancel)
  assert res.cancelled
  assert res.transferred == 1
  present = [p.name for p in (ftp_server.root / "dest").iterdir()]
  assert present == ["a.txt"]


def test_progress_receives_action(client, local):
  write(local / "a.txt", b"aaa")
  seen = []
  client.upload_dir(str(local), "/dest",
                    on_progress=lambda a, d, t: seen.append((a, d, t)))
  assert isinstance(seen[-1][0], Action) and seen[-1][0].rel_path == "a.txt"
  assert seen[-1][1:] == (3, 3)


def test_include_exclude_patterns(client, ftp_server, local):
  write(local / "a.txt", b"a")
  write(local / "b.log", b"b")
  write(local / "cache" / "c.txt", b"c")
  write(local / "keep" / "d.txt", b"d")
  rules = Rules(include=["*.txt"], exclude=["cache", "cache/*"])
  res = client.sync("up", str(local), "/dest", rules=rules)
  k = kinds(res.actions)
  assert k["b.log"] == "skip"
  assert "cache" not in k and "cache/c.txt" not in k
  root = ftp_server.root / "dest"
  assert (root / "a.txt").exists() and (root / "keep" / "d.txt").exists()
  assert not (root / "b.log").exists() and not (root / "cache").exists()


def test_type_conflict_skip(client, ftp_server, local):
  write(local / "thing" / "x.txt", b"x")  # local: directory
  write(local / "other", b"file")  # local: file
  write(ftp_server.root / "dest" / "thing", b"remote file")  # remote: file
  (ftp_server.root / "dest" / "other").mkdir()  # remote: directory
  res = client.upload_dir(str(local), "/dest")
  conflicts = {a.rel_path for a in res.actions if a.reason == "type-conflict"}
  assert conflicts == {"thing", "other"}
  assert res.transferred == 0
  assert (ftp_server.root / "dest" / "thing").read_bytes() == b"remote file"


def test_hostile_remote_names_are_unsafe_and_never_written(client, tmp_path,
                                                           monkeypatch):
  from minimalftp import RemoteEntry
  parent = tmp_path / "parent"
  parent.mkdir()
  dest = parent / "dest"
  bad = ["../../evil", "a/../../x", "x\\y", ".."]
  entries = [RemoteEntry(n, False, 1, None) for n in bad]
  entries.append(RemoteEntry("ok.txt", False, 1, None))
  monkeypatch.setattr(client, "list", lambda path: list(entries))
  plan = client.plan_sync("down", str(dest), "/r")
  unsafe = {a.rel_path for a in plan if a.reason == "unsafe-name"}
  assert unsafe == set(bad)
  assert not [a for a in plan if a.kind == "download" and a.rel_path in bad]

  def fake_download(remote, local, on_progress=None):
    with open(local, "wb") as fh:
      fh.write(b"x")

  monkeypatch.setattr(client, "download", fake_download)
  client.sync("down", str(dest), "/r")
  assert [p.name for p in parent.iterdir()] == ["dest"]
  assert [p.name for p in dest.iterdir()] == ["ok.txt"]


def test_sync_refuses_action_outside_local_dir(client, tmp_path, monkeypatch):
  parent = tmp_path / "parent"
  dest = parent / "dest"
  dest.mkdir(parents=True)
  forced = [Action("mkdir", "../escape", "missing", 0),
            Action("download", "../../evil", "missing", 1)]
  monkeypatch.setattr(client, "plan_sync", lambda *a, **k: forced)
  called = []
  monkeypatch.setattr(client, "download", lambda *a, **k: called.append(a))
  res = client.sync("down", str(dest), "/r")
  assert len(res.errors) == 2 and res.transferred == 0 and not called
  assert [p.name for p in parent.iterdir()] == ["dest"]


def test_max_depth_terminates(client, monkeypatch):
  from minimalftp import MAX_SYNC_DEPTH, RemoteEntry
  monkeypatch.setattr(client, "list",
                      lambda path: [RemoteEntry("d", True, 0, None)])
  plan = client.plan_sync("down", "/nonexistent-minimalftp-dir", "/r")
  depth = [a for a in plan if a.reason == "max-depth"]
  assert len(depth) == 1
  assert depth[0].rel_path.count("/") == MAX_SYNC_DEPTH


# --- names that differ only by case or Unicode normalization ------------------------


def reasons(actions):
  return {a.rel_path: (a.kind, a.reason) for a in actions}


def test_down_case_only_name_difference_never_overwrites(client, ftp_server, local):
  now = time.time()
  write(ftp_server.root / "r" / "README.TXT", b"vecchio dal server", now - 30 * 86400)
  write(local / "readme.txt", b"nuovo locale", now)
  res = client.sync("down", str(local), "/r")
  assert reasons(res.actions)["README.TXT"] == ("skip", "name-case-conflict")
  assert res.transferred == 0 and res.errors == []
  assert (local / "readme.txt").read_bytes() == b"nuovo locale"


def test_up_case_only_name_difference_never_overwrites(client, ftp_server, local):
  now = time.time()
  write(local / "readme.txt", b"vecchio locale", now - 30 * 86400)
  write(ftp_server.root / "r" / "README.TXT", b"nuovo sul server", now)
  res = client.sync("up", str(local), "/r")
  assert reasons(res.actions)["readme.txt"] == ("skip", "name-case-conflict")
  assert res.transferred == 0
  assert [p.name for p in (ftp_server.root / "r").iterdir()] == ["README.TXT"]
  assert (ftp_server.root / "r" / "README.TXT").read_bytes() == b"nuovo sul server"


def test_case_only_directory_difference_is_skipped(client, ftp_server, local):
  write(ftp_server.root / "r" / "Docs" / "a.txt", b"a")
  write(local / "docs" / "b.txt", b"b")
  plan = client.plan_sync("down", str(local), "/r")
  assert reasons(plan) == {"Docs": ("skip", "name-case-conflict")}
  plan_up = client.plan_sync("up", str(local), "/r")
  assert reasons(plan_up) == {"docs": ("skip", "name-case-conflict")}


def test_nfc_nfd_names_match_as_conflict(client, local, monkeypatch):
  from minimalftp import RemoteEntry
  nfc, nfd = "café.txt", "café.txt"
  write(local / nfc, b"locale")
  monkeypatch.setattr(client, "list",
                      lambda path: [RemoteEntry(nfd, False, 99, None)])
  names = {p.name for p in local.iterdir()}
  if nfd in names:
    pytest.skip("il filesystem ha riscritto il nome in NFD")
  plan = client.plan_sync("down", str(local), "/r")
  assert reasons(plan) == {nfd: ("skip", "name-case-conflict")}
  plan_up = client.plan_sync("up", str(local), "/r")
  assert reasons(plan_up) == {nfc: ("skip", "name-case-conflict")}


def test_colliding_source_names_first_wins(client, tmp_path, monkeypatch):
  from minimalftp import RemoteEntry
  monkeypatch.setattr(client, "list", lambda path: [
    RemoteEntry("a.txt", False, 1, None), RemoteEntry("A.txt", False, 2, None)])
  plan = client.plan_sync("down", str(tmp_path / "nuova"), "/r")
  assert reasons(plan) == {".": ("mkdir", "missing"),
                           "A.txt": ("download", "missing"),
                           "a.txt": ("skip", "name-case-conflict")}


# --- unreadable directories ---------------------------------------------------------


def test_unreadable_local_subdir_is_skipped(client, ftp_server, local):
  write(local / "a.txt", b"a")
  secret = local / "segreta"
  write(secret / "s.txt", b"s")
  os.chmod(secret, 0)
  try:
    if os.access(secret, os.R_OK):
      pytest.skip("permessi non applicabili (utente privilegiato?)")
    res = client.sync("up", str(local), "/dest")
  finally:
    os.chmod(secret, 0o755)
  assert reasons(res.actions)["segreta"] == ("skip", "unreadable")
  assert "segreta/s.txt" not in reasons(res.actions)
  assert res.transferred == 1 and res.errors == []
  assert not (ftp_server.root / "dest" / "segreta").exists()


def test_unreadable_remote_subdir_is_skipped(client, ftp_server, local, monkeypatch):
  write(ftp_server.root / "r" / "a.txt", b"a")
  write(ftp_server.root / "r" / "chiusa" / "c.txt", b"c")
  real = client.list

  def deny(path):
    if path.endswith("/chiusa"):
      raise FtpTransferError("listing failed: 550 Permission denied", 550)
    return real(path)

  monkeypatch.setattr(client, "list", deny)
  res = client.sync("down", str(local), "/r")
  assert reasons(res.actions)["chiusa"] == ("skip", "unreadable")
  assert res.transferred == 1 and res.errors == []
  # no local directory created for the unreadable part
  assert not (local / "chiusa").exists()
  # in "up" the unreadable remote destination is not compared blindly
  write(local / "chiusa" / "c.txt", b"locale")
  plan = client.plan_sync("up", str(local), "/r")
  assert reasons(plan)["chiusa"] == ("skip", "unreadable")
  assert "chiusa/c.txt" not in reasons(plan)


def test_remote_network_error_during_scan_still_raises(client, local, monkeypatch):
  real = client.list

  def broken(path):
    if path.endswith("/sub"):
      raise FtpTransferError("listing failed: ConnectionResetError", None)
    return real(path)

  write(local / "sub" / "x.txt", b"x")
  client.upload_dir(str(local), "/dest")
  monkeypatch.setattr(client, "list", broken)
  with pytest.raises(FtpTransferError):
    client.plan_sync("up", str(local), "/dest")


# --- connection lost halfway through a sync -----------------------------------------


def _kill_control_before(client, monkeypatch, method, nth):
  """Closes the control channel before call number `nth` to `method`."""
  import socket
  real = getattr(client, method)
  calls = []

  def wrapper(*args, **kwargs):
    calls.append(args[0])
    if len(calls) == nth:
      client._ftp.sock.shutdown(socket.SHUT_RDWR)
    return real(*args, **kwargs)

  monkeypatch.setattr(client, method, wrapper)
  return calls


def test_connection_lost_stops_upload_sync(client, ftp_server, local, monkeypatch):
  for name in ("a.txt", "b.txt", "c.txt"):
    write(local / name, name.encode())
  client.mkdir("/dest")
  calls = _kill_control_before(client, monkeypatch, "upload", 2)
  res = client.sync("up", str(local), "/dest")
  assert res.transferred == 1
  assert [e[0] for e in res.errors] == ["b.txt"]
  assert client._closed
  assert len(calls) == 2  # c.txt was not attempted
  assert (ftp_server.root / "dest" / "a.txt").exists()


def test_connection_lost_stops_download_sync(client, ftp_server, local, monkeypatch):
  for name in ("a.txt", "b.txt", "c.txt"):
    write(ftp_server.root / "r" / name, name.encode())
  calls = _kill_control_before(client, monkeypatch, "download", 2)
  res = client.sync("down", str(local), "/r")
  assert res.transferred == 1
  assert [e[0] for e in res.errors] == ["b.txt"]
  assert client._closed
  assert len(calls) == 2
  assert sorted(p.name for p in local.iterdir()) == ["a.txt"]


def test_connection_lost_after_upload_stops_before_next(client, ftp_server, local,
                                                         monkeypatch):
  if not client.supports("MFMT"):
    pytest.skip("il server di test non annuncia MFMT")
  write(local / "a.txt", b"a")
  write(local / "b.txt", b"b")
  client.mkdir("/dest")
  real = client._ftp.sendcmd

  def eof_on_mfmt(cmd):
    if cmd.startswith("MFMT"):
      raise EOFError()
    return real(cmd)

  monkeypatch.setattr(client._ftp, "sendcmd", eof_on_mfmt)
  res = client.sync("up", str(local), "/dest")
  assert res.transferred == 1
  assert len(res.errors) == 1 and res.errors[0][0] == "b.txt"
  assert client._closed
  assert not (ftp_server.root / "dest" / "b.txt").exists()
