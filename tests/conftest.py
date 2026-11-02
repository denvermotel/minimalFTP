"""Shared fixtures: local FTP and FTPS servers based on pyftpdlib."""
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

USER = "user"
PASSWORD = "Pw-segreta-42"


@dataclass
class ServerInfo:
  """Access data for a test server."""

  host: str
  port: int
  user: str
  password: str
  root: Path
  cert: Optional[Path] = None
  key: Optional[Path] = None


class _ServerThread(threading.Thread):
  """Runs the pyftpdlib loop in small steps, so that stopping is clean."""

  def __init__(self, server):
    super().__init__(daemon=True)
    self.server = server
    self._stop_event = threading.Event()

  def run(self):
    try:
      while not self._stop_event.is_set():
        self.server.serve_forever(timeout=0.01, blocking=False)
    finally:
      # close_all must be called from the thread that owns the ioloop
      self.server.close_all()

  def stop(self):
    self._stop_event.set()
    self.join(5)


def _start_server(handler_base, root: Path, **handler_attrs):
  from pyftpdlib.authorizers import DummyAuthorizer
  from pyftpdlib.servers import FTPServer

  authorizer = DummyAuthorizer()
  authorizer.add_user(USER, PASSWORD, str(root), perm="elradfmwMT")
  # Subclass so as not to modify the global attributes of the base class
  attrs = {"authorizer": authorizer, "banner": "minimalftp test server"}
  attrs.update(handler_attrs)
  handler = type("TestHandler", (handler_base,), attrs)
  server = FTPServer(("127.0.0.1", 0), handler)
  thread = _ServerThread(server)
  thread.start()
  host, port = server.address[:2]
  return thread, host, port


@pytest.fixture
def ftp_server(tmp_path):
  """Plain FTP server with home in a temporary directory."""
  from pyftpdlib.handlers import FTPHandler

  root = tmp_path / "ftproot"
  root.mkdir()
  thread, host, port = _start_server(FTPHandler, root)
  try:
    yield ServerInfo(host, port, USER, PASSWORD, root)
  finally:
    thread.stop()


@pytest.fixture(scope="session")
def tls_cert(tmp_path_factory):
  """Self-signed certificate for localhost/127.0.0.1; (cert, key)."""
  openssl = shutil.which("openssl") or (
    "/opt/homebrew/bin/openssl" if Path("/opt/homebrew/bin/openssl").exists() else None)
  if openssl is None:
    pytest.skip("openssl non disponibile")
  try:
    import OpenSSL  # noqa: F401  required by TLS_FTPHandler
  except ImportError:
    pytest.skip("pyOpenSSL non disponibile")
  d = tmp_path_factory.mktemp("tls")
  cert, key = d / "cert.pem", d / "key.pem"
  subprocess.run(
    [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
     "-subj", "/CN=localhost",
     "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
     "-keyout", str(key), "-out", str(cert)],
    check=True, capture_output=True,
  )
  return cert, key


@pytest.fixture
def tls_server(tmp_path, tls_cert):
  """Explicit FTPS server (AUTH TLS) with a self-signed certificate."""
  from pyftpdlib.handlers import TLS_FTPHandler

  cert, key = tls_cert
  root = tmp_path / "ftpsroot"
  root.mkdir()
  thread, host, port = _start_server(
    TLS_FTPHandler, root, certfile=str(cert), keyfile=str(key),
    tls_control_required=False, tls_data_required=False,
  )
  try:
    yield ServerInfo(host, port, USER, PASSWORD, root, cert, key)
  finally:
    thread.stop()


@pytest.fixture(scope="session")
def tk_root():
  """One hidden Tk root per process (Tk 9 on macOS crashes on a second tk.Tk())."""
  tk = pytest.importorskip("tkinter")
  try:
    root = tk.Tk()
  except tk.TclError:
    pytest.skip("no display available")
  root.withdraw()
  yield root
  try:
    root.destroy()
  except tk.TclError:
    pass


@pytest.fixture
def tk_window(tk_root):
  """A fresh Toplevel of the shared root for each test; cleaned up at teardown."""
  import tkinter as tk
  window = tk.Toplevel(tk_root)
  yield window
  try:
    # A test may already have destroyed the window
    if window.winfo_exists():
      for child in window.winfo_children():
        child.destroy()
      window.destroy()
  except tk.TclError:
    pass


@pytest.fixture(autouse=True)
def isolated_ui_state(tmp_path_factory, monkeypatch):
  """No test may read or write the real ~/.minimalftp/ui.json."""
  import sys
  gui = sys.modules.get("minimalftp_gui")
  if gui is not None:
    path = tmp_path_factory.mktemp("ui-state") / "ui.json"
    monkeypatch.setattr(gui, "ui_state_path", lambda: path)


def pytest_configure(config):
  # Internal DeprecationWarning of pyftpdlib (MFMT on Python 3.12+), not ours
  config.addinivalue_line(
    "filterwarnings",
    "ignore:datetime.datetime.utcfromtimestamp:DeprecationWarning:pyftpdlib")
