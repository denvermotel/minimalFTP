"""Tests for FTP, explicit FTPS and implicit FTPS connection."""
import logging
import socket
import ssl
import threading

import pytest

from minimalftp import FtpAuthError, FtpClient, FtpConfig, FtpConnectionError, connect


def _cfg(server, **kw):
  base = dict(host=server.host, port=server.port, user=server.user,
              password=server.password, timeout=5)
  base.update(kw)
  return FtpConfig(**base)


def test_plain_connect_and_close(ftp_server):
  client = connect(_cfg(ftp_server))
  assert isinstance(client, FtpClient)
  assert client._ftp.pwd() == "/"
  client.close()
  assert client._ftp.sock is None
  # close is idempotent
  client.close()


def test_context_manager_closes(ftp_server):
  with connect(_cfg(ftp_server)) as client:
    assert client._ftp.sock is not None
  assert client._ftp.sock is None


def test_wrong_password_raises_auth_error_without_password_in_message(ftp_server):
  bad = "Sbagliata-XYZ-987"
  with pytest.raises(FtpAuthError) as info:
    connect(_cfg(ftp_server, password=bad))
  assert bad not in str(info.value)
  assert ftp_server.password not in str(info.value)
  assert info.value.code == 530


def test_unreachable_host_raises_connection_error():
  # Free port obtained and immediately released: nobody listening
  s = socket.socket()
  s.bind(("127.0.0.1", 0))
  port = s.getsockname()[1]
  s.close()
  with pytest.raises(FtpConnectionError):
    connect(FtpConfig(host="127.0.0.1", port=port, password="x", timeout=2))


def test_explicit_tls_with_ca_file(tls_server):
  cfg = _cfg(tls_server, tls="explicit", ca_file=str(tls_server.cert))
  with connect(cfg) as client:
    assert isinstance(client._ftp.sock, ssl.SSLSocket)
    # Protected data channel (PROT P) with reused session
    (tls_server.root / "a.txt").write_text("ciao")
    assert client._ftp.nlst() == ["a.txt"]


def test_explicit_tls_selfsigned_without_ca_file_fails_verification(tls_server):
  cfg = _cfg(tls_server, tls="explicit")
  with pytest.raises(FtpConnectionError) as info:
    connect(cfg)
  assert tls_server.password not in str(info.value)


def test_verify_cert_false_logs_warning(tls_server, caplog):
  cfg = _cfg(tls_server, tls="explicit", verify_cert=False)
  with caplog.at_level(logging.WARNING, logger="minimalftp"):
    with connect(cfg):
      pass
    with connect(cfg):
      pass
  warnings = [r for r in caplog.records
              if r.name == "minimalftp" and r.levelno == logging.WARNING]
  assert len(warnings) == 2
  for r in warnings:
    assert tls_server.password not in r.getMessage()


def test_features_detected(ftp_server):
  with connect(_cfg(ftp_server)) as client:
    assert "MDTM" in client.features
    assert client.supports("mlst")
    assert client.supports("Size")
    assert not client.supports("NONESISTENTE")


# ---------------------------------------------------------------------------
# Implicit FTPS: pyftpdlib does not support it, so a mini fake server is used that
# wraps the socket in TLS before the banner (control channel only).
# ---------------------------------------------------------------------------


def _fake_implicit_server(cert, key, ready, seen):
  ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
  ctx.load_cert_chain(str(cert), str(key))
  lsock = socket.socket()
  lsock.bind(("127.0.0.1", 0))
  lsock.listen(1)
  lsock.settimeout(5)
  seen.append(lsock.getsockname()[1])
  ready.set()
  try:
    raw, _ = lsock.accept()
    with ctx.wrap_socket(raw, server_side=True) as conn:
      f = conn.makefile("rwb")
      f.write(b"220 implicito\r\n")
      f.flush()
      for line in f:
        cmd = line.decode().strip().split(" ")[0].upper()
        seen.append(cmd)
        replies = {
          "USER": b"331 password\r\n", "PASS": b"230 ok\r\n",
          "PBSZ": b"200 ok\r\n", "PROT": b"200 ok\r\n",
          "FEAT": b"211-Features:\r\n UTF8\r\n211 End\r\n",
        }
        if cmd == "QUIT":
          f.write(b"221 bye\r\n")
          f.flush()
          break
        f.write(replies.get(cmd, b"500 ?\r\n"))
        f.flush()
      f.close()
  except (OSError, ssl.SSLError):
    pass
  finally:
    lsock.close()


def test_implicit_tls_control_channel(tls_cert):
  cert, key = tls_cert
  ready, seen = threading.Event(), []
  t = threading.Thread(target=_fake_implicit_server, args=(cert, key, ready, seen),
                       daemon=True)
  t.start()
  assert ready.wait(5)
  port = seen.pop(0)
  cfg = FtpConfig(host="127.0.0.1", port=port, user="u", password="p",
                  tls="implicit", ca_file=str(cert), timeout=5)
  with connect(cfg) as client:
    assert isinstance(client._ftp.sock, ssl.SSLSocket)
    assert client.supports("utf8")
  t.join(5)
  # No AUTH TLS in implicit mode; PROT P after the login
  assert "AUTH" not in seen
  assert seen[:4] == ["USER", "PASS", "PBSZ", "PROT"]


def test_tls_context_requires_tls_12():
  import ssl
  from minimalftp import _make_ssl_context
  ctx = _make_ssl_context(FtpConfig(host="h"))
  assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
