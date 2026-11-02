"""Tests for configuration, errors and password resolution."""
import pytest

from minimalftp import FtpConfig, FtpError


def test_repr_hides_password():
  cfg = FtpConfig(host="h", password="segreto123")
  assert "segreto123" not in repr(cfg)


def test_password_sources_are_exclusive():
  with pytest.raises(ValueError):
    FtpConfig(host="h", password="a", password_env="X")
  with pytest.raises(ValueError):
    FtpConfig(host="h", password_env="X", password_provider=lambda: "a")


def test_password_env_resolved_lazily(monkeypatch):
  monkeypatch.delenv("MFTP_PW", raising=False)
  cfg = FtpConfig(host="h", password_env="MFTP_PW")
  monkeypatch.setenv("MFTP_PW", "dallambiente")
  assert cfg.resolve_password() == "dallambiente"


def test_password_env_missing_raises_ftperror(monkeypatch):
  monkeypatch.delenv("MFTP_PW", raising=False)
  with pytest.raises(FtpError):
    FtpConfig(host="h", password_env="MFTP_PW").resolve_password()


def test_provider_called_at_resolve():
  calls = []
  cfg = FtpConfig(host="h", password_provider=lambda: calls.append(1) or "pw")
  assert calls == []
  assert cfg.resolve_password() == "pw"
  assert calls == [1]


def test_no_password_resolves_empty():
  assert FtpConfig(host="h").resolve_password() == ""


def test_tls_value_validated():
  for ok in ("none", "explicit", "implicit"):
    assert FtpConfig(host="h", tls=ok).tls == ok
  with pytest.raises(ValueError):
    FtpConfig(host="h", tls="ssl")


def test_from_env_prefix(monkeypatch):
  monkeypatch.setenv("X_HOST", "ftp.example.com")
  monkeypatch.setenv("X_PORT", "2121")
  monkeypatch.setenv("X_USER", "bob")
  monkeypatch.setenv("X_PASSWORD", "pw")
  monkeypatch.setenv("X_TLS", "explicit")
  monkeypatch.setenv("X_VERIFY_CERT", "FALSE")
  monkeypatch.setenv("X_CA_FILE", "/tmp/ca.pem")
  monkeypatch.setenv("X_TIMEOUT", "5.5")
  cfg = FtpConfig.from_env("X_")
  assert (cfg.host, cfg.port, cfg.user, cfg.password) == ("ftp.example.com", 2121, "bob", "pw")
  assert cfg.tls == "explicit" and cfg.verify_cert is False
  assert cfg.ca_file == "/tmp/ca.pem" and cfg.timeout == 5.5


def test_from_env_missing_host(monkeypatch):
  monkeypatch.delenv("Y_HOST", raising=False)
  with pytest.raises(ValueError):
    FtpConfig.from_env("Y_")


def test_from_dict_unknown_key():
  assert FtpConfig.from_dict({"host": "h", "port": 22}).port == 22
  with pytest.raises(TypeError):
    FtpConfig.from_dict({"host": "h", "bogus": 1})


def test_to_safe_dict_has_no_password():
  cfg = FtpConfig(host="h", password="segreto123")
  d = cfg.to_safe_dict()
  assert "password" not in d and "password_provider" not in d
  assert "segreto123" not in str(d)
  assert d["host"] == "h"


@pytest.mark.parametrize("pw", ["a\r\nb", "a\nb", "a\rb"])
def test_password_with_crlf_rejected(pw):
  with pytest.raises(FtpError) as exc:
    FtpConfig(host="h", password=pw).resolve_password()
  assert pw not in str(exc.value)
