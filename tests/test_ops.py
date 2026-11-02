"""Single-file operations: list, upload, download, mkdir, remove, rename, exists."""
import ftplib
import os
from datetime import datetime, timezone

import pytest

from minimalftp import FtpConfig, FtpTransferError, connect


@pytest.fixture
def client(ftp_server):
  cfg = FtpConfig(host=ftp_server.host, port=ftp_server.port,
                  user=ftp_server.user, password=ftp_server.password, timeout=10)
  c = connect(cfg)
  try:
    yield c
  finally:
    c.close()


def make_local(tmp_path, name, data):
  p = tmp_path / name
  p.write_bytes(data)
  return p


def test_upload_download_roundtrip_bytes_equal(client, ftp_server, tmp_path):
  data = os.urandom(300_000)
  src = make_local(tmp_path, "a.bin", data)
  client.upload(str(src), "/a.bin")
  assert (ftp_server.root / "a.bin").read_bytes() == data
  assert not (ftp_server.root / "a.bin.minimalftp-part").exists()
  dst = tmp_path / "out.bin"
  client.download("/a.bin", str(dst))
  assert dst.read_bytes() == data
  assert not (tmp_path / "out.bin.minimalftp-part").exists()


def test_zero_byte_file(client, ftp_server, tmp_path):
  src = make_local(tmp_path, "z.txt", b"")
  seen = []
  client.upload(str(src), "/z.txt", on_progress=lambda d, t: seen.append((d, t)))
  assert (ftp_server.root / "z.txt").read_bytes() == b""
  assert seen[-1] == (0, 0)
  dst = tmp_path / "z-out.txt"
  client.download("/z.txt", str(dst))
  assert dst.exists() and dst.read_bytes() == b""


@pytest.mark.parametrize("name", ["con spazi.txt", "perché è città.txt", "-dash.txt"])
def test_filename_with_spaces_unicode_and_leading_dash(client, ftp_server, tmp_path, name):
  src = make_local(tmp_path, "src.bin", b"contenuto")
  client.upload(str(src), "/" + name)
  assert (ftp_server.root / name).read_bytes() == b"contenuto"
  dst = tmp_path / "dl.bin"
  client.download("/" + name, str(dst))
  assert dst.read_bytes() == b"contenuto"
  assert client.exists("/" + name)


def test_progress_called_with_total(client, tmp_path):
  data = os.urandom(200_000)
  src = make_local(tmp_path, "p.bin", data)
  up, down = [], []
  client.upload(str(src), "/p.bin", on_progress=lambda d, t: up.append((d, t)))
  assert up and up[-1] == (len(data), len(data))
  assert all(t == len(data) for _, t in up)
  assert [d for d, _ in up] == sorted(d for d, _ in up)
  client.download("/p.bin", str(tmp_path / "p2.bin"),
                  on_progress=lambda d, t: down.append((d, t)))
  assert down and down[-1] == (len(data), len(data))


class Boom(Exception):
  pass


def test_interrupted_upload_leaves_no_final_file(client, ftp_server, tmp_path):
  src = make_local(tmp_path, "big.bin", os.urandom(4 * 1024 * 1024))
  calls = []

  def cb(done, total):
    calls.append(done)
    raise Boom()

  with pytest.raises(Boom):
    client.upload(str(src), "/big.bin", on_progress=cb)
  assert len(calls) == 1
  assert not (ftp_server.root / "big.bin").exists()
  assert not (ftp_server.root / "big.bin.minimalftp-part").exists()
  # the session remains usable after draining the pending reply
  small = make_local(tmp_path, "s.txt", b"ok")
  client.upload(str(small), "/s.txt")
  assert (ftp_server.root / "s.txt").read_bytes() == b"ok"


def test_interrupted_download_removes_partial(client, ftp_server, tmp_path):
  (ftp_server.root / "big.bin").write_bytes(os.urandom(4 * 1024 * 1024))
  dst = tmp_path / "big-out.bin"

  def cb(done, total):
    raise Boom()

  with pytest.raises(Boom):
    client.download("/big.bin", str(dst), on_progress=cb)
  assert not dst.exists()
  assert not (tmp_path / "big-out.bin.minimalftp-part").exists()


