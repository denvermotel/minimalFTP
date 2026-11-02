"""Headless tests for ConnectionWorker (no Tk, real pyftpdlib server)."""
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minimalftp_gui as gui  # noqa: E402
from minimalftp import FtpConfig  # noqa: E402


class Harness:
  """A worker plus a thread-safe list of the emitted messages."""

  def __init__(self):
    self.messages = []
    self._lock = threading.Lock()
    self.worker = gui.ConnectionWorker(self.emit)
    self.worker.start()

  def emit(self, msg):
    with self._lock:
      self.messages.append(msg)

  def snapshot(self):
    with self._lock:
      return list(self.messages)

  def of(self, kind):
    return [m for m in self.snapshot() if m[0] == kind]

  def wait_idle(self, count=1, timeout=10.0):
    """Waits until `count` ("busy", False) messages have been emitted in total."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      if len([m for m in self.snapshot() if m == ("busy", False)]) >= count:
        return
      time.sleep(0.01)
    raise AssertionError("worker did not become idle: %r" % (self.snapshot(),))

  def run(self, cmd, **kw):
    """Submits a command and waits for it to finish; returns the messages it produced."""
    before = len(self.snapshot())
    done_before = len([m for m in self.snapshot() if m == ("busy", False)])
    self.worker.submit(cmd, **kw)
    self.wait_idle(done_before + 1)
    return self.snapshot()[before:]


@pytest.fixture
def harness():
  h = Harness()
  try:
    yield h
  finally:
    h.worker.stop()
    assert not h.worker._thread.is_alive()


def _cfg(server, **kw):
  base = dict(host=server.host, port=server.port, user=server.user,
              password=server.password, timeout=5)
  base.update(kw)
  return FtpConfig(**base)


@pytest.fixture
def connected(harness, ftp_server):
  harness.run("connect", cfg=_cfg(ftp_server))
  assert harness.worker.connected
  return harness


def _set_mtime(path, epoch):
  os.utime(str(path), (epoch, epoch))


def test_connect_and_list_remote(harness, ftp_server):
  (ftp_server.root / "a.txt").write_text("hello")
  (ftp_server.root / "sub").mkdir()
  msgs = harness.run("connect", cfg=_cfg(ftp_server))
  assert ("connected",) in msgs
  assert harness.worker.connected
  assert not harness.worker.busy
  msgs = harness.run("list_remote", path="/")
  listing = [m for m in msgs if m[0] == "remote_listing"]
  assert len(listing) == 1
  _, path, entries = listing[0]
  assert path == "/"
  assert {e.name: e.is_dir for e in entries} == {"a.txt": False, "sub": True}
  # the busy flag brackets every command
  assert msgs[0] == ("busy", True) and msgs[-1] == ("busy", False)


def test_transfer_upload_single_file_roundtrip(connected, ftp_server, tmp_path):
  name = "caffe' e perche' è 日本.txt"
  src = tmp_path / name
  payload = ("contenuto è 日本\n" * 5000).encode("utf-8")
  src.write_bytes(payload)
  msgs = connected.run("transfer", direction="up",
                       items=[(str(src), "/" + name, False)],
                       overwrite=set(), overwrite_dirs=False)
  done = [m for m in msgs if m[0] == "transfer_done"]
  assert len(done) == 1
  summary = done[0][1]
  assert summary["transferred"] == 1 and summary["skipped"] == 0
  assert not summary["errors"] and not summary["cancelled"]
  assert (ftp_server.root / name).read_bytes() == payload
  # a final progress message for the file is always emitted
  progress = [m for m in msgs if m[0] == "progress"]
  assert progress and progress[-1][1] == name
  assert progress[-1][2] == progress[-1][3] == len(payload)
  # and back down to a different local name
  dest = tmp_path / "back" / name
  dest.parent.mkdir()
  msgs = connected.run("transfer", direction="down",
                       items=[(str(dest), "/" + name, False)],
                       overwrite=set(), overwrite_dirs=False)
  assert [m for m in msgs if m[0] == "transfer_done"][0][1]["transferred"] == 1
  assert dest.read_bytes() == payload


def test_overwrite_only_when_listed(connected, ftp_server, tmp_path):
  (ftp_server.root / "keep.txt").write_text("remote-old")
  (ftp_server.root / "swap.txt").write_text("remote-old")
  keep = tmp_path / "keep.txt"
  swap = tmp_path / "swap.txt"
  keep.write_text("local-new")
  swap.write_text("local-new")
  items = [(str(keep), "/keep.txt", False), (str(swap), "/swap.txt", False)]
  msgs = connected.run("transfer", direction="up", items=items,
                       overwrite={"swap.txt"}, overwrite_dirs=False)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["transferred"] == 1 and summary["skipped"] == 1
  assert (ftp_server.root / "keep.txt").read_text() == "remote-old"
  assert (ftp_server.root / "swap.txt").read_text() == "local-new"


def test_download_overwrites_listed_file(connected, ftp_server, tmp_path):
  (ftp_server.root / "f.txt").write_text("remote-content")
  (ftp_server.root / "g.txt").write_text("remote-content")
  (tmp_path / "f.txt").write_text("local-old")
  (tmp_path / "g.txt").write_text("local-old")
  items = [(str(tmp_path / "f.txt"), "/f.txt", False),
           (str(tmp_path / "g.txt"), "/g.txt", False)]
  msgs = connected.run("transfer", direction="down", items=items,
                       overwrite={"f.txt"}, overwrite_dirs=False)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["transferred"] == 1 and summary["skipped"] == 1
  assert (tmp_path / "f.txt").read_text() == "remote-content"
  assert (tmp_path / "g.txt").read_text() == "local-old"


def test_transfer_folder_uses_default_rules_and_reports_conflicts(
    connected, ftp_server, tmp_path):
  local = tmp_path / "proj"
  local.mkdir()
  (local / "new.txt").write_text("brand new")
  (local / "clash.txt").write_text("local-version-longer")
  remote_dir = ftp_server.root / "proj"
  remote_dir.mkdir()
  (remote_dir / "clash.txt").write_text("remote")
  # the remote copy is much newer than the local one, with a different size
  _set_mtime(local / "clash.txt", time.time() - 86400)
  _set_mtime(remote_dir / "clash.txt", time.time())
  items = [(str(local), "/proj", True)]

  msgs = connected.run("plan_dirs", direction="up", items=items)
  conflicts = [m for m in msgs if m[0] == "dir_conflicts"]
  assert len(conflicts) == 1
  actions = conflicts[0][1]
  assert [a.rel_path for a in actions] == ["proj/clash.txt"]
  assert actions[0].reason == "conflict-dest-newer"

  msgs = connected.run("transfer", direction="up", items=items,
                       overwrite=set(), overwrite_dirs=False)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["transferred"] == 1 and summary["skipped"] >= 1
  assert (remote_dir / "new.txt").read_text() == "brand new"
  assert (remote_dir / "clash.txt").read_text() == "remote"

  msgs = connected.run("transfer", direction="up", items=items,
                       overwrite=set(), overwrite_dirs=True)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["transferred"] == 1
  assert (remote_dir / "clash.txt").read_text() == "local-version-longer"


def test_plan_dirs_ignores_plain_newer(connected, ftp_server, tmp_path):
  local = tmp_path / "d"
  local.mkdir()
  (local / "x.txt").write_text("local newer content")
  remote_dir = ftp_server.root / "d"
  remote_dir.mkdir()
  (remote_dir / "x.txt").write_text("old")
  _set_mtime(remote_dir / "x.txt", time.time() - 86400)
  msgs = connected.run("plan_dirs", direction="up", items=[(str(local), "/d", True)])
  # a newer source is a normal transfer, not a conflict
  assert [m for m in msgs if m[0] == "dir_conflicts"][0][1] == []


def test_mkdir_remote(connected, ftp_server):
  msgs = connected.run("mkdir_remote", path="/nuova cartella")
  assert not [m for m in msgs if m[0] == "error"]
  assert (ftp_server.root / "nuova cartella").is_dir()
  # creating it again is reported as an error, not a crash
  msgs = connected.run("mkdir_remote", path="/nuova cartella")
  assert [m for m in msgs if m[0] == "error"]
  assert connected.worker.connected


def test_command_while_busy_is_rejected(harness, ftp_server):
  gate = threading.Event()
  started = threading.Event()

  def slow_password():
    started.set()
    assert gate.wait(10)
    return ftp_server.password

  harness.worker.submit("connect", cfg=_cfg(ftp_server, password=None,
                                            password_provider=slow_password))
  assert started.wait(10)
  assert harness.worker.busy
  harness.worker.submit("list_remote", path="/")
  assert ("error", gui.tr("busy")) in harness.snapshot()
  gate.set()
  harness.wait_idle(1)
  assert harness.worker.connected
  # the rejected command was not executed
  assert not harness.of("remote_listing")
  assert not harness.worker.busy


def test_command_while_not_connected(harness):
  msgs = harness.run("list_remote", path="/")
  assert ("error", gui.tr("not_connected")) in msgs
  msgs = harness.run("bogus")
  assert [m for m in msgs if m[0] == "error"]
  assert not harness.worker.connected


def test_connection_lost_emits_disconnected(connected):
  # simulate the network dropping under the control connection
  # shutdown (not close): ftplib keeps a file object on the same socket
  connected.worker._client._ftp.sock.shutdown(socket.SHUT_RDWR)
  msgs = connected.run("list_remote", path="/")
  assert len([m for m in msgs if m[0] == "disconnected"]) == 1
  assert not connected.worker.connected
  # afterwards commands report "not connected" without further disconnects
  msgs = connected.run("list_remote", path="/")
  assert ("error", gui.tr("not_connected")) in msgs
  assert not [m for m in msgs if m[0] == "disconnected"]


def test_wrong_password_emits_error_not_disconnected_crash(harness, ftp_server):
  msgs = harness.run("connect", cfg=_cfg(ftp_server, password="wrong"))
  errors = [m for m in msgs if m[0] == "error"]
  assert len(errors) == 1 and "login" in errors[0][1]
  assert not [m for m in msgs if m[0] in ("connected", "disconnected")]
  assert not harness.worker.connected
  # the worker thread survived and still answers
  assert harness.worker._thread.is_alive()
  harness.run("connect", cfg=_cfg(ftp_server))
  assert harness.worker.connected


def test_disconnect_command(connected):
  msgs = connected.run("disconnect")
  assert [m for m in msgs if m[0] == "disconnected"]
  assert not connected.worker.connected


def test_cancel_event_stops_between_files(connected, ftp_server, tmp_path):
  items = []
  for i in range(3):
    f = tmp_path / ("f%d.bin" % i)
    f.write_bytes(os.urandom(4096))
    items.append((str(f), "/f%d.bin" % i, False))

  real_emit = connected.worker._emit_fn

  def emit_and_cancel(msg):
    real_emit(msg)
    if msg[0] == "progress":
      connected.worker.cancel_event.set()

  connected.worker._emit_fn = emit_and_cancel
  msgs = connected.run("transfer", direction="up", items=items,
                       overwrite=set(), overwrite_dirs=False)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["cancelled"] is True
  assert summary["transferred"] == 1
  assert (ftp_server.root / "f0.bin").exists()
  assert not (ftp_server.root / "f2.bin").exists()
  # the next command starts with a clean cancel flag
  connected.worker._emit_fn = real_emit
  msgs = connected.run("transfer", direction="up", items=items[1:],
                       overwrite=set(), overwrite_dirs=False)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["cancelled"] is False and summary["transferred"] == 2


def test_progress_is_throttled(connected, ftp_server, tmp_path):
  big = tmp_path / "big.bin"
  big.write_bytes(os.urandom(3 * 1024 * 1024))
  msgs = connected.run("transfer", direction="up", items=[(str(big), "/big.bin", False)],
                       overwrite=set(), overwrite_dirs=False)
  progress = [m for m in msgs if m[0] == "progress"]
  assert progress[-1][2] == progress[-1][3] == big.stat().st_size
  # 3 MiB in 8 KiB blocks would be hundreds of callbacks; the throttle keeps few
  assert 1 <= len(progress) <= 60


def test_run_sync_job(connected, ftp_server, tmp_path):
  (tmp_path / "s.txt").write_text("sync me")
  job = {"cfg": None, "direction": "up", "local": str(tmp_path), "remote": "/syncdir",
         "rules": gui.Rules()}
  msgs = connected.run("run_sync", job=job)
  done = [m for m in msgs if m[0] == "sync_done"]
  assert len(done) == 1 and done[0][1].transferred == 1
  assert (ftp_server.root / "syncdir" / "s.txt").read_text() == "sync me"


def test_stop_closes_connection_and_joins_thread(ftp_server):
  h = Harness()
  h.run("connect", cfg=_cfg(ftp_server))
  client = h.worker._client
  assert client is not None and not client._closed
  h.worker.stop()
  assert not h.worker._thread.is_alive()
  assert client._closed
  assert not h.worker.connected
  # idempotent
  h.worker.stop()


def test_stop_without_start_is_safe():
  worker = gui.ConnectionWorker(lambda msg: None)
  worker.stop()
  assert not worker.connected


def test_emit_is_never_called_under_the_lock(harness, ftp_server):
  seen = []

  def probing_emit(msg):
    # if the internal lock were held here, this acquire would fail
    got = harness.worker._lock.acquire(timeout=1)
    seen.append(got)
    if got:
      harness.worker._lock.release()
    harness.emit(msg)

  harness.worker._emit_fn = probing_emit
  harness.run("connect", cfg=_cfg(ftp_server))
  harness.worker.submit("list_remote", path="/")
  harness.worker.submit("list_remote", path="/")
  harness.wait_idle(2)
  assert seen and all(seen)


def test_plan_dirs_missing_local_folder_keeps_connection(connected, tmp_path):
  items = [(str(tmp_path / "does-not-exist"), "/x", True)]
  msgs = connected.run("plan_dirs", direction="up", items=items)
  assert [m for m in msgs if m[0] == "error"]
  assert not [m for m in msgs if m[0] == "disconnected"]
  assert connected.worker.connected
  msgs = connected.run("list_remote", path="/")
  assert [m for m in msgs if m[0] == "remote_listing"]


def test_run_sync_missing_local_folder_keeps_connection(connected, tmp_path):
  job = {"cfg": None, "direction": "up", "local": str(tmp_path / "nope"), "remote": "/r",
         "rules": gui.Rules()}
  msgs = connected.run("run_sync", job=job)
  assert [m for m in msgs if m[0] == "error"]
  assert not [m for m in msgs if m[0] == "disconnected"]
  assert connected.worker.connected
  msgs = connected.run("list_remote", path="/")
  assert [m for m in msgs if m[0] == "remote_listing"]


def test_invalid_direction_is_an_error(connected, tmp_path):
  for cmd, kw in (("transfer", {"overwrite": set(), "overwrite_dirs": False}),
                  ("plan_dirs", {})):
    msgs = connected.run(cmd, direction="sideways", items=[], **kw)
    assert [m for m in msgs if m[0] == "error"]
  assert connected.worker.connected


def test_size_differs_is_conflict_and_needs_overwrite_dirs(connected, ftp_server, tmp_path):
  local = tmp_path / "z"
  local.mkdir()
  (local / "same-date.txt").write_text("local-longer-content")
  remote_dir = ftp_server.root / "z"
  remote_dir.mkdir()
  (remote_dir / "same-date.txt").write_text("remote")
  stamp = time.time() - 3600
  _set_mtime(local / "same-date.txt", stamp)
  _set_mtime(remote_dir / "same-date.txt", stamp)
  items = [(str(local), "/z", True)]
  msgs = connected.run("plan_dirs", direction="up", items=items)
  actions = [m for m in msgs if m[0] == "dir_conflicts"][0][1]
  assert [(a.rel_path, a.reason) for a in actions] == [("z/same-date.txt", "size-differs")]

  msgs = connected.run("transfer", direction="up", items=items,
                       overwrite=set(), overwrite_dirs=False)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["transferred"] == 0 and summary["skipped"] >= 1
  assert (remote_dir / "same-date.txt").read_text() == "remote"

  msgs = connected.run("transfer", direction="up", items=items,
                       overwrite=set(), overwrite_dirs=True)
  summary = [m for m in msgs if m[0] == "transfer_done"][0][1]
  assert summary["transferred"] == 1
  assert (remote_dir / "same-date.txt").read_text() == "local-longer-content"


def test_stop_interrupts_inflight_upload_cleanly(ftp_server, tmp_path):
  big = tmp_path / "big.bin"
  big.write_bytes(os.urandom(4 * 1024 * 1024))
  baseline = threading.active_count()
  h = Harness()
  first_progress = threading.Event()

  def slow_emit(msg):
    h.emit(msg)
    if msg[0] == "progress":
      first_progress.set()
      time.sleep(0.3)

  h.worker._emit_fn = slow_emit
  h.worker.submit("connect", cfg=_cfg(ftp_server))
  h.wait_idle(1)
  h.worker.submit("transfer", direction="up", items=[(str(big), "/big.bin", False)],
                  overwrite=set(), overwrite_dirs=False)
  assert first_progress.wait(10)
  started = time.monotonic()
  h.worker.stop(timeout=5)
  assert time.monotonic() - started < 3
  assert not h.worker._thread.is_alive()
  count = len(h.snapshot())
  time.sleep(0.5)
  assert len(h.snapshot()) == count
  assert sorted(p.name for p in ftp_server.root.iterdir()) == []
  assert threading.active_count() == baseline