def test_download_size_mismatch_fails_and_keeps_session(client, ftp_server, tmp_path):
  (ftp_server.root / "cut.bin").write_bytes(b"x" * 1000)
  dst = tmp_path / "cut-out.bin"
  client._size = lambda remote: 5000  # the server announces more than it sends
  with pytest.raises(FtpTransferError, match="received 1000 of 5000"):
    client.download("/cut.bin", str(dst))
  assert not dst.exists()
  assert not (tmp_path / "cut-out.bin.minimalftp-part").exists()
  del client._size
  assert client.exists("/cut.bin")


def test_upload_overwrites_existing_file(client, ftp_server, tmp_path):
  (ftp_server.root / "o.txt").write_bytes(b"vecchio")
  src = make_local(tmp_path, "o.txt", b"nuovo")
  client.upload(str(src), "/o.txt")
  assert (ftp_server.root / "o.txt").read_bytes() == b"nuovo"
  assert not (ftp_server.root / "o.txt.minimalftp-part").exists()


def test_upload_sets_remote_mtime_when_mfmt(client, ftp_server, tmp_path):
  src = make_local(tmp_path, "m.txt", b"x")
  os.utime(src, (1_700_000_000, 1_700_000_000))
  client.upload(str(src), "/m.txt")
  if client.supports("MFMT"):
    assert abs((ftp_server.root / "m.txt").stat().st_mtime - 1_700_000_000) <= 2


def test_download_sets_local_mtime_to_remote(client, ftp_server, tmp_path):
  remote = ftp_server.root / "t.txt"
  remote.write_bytes(b"dati")
  os.utime(remote, (1_600_000_000, 1_600_000_000))
  dst = tmp_path / "t.txt"
  client.download("/t.txt", str(dst))
  assert abs(dst.stat().st_mtime - 1_600_000_000) <= 2


def test_download_missing_raises_and_client_usable(client, tmp_path):
  dst = tmp_path / "nope.txt"
  with pytest.raises(FtpTransferError) as ei:
    client.download("/nope.txt", str(dst))
  assert ei.value.code == 550
  assert not dst.exists()
  assert not (tmp_path / "nope.txt.minimalftp-part").exists()
  assert client.exists("/") is True


def test_mkdir_parents(client, ftp_server):
  client.mkdir("/a/b/c", parents=True)
  assert (ftp_server.root / "a" / "b" / "c").is_dir()
  # already existing components tolerated
  client.mkdir("/a/b/c/d", parents=True)
  client.mkdir("/a/b/c/d", parents=True)
  assert (ftp_server.root / "a" / "b" / "c" / "d").is_dir()
  with pytest.raises(FtpTransferError):
    client.mkdir("/x/y")


def test_mkdir_parents_fails_when_component_is_file(client, ftp_server):
  (ftp_server.root / "f").write_bytes(b"1")
  with pytest.raises(FtpTransferError):
    client.mkdir("/f/sub", parents=True)


def test_remove_and_rename(client, ftp_server):
  (ftp_server.root / "r.txt").write_bytes(b"1")
  client.rename("/r.txt", "/r2.txt")
  assert not (ftp_server.root / "r.txt").exists()
  assert (ftp_server.root / "r2.txt").exists()
  client.remove("/r2.txt")
  assert not (ftp_server.root / "r2.txt").exists()
  with pytest.raises(FtpTransferError):
    client.remove("/r2.txt")


def test_exists_true_false(client, ftp_server):
  (ftp_server.root / "f.txt").write_bytes(b"1")
  (ftp_server.root / "d").mkdir()
  pwd_before = client._ftp.pwd()
  assert client.exists("/f.txt") is True
  assert client.exists("/d") is True
  assert client.exists("/nope") is False
  assert client.exists("/d/nope") is False
  assert client._ftp.pwd() == pwd_before


def test_list_returns_utc_mtimes_and_dirs(client, ftp_server):
  f = ftp_server.root / "file.txt"
  f.write_bytes(b"12345")
  os.utime(f, (1_600_000_000, 1_600_000_000))
  (ftp_server.root / "sub").mkdir()
  entries = {e.name: e for e in client.list("/")}
  assert set(entries) == {"file.txt", "sub"}
  assert entries["sub"].is_dir is True
  e = entries["file.txt"]
  assert e.is_dir is False and e.size == 5
  assert e.mtime.tzinfo is not None
  assert e.mtime == datetime.fromtimestamp(1_600_000_000, timezone.utc)


def test_list_uses_mlsd_when_server_announces_mlst(client, ftp_server, monkeypatch):
  # RFC 3659: FEAT announces only MLST, which implies MLSD (pyftpdlib does this)
  assert "MLST" in client.features and "MLSD" not in client.features
  f = ftp_server.root / "m.txt"
  f.write_bytes(b"1234")
  os.utime(f, (1_600_000_123, 1_600_000_123))
  (ftp_server.root / "sub").mkdir()
  sent = []
  real = client._ftp.retrlines

  def spy(cmd, callback=None):
    sent.append(cmd.split()[0])
    return real(cmd, callback)

  monkeypatch.setattr(client._ftp, "retrlines", spy)
  monkeypatch.setattr(client, "_mdtm", lambda remote: pytest.fail("MDTM non serve"))
  entries = {e.name: e for e in client.list("/")}
  assert sent == ["MLSD"]
  assert entries["sub"].is_dir is True
  e = entries["m.txt"]
  assert e.size == 4 and e.approximate is False
  assert e.mtime == datetime.fromtimestamp(1_600_000_123, timezone.utc)


def test_list_via_list_fallback(client, ftp_server, monkeypatch):
  f = ftp_server.root / "old.txt"
  f.write_bytes(b"123")
  os.utime(f, (1_600_000_000, 1_600_000_000))
  (ftp_server.root / "sub").mkdir()
  client.features = client.features - {"MLST", "MLSD"}
  sent = []
  real = client._ftp.retrlines

  def spy(cmd, callback=None):
    sent.append(cmd.split()[0])
    return real(cmd, callback)

  monkeypatch.setattr(client._ftp, "retrlines", spy)
  entries = {e.name: e for e in client.list("/")}
  assert sent == ["LIST"]
  assert entries["sub"].is_dir is True
  e = entries["old.txt"]
  assert e.size == 3
  # MDTM replaces the approximate LIST date
  assert e.approximate is False
  assert e.mtime == datetime.fromtimestamp(1_600_000_000, timezone.utc)
  # without MDTM the date stays that of LIST, approximate
  monkeypatch.setattr(client, "_mdtm", lambda remote: None)
  e2 = {x.name: x for x in client.list("/")}["old.txt"]
  assert e2.approximate is True and e2.mtime.tzinfo is not None


def _fake_list_lines(client, monkeypatch, lines):
  client.features = client.features - {"MLST", "MLSD"}

  def fake(cmd, callback=None):
    for line in lines:
      callback(line)
    return "226 ok"

  monkeypatch.setattr(client._ftp, "retrlines", fake)


def test_list_fallback_unrecognized_line_fails(client, monkeypatch):
  _fake_list_lines(client, monkeypatch, [
    "total 8",
    "drwxr-xr-x 2 ftp ftp 4096 Nov  1 11:00 .",
    "-rw-r--r-- 1 ftp ftp 10 Nov  1 11:00 ok.txt",
    "riga misteriosa che nessuno sa leggere",
  ])
  with pytest.raises(FtpTransferError) as ei:
    client.list("/")
  assert "unrecognized" in str(ei.value)
  assert not client._closed


def test_list_fallback_line_without_group_is_listed(client, monkeypatch):
  _fake_list_lines(client, monkeypatch, [
    "total 4",
    "",
    "-rw-r--r-- 1 ftp 1234 Nov  1 11:00 name.txt",
  ])
  monkeypatch.setattr(client, "_mdtm", lambda remote: None)
  entries = client.list("/")
  assert [(e.name, e.size) for e in entries] == [("name.txt", 1234)]


def test_list_missing_dir_raises(client):
  with pytest.raises(FtpTransferError) as ei:
    client.list("/non-esiste")
  assert ei.value.code == 550


def test_backslash_path_normalized(client, ftp_server, tmp_path):
  (ftp_server.root / "dir").mkdir()
  src = make_local(tmp_path, "b.txt", b"bk")
  client.upload(str(src), "\\dir\\b.txt")
  assert (ftp_server.root / "dir" / "b.txt").read_bytes() == b"bk"
  assert [e.name for e in client.list("\\dir")] == ["b.txt"]
  assert client.exists("\\dir\\b.txt")
  client.rename("\\dir\\b.txt", "\\dir\\c.txt")
  assert (ftp_server.root / "dir" / "c.txt").exists()
  client.remove("\\dir\\c.txt")
  assert not (ftp_server.root / "dir" / "c.txt").exists()


def _refuse_part_to_final(client, times):
  """Makes the first `times` renames `*.minimalftp-part` -> final file fail."""
  real = client._ftp.rename
  count = {"n": 0}

  def fake(src, dst):
    if src.endswith(".minimalftp-part"):
      count["n"] += 1
      if count["n"] <= times:
        raise ftplib.error_perm("550 exists")
    return real(src, dst)

  client._ftp.rename = fake


def test_upload_rename_fallback_replaces_existing(client, ftp_server, tmp_path):
  (ftp_server.root / "e.txt").write_bytes(b"vecchio")
  src = make_local(tmp_path, "e.txt", b"nuovo")
  _refuse_part_to_final(client, 1)
  client.upload(str(src), "/e.txt")
  assert (ftp_server.root / "e.txt").read_bytes() == b"nuovo"
  assert not (ftp_server.root / "e.txt.minimalftp-old").exists()
  assert not (ftp_server.root / "e.txt.minimalftp-part").exists()


def test_upload_rename_fallback_failure_keeps_old_and_part(client, ftp_server, tmp_path):
  (ftp_server.root / "e.txt").write_bytes(b"vecchio")
  src = make_local(tmp_path, "e.txt", b"nuovo")
  _refuse_part_to_final(client, 2)
  with pytest.raises(FtpTransferError) as ei:
    client.upload(str(src), "/e.txt")
  assert "minimalftp-part" in str(ei.value)
  assert (ftp_server.root / "e.txt").read_bytes() == b"vecchio"
  assert (ftp_server.root / "e.txt.minimalftp-part").read_bytes() == b"nuovo"
  assert not (ftp_server.root / "e.txt.minimalftp-old").exists()
  assert client.exists("/e.txt") is True


def test_upload_onto_directory_refused_and_untouched(client, ftp_server, tmp_path):
  (ftp_server.root / "d.txt").mkdir()
  (ftp_server.root / "d.txt" / "dentro").write_bytes(b"k")
  src = make_local(tmp_path, "d.txt", b"nuovo")
  with pytest.raises(FtpTransferError) as ei:
    client.upload(str(src), "/d.txt")
  assert ei.value.code == 550 and "directory" in str(ei.value)
  assert (ftp_server.root / "d.txt" / "dentro").read_bytes() == b"k"
  assert sorted(p.name for p in ftp_server.root.iterdir()) == ["d.txt"]


def test_upload_refuses_to_overwrite_existing_backup(client, ftp_server, tmp_path):
  (ftp_server.root / "e.txt").write_bytes(b"vecchio")
  (ftp_server.root / "e.txt.minimalftp-old").write_bytes(b"backup prezioso")
  src = make_local(tmp_path, "e.txt", b"nuovo")
  _refuse_part_to_final(client, 1)
  with pytest.raises(FtpTransferError) as ei:
    client.upload(str(src), "/e.txt")
  assert "e.txt.minimalftp-old" in str(ei.value)
  assert (ftp_server.root / "e.txt").read_bytes() == b"vecchio"
  assert (ftp_server.root / "e.txt.minimalftp-old").read_bytes() == b"backup prezioso"
  assert (ftp_server.root / "e.txt.minimalftp-part").read_bytes() == b"nuovo"


def test_mfmt_connection_error_does_not_fail_upload(client, ftp_server, tmp_path,
                                                    monkeypatch):
  src = make_local(tmp_path, "m.txt", b"dati")
  real = client._ftp.sendcmd

  def eof_on_mfmt(cmd):
    if cmd.startswith("MFMT"):
      raise EOFError()
    return real(cmd)

  monkeypatch.setattr(client._ftp, "sendcmd", eof_on_mfmt)
  client.upload(str(src), "/m.txt")
  assert (ftp_server.root / "m.txt").read_bytes() == b"dati"
  # the session is no longer reliable: closed, so a sync stops
  assert client._closed


def test_upload_control_channel_lost_closes_session(client, tmp_path):
  import socket
  src = make_local(tmp_path, "x.txt", b"x")
  client._ftp.sock.shutdown(socket.SHUT_RDWR)
  with pytest.raises(FtpTransferError):
    client.upload(str(src), "/x.txt")
  assert client._closed


def test_local_file_error_keeps_session_open(client, tmp_path):
  with pytest.raises(FtpTransferError):
    client.upload(str(tmp_path / "manca.txt"), "/manca.txt")
  assert not client._closed
  with pytest.raises(FtpTransferError):
    client.download("/manca-remoto.txt", str(tmp_path / "out.txt"))
  assert not client._closed
