"""minimalFTP - FTP/FTPS client in a single file, standard library only (Python 3.9+).

Public names:
  Configuration: FtpConfig
  Connection:    connect, FtpClient
  Errors:        FtpError, FtpConnectionError, FtpAuthError, FtpTransferError
  Sync:          Rules, Action, SyncResult, decide, MAX_SYNC_DEPTH
  Listings:      RemoteEntry, parse_mlsd_line, parse_list_line
  Version:       __version__

Minimal usage:
  cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
  with connect(cfg) as ftp:
    ftp.upload("a.txt", "/remoto/a.txt")

Conventions:
  - Remote paths always use "/": backslashes are converted to "/". A path that starts
    with "/" is absolute on the server, otherwise it is relative to the login directory.
  - Remote dates are UTC datetimes with timezone (tzinfo=timezone.utc).
  - One client per thread: FtpClient is not thread-safe and does not reconnect.
  - Errors: all derive from FtpError and have `.code` (FTP code, or None).
  - Log: logger "minimalftp" (warning if verify_cert=False; never passwords).
"""
from __future__ import annotations

import fnmatch
import ftplib
import logging
import os
import re
import socket
import ssl
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

__version__ = "1.0"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FtpError(Exception):
  """Base error of the library.

  Attributes: `code` is the FTP reply code (int), or None if unknown.
  The message never contains the password.
  """

  def __init__(self, message: str = "", code: Optional[int] = None):
    """`message`: error text; `code`: FTP reply code, if known."""
    super().__init__(message)
    self.code = code


class FtpConnectionError(FtpError):
  """Connection failed or lost: network, timeout, invalid TLS certificate,
  AUTH TLS or PROT P refused."""


class FtpAuthError(FtpError):
  """Login rejected by the server (wrong user or password); `code` is usually 530."""


class FtpTransferError(FtpError):
  """File operation failed: listing, upload, download, mkdir, remove, rename, exists.

  `code` is the FTP code of the reply (for example 550 for missing file or permission denied).
  """


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_TLS_MODES = ("none", "explicit", "implicit")


def _parse_bool(value: str) -> bool:
  v = value.strip().lower()
  if v in ("1", "true"):
    return True
  if v in ("0", "false"):
    return False
  raise ValueError("invalid boolean value: %r" % value)


@dataclass
class FtpConfig:
  """Connection parameters; passed to `connect()`.

  Fields:
    host: server name or address (required).
    port: control port (int, default 21; 990 for implicit FTPS).
    user: user name (default "anonymous").
    password: plain-text password (str); alternative to password_env and password_provider.
    password_env: name of an environment variable that holds the password,
      read when `connect()` is called.
    password_provider: function with no arguments that returns the password (str),
      called when `connect()` is called (keychain, `getpass`, etc.).
    tls: "none" (default, plain FTP), "explicit" (AUTH TLS) or "implicit" (FTPS).
    verify_cert: TLS certificate verification (default True). False only if necessary:
      writes a warning to the log at every connection.
    ca_file: path of a PEM file with a CA or self-signed certificate to trust
      (default None: system CAs).
    timeout: seconds to wait for each network operation (float, default 30.0).
    encoding: encoding of commands and names on the control channel (default "utf-8").

  At most one of password, password_env and password_provider; none = empty password.
  The password never appears in repr, logs or error messages.

  Errors: ValueError if `tls` is not valid or if more than one password source is set.
  """

  host: str
  port: int = 21
  user: str = "anonymous"
  # The password never appears in repr, logs or error messages
  password: Optional[str] = field(default=None, repr=False)
  password_env: Optional[str] = None
  password_provider: Optional[Callable[[], str]] = field(default=None, repr=False)
  tls: str = "none"
  verify_cert: bool = True
  ca_file: Optional[str] = None
  timeout: float = 30.0
  encoding: str = "utf-8"

  def __post_init__(self) -> None:
    """Validates `tls` and that the password sources are mutually exclusive (ValueError)."""
    if self.tls not in _TLS_MODES:
      raise ValueError("tls must be one of: " + ", ".join(_TLS_MODES))
    sources = [self.password, self.password_env, self.password_provider]
    if sum(s is not None for s in sources) > 1:
      raise ValueError(
        "set at most one password source among password, password_env, password_provider"
      )

  @classmethod
  def from_dict(cls, d: dict) -> "FtpConfig":
    """Creates the configuration from a dict with the field names (types already correct).

    Errors: TypeError for unknown keys or missing `host`; ValueError as the constructor.
    """
    return cls(**d)

  @classmethod
  def from_env(cls, prefix: str = "FTP_") -> "FtpConfig":
    """Creates the configuration from the environment variables `<prefix>NAME`.

    Variables read: HOST (required), PORT, USER, PASSWORD, TLS, VERIFY_CERT
    (1/true/0/false), CA_FILE, TIMEOUT. Missing ones leave the default.
    PASSWORD becomes the `password` field; for `password_env` or `password_provider`
    build FtpConfig directly.

    Errors: ValueError if HOST is missing, or if PORT, TIMEOUT, VERIFY_CERT or TLS are not valid.
    """
    env = os.environ
    host = env.get(prefix + "HOST")
    if not host:
      raise ValueError("missing environment variable: " + prefix + "HOST")
    kwargs: dict = {"host": host}
    if prefix + "PORT" in env:
      kwargs["port"] = int(env[prefix + "PORT"])
    if prefix + "USER" in env:
      kwargs["user"] = env[prefix + "USER"]
    if prefix + "PASSWORD" in env:
      kwargs["password"] = env[prefix + "PASSWORD"]
    if prefix + "TLS" in env:
      kwargs["tls"] = env[prefix + "TLS"]
    if prefix + "VERIFY_CERT" in env:
      kwargs["verify_cert"] = _parse_bool(env[prefix + "VERIFY_CERT"])
    if prefix + "CA_FILE" in env:
      kwargs["ca_file"] = env[prefix + "CA_FILE"]
    if prefix + "TIMEOUT" in env:
      kwargs["timeout"] = float(env[prefix + "TIMEOUT"])
    return cls(**kwargs)

  def to_safe_dict(self) -> dict:
    """Dict of the fields without `password` and `password_provider`; safe to log.

    It still contains `password_env` (only the variable name, not the secret).
    """
    skip = ("password", "password_provider")
    return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in skip}

  def resolve_password(self) -> str:
    """Returns the password to use ("" if no source is set).

    Reads the environment or calls the provider now, not at construction.
    Errors: FtpError if the `password_env` variable is not set or if the password
    contains line breaks; exceptions from the provider pass through unchanged.
    """
    if self.password is not None:
      pw = self.password
    elif self.password_env is not None:
      pw = os.environ.get(self.password_env)
      if pw is None:
        raise FtpError("password environment variable not set: " + self.password_env)
    elif self.password_provider is not None:
      pw = self.password_provider()
    else:
      return ""
    if "\r" in pw or "\n" in pw:
      raise FtpError("the password contains line break characters, which are not allowed")
    return pw


# ---------------------------------------------------------------------------
# Remote entries and parsing
# ---------------------------------------------------------------------------


@dataclass
class RemoteEntry:
  """Entry of a remote listing, as returned by `FtpClient.list()`.

  Fields: name (the name only, without path); is_dir (bool); size (bytes, as
  declared by the server; meaningless for directories); mtime (UTC datetime with timezone, or None if unknown); approximate (True if the
  date comes from LIST: server timezone unknown and seconds missing).
  """

  name: str
  is_dir: bool
  size: int
  mtime: Optional[datetime]
  # True when the date comes from LIST (server timezone unknown, seconds missing)
  approximate: bool = False


def _parse_mlsd_time(value: str) -> Optional[datetime]:
  """Interprets the `modify` fact (YYYYMMDDHHMMSS[.fraction]) as UTC."""
  m = re.fullmatch(r"(\d{14})(?:\.(\d+))?", value)
  if not m:
    return None
  try:
    base = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
  except ValueError:
    return None
  micro = int((m.group(2) or "0")[:6].ljust(6, "0"))
  return base.replace(microsecond=micro, tzinfo=timezone.utc)


def parse_mlsd_line(line: str) -> Optional[RemoteEntry]:
  """Interprets an MLSD line (`facts; name`) and returns a RemoteEntry.

  Returns None for cdir, pdir, `.`, `..` or invalid lines. The date is UTC.
  """
  line = line.rstrip("\r\n")
  # The name is everything after the first "; ", including spaces and semicolons
  idx = line.find("; ")
  if idx < 0:
    return None
  facts_part, name = line[:idx], line[idx + 2:]
  if not name or name in (".", ".."):
    return None
  facts = {}
  for item in facts_part.split(";"):
    key, sep, value = item.partition("=")
    if sep:
      facts[key.strip().lower()] = value
  kind = facts.get("type", "").lower()
  if kind in ("cdir", "pdir"):
    return None
  try:
    size = int(facts.get("size", "0"))
  except ValueError:
    size = 0
  mtime = _parse_mlsd_time(facts["modify"]) if "modify" in facts else None
  # Other types (OS.unix=slink, etc.) are treated as files
  return RemoteEntry(name, kind == "dir", size, mtime, False)


_MONTHS = {m: i + 1 for i, m in enumerate(
  ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
)}

_UNIX_RE = re.compile(
  # owner and group: the group may be missing (some servers show only the owner)
  r"^([-dlbcpsD])[-rwxsStTlL?]{9}[+@.]?\s+\d+\s+\S+(?:\s+\S+)?\s+(\d+)\s+"
  r"([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4}|\d{1,2}:\d{2}) (.+)$"
)
_WIN_RE = re.compile(
  r"^(\d{2})-(\d{2})-(\d{2}|\d{4})\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s+(<DIR>|\d+)\s+(.+)$"
)


def _infer_unix_datetime(month: int, day: int, hour: int, minute: int,
                         now: datetime) -> Optional[datetime]:
  """Missing year: that of `now`, or the previous one if the date falls more than 1 day in the future."""
  limit = now + timedelta(days=1)
  year = now.year
  # The loop also covers February 29: it steps back to the first year in which the date exists
  for _ in range(9):
    try:
      candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
      year -= 1
      continue
    if candidate > limit:
      year -= 1
      continue
    return candidate
  return None


def _parse_list_unix(line: str, now: datetime) -> Optional[RemoteEntry]:
  m = _UNIX_RE.match(line)
  if not m:
    return None
  kind, size, mon, day, when, name = m.groups()
  month = _MONTHS.get(mon.lower())
  if month is None:
    return None
  try:
    if ":" in when:
      hour, minute = (int(x) for x in when.split(":"))
      mtime = _infer_unix_datetime(month, int(day), hour, minute, now)
    else:
      mtime = datetime(int(when), month, int(day), tzinfo=timezone.utc)
  except ValueError:
    return None
  if kind == "l" and " -> " in name:
    name = name.split(" -> ", 1)[0]
  if name in (".", ".."):
    return None
  return RemoteEntry(name, kind in ("d", "D"), int(size), mtime, True)


def _parse_list_windows(line: str) -> Optional[RemoteEntry]:
  m = _WIN_RE.match(line)
  if not m:
    return None
  mm, dd, yy, hh, mi, ampm, size, name = m.groups()
  year = int(yy)
  if len(yy) == 2:
    year += 2000 if year < 70 else 1900
  hour = int(hh)
  if hour < 1 or hour > 12:
    return None
  hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
  try:
    mtime = datetime(year, int(mm), int(dd), hour, int(mi), tzinfo=timezone.utc)
  except ValueError:
    return None
  if name in (".", ".."):
    return None
  is_dir = size == "<DIR>"
  return RemoteEntry(name, is_dir, 0 if is_dir else int(size), mtime, True)


def parse_list_line(line: str, now: datetime) -> Optional[RemoteEntry]:
  """Interprets a LIST line in Unix or Windows/IIS format (RemoteEntry approximate).

  `now`: UTC datetime with timezone, used to infer the year in Unix dates without a year.
  Returns None if the line is not recognized or is `.` / `..`.
  """
  line = line.rstrip("\r\n")
  return _parse_list_unix(line, now) or _parse_list_windows(line)


_TOTAL_RE = re.compile(r"^total\s+\d+$", re.IGNORECASE)


def _is_ignorable_list_line(line: str) -> bool:
  """True for LIST lines with no useful entry: empty, `total N`, `.` and `..`."""
  text = line.strip()
  if not text or _TOTAL_RE.match(text):
    return True
  m = _UNIX_RE.match(line.rstrip("\r\n"))
  if m and m.group(6) in (".", ".."):
    return True
  m = _WIN_RE.match(line.rstrip("\r\n"))
  return bool(m and m.group(8) in (".", ".."))


# ---------------------------------------------------------------------------
# Sync rules and decision
# ---------------------------------------------------------------------------


@dataclass
class Rules:
  """Selection and comparison rules for sync; sync never deletes anything.

  Fields:
    max_age_days: float or None. Only files modified in the last N days (None: no
      limit). Also applies to files missing at the destination.
    modified_after: datetime WITH timezone or None. Only files modified after this instant.
      With max_age_days the more recent limit applies.
    newer_than_dest: bool, default True. Transfers if the source is newer than the
      destination beyond the tolerance.
    size_differs: bool, default True. Transfers if the size is different.
    overwrite_newer: bool, default False. If True, allows overwriting a newer
      destination when the size is different.
    mtime_tolerance_s: float, default 2.0. Date difference ignored, in seconds
      (at least 60 if a date comes from LIST).
    include: sequence of fnmatch patterns; if not empty, transfers only the files whose
      relative path (with "/") matches.
    exclude: sequence of fnmatch patterns to exclude; wins over include. A pattern
      that matches a directory excludes it entirely.

  Patterns are case-sensitive and `*` crosses "/".
  """

  max_age_days: Optional[float] = None
  modified_after: Optional[datetime] = None
  newer_than_dest: bool = True
  size_differs: bool = True
  overwrite_newer: bool = False
  mtime_tolerance_s: float = 2.0
  include: Sequence[str] = ()
  exclude: Sequence[str] = ()


@dataclass
class Action:
  """One entry of the sync plan.

  Fields: kind ("upload", "download", "skip" or "mkdir"); rel_path (path relative to the
  sync root directory, with "/"; "." for the root itself); reason (reason, see
  `decide`; for plan entries also "unsafe-name", "symlink", "type-conflict",
  "max-depth", "unreadable", "name-case-conflict"); size (bytes of the source file, 0 for directories and non-transferable entries).
  `mkdir` indicates a missing destination directory (reason "missing").
  """

  kind: str
  rel_path: str
  reason: str
  size: int


def decide(rel_path: str, src: RemoteEntry, dest: Optional[RemoteEntry],
           rules: Rules, now: datetime, direction: str) -> Action:
  """Decides what to do with a file; pure function, fixed evaluation order.

  `rel_path`: relative path (for the patterns); `src` and `dest`: RemoteEntry of the
  source and of the destination (`dest` None if missing); `rules`: Rules; `now`:
  UTC datetime with timezone; `direction`: "up" (kind upload) or "down" (kind download).
  Order: exclude, include, age, missing destination, unknown date on one side,
  newer destination, newer source or different size. With an unknown date
  and different sizes it is not known which is more recent: conflict-dest-newer, unless
  overwrite_newer=True. Reasons: excluded, not-included, age-unknown, too-old, missing,
  unchanged, conflict-dest-newer, size-differs, newer.
  Errors: ValueError if `direction` is not "up" or "down".
  """
  if direction == "up":
    kind = "upload"
  elif direction == "down":
    kind = "download"
  else:
    raise ValueError("direction must be 'up' or 'down'")

  def skip(reason: str) -> Action:
    return Action("skip", rel_path, reason, src.size)

  def transfer(reason: str) -> Action:
    return Action(kind, rel_path, reason, src.size)

  # 1. pattern filters (exclude wins over include)
  if any(fnmatch.fnmatchcase(rel_path, pat) for pat in rules.exclude):
    return skip("excluded")
  if rules.include and not any(
      fnmatch.fnmatchcase(rel_path, pat) for pat in rules.include):
    return skip("not-included")

  # 2. age filter, valid even if the destination is missing
  cutoffs = []
  if rules.max_age_days is not None:
    cutoffs.append(now - timedelta(days=rules.max_age_days))
  if rules.modified_after is not None:
    cutoffs.append(rules.modified_after)
  if cutoffs:
    cutoff = max(cutoffs)
    if src.mtime is None:
      return skip("age-unknown")
    if src.mtime < cutoff:
      return skip("too-old")

  # 3. missing destination
  if dest is None:
    return transfer("missing")

  # 4. tolerance (at least 60 s if a date is approximate)
  tolerance = rules.mtime_tolerance_s
  if src.approximate or dest.approximate:
    tolerance = max(tolerance, 60.0)
  both_known = src.mtime is not None and dest.mtime is not None
  size_differs = src.size != dest.size

  # 5. unknown date on one side: with different sizes the destination might be
  # the newer one, so it is treated as a conflict (never overwrite blindly)
  if not both_known:
    if not size_differs:
      return skip("unchanged")
    if not rules.overwrite_newer:
      return skip("conflict-dest-newer")
    if rules.size_differs:
      return transfer("size-differs")
    return skip("unchanged")

  # 6. newer destination
  if (dest.mtime - src.mtime).total_seconds() > tolerance:
    if not size_differs:
      return skip("unchanged")
    if not rules.overwrite_newer:
      return skip("conflict-dest-newer")
    if rules.size_differs:
      return transfer("size-differs")
    return skip("unchanged")

  # 7. newer source or different size
  if (rules.newer_than_dest
      and (src.mtime - dest.mtime).total_seconds() > tolerance):
    return transfer("newer")
  if size_differs and rules.size_differs:
    return transfer("size-differs")
  return skip("unchanged")


@dataclass
class SyncResult:
  """Outcome of `FtpClient.sync()`.

  Fields: actions (the plan, list[Action]); transferred (files transferred successfully);
  skipped (skip entries of the plan); errors (list of (rel_path, message) tuples of the
  failed files: the sync continues on the others); cancelled (True if stopped by the cancel event).
  With dry_run=True transferred is 0.
  """

  actions: "list[Action]"
  transferred: int
  skipped: int
  errors: "list[tuple[str, str]]"
  cancelled: bool


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_log = logging.getLogger("minimalftp")


class _ReuseSessionFTP_TLS(ftplib.FTP_TLS):
  """FTP_TLS that reuses the TLS session of the control channel on the data channel.

  Many servers (vsftpd with require_ssl_reuse, FileZilla Server) reject a data
  channel that does not resume the control session; ftplib up to 3.14
  does not pass `session=` to `wrap_socket`, so it has to be added here.
  """

  def ntransfercmd(self, cmd, rest=None):
    conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
    if self._prot_p:
      session = getattr(self.sock, "session", None)
      try:
        conn = self.context.wrap_socket(
          conn, server_hostname=self.host, session=session)
      except BaseException:
        conn.close()
        raise
    return conn, size


class _ImplicitFTP_TLS(_ReuseSessionFTP_TLS):
  """Implicit FTPS: the socket is wrapped in TLS before reading the banner."""

  def connect(self, host="", port=0, timeout=-999, source_address=None):
    if host != "":
      self.host = host
    if port > 0:
      self.port = port
    if timeout != -999:
      self.timeout = timeout
    if source_address is not None:
      self.source_address = source_address
    raw = socket.create_connection(
      (self.host, self.port), self.timeout, source_address=self.source_address)
    try:
      self.sock = self.context.wrap_socket(raw, server_hostname=self.host)
    except BaseException:
      raw.close()
      raise
    self.af = self.sock.family
    self.file = self.sock.makefile("r", encoding=self.encoding)
    self.welcome = self.getresp()
    return self.welcome


def _make_ssl_context(cfg: FtpConfig) -> ssl.SSLContext:
  """TLS context with verification on unless `verify_cert=False` is explicit."""
  ctx = ssl.create_default_context()
  # Python 3.9 still allows TLS 1.0/1.1 by default
  ctx.minimum_version = ssl.TLSVersion.TLSv1_2
  if cfg.ca_file:
    ctx.load_verify_locations(cfg.ca_file)
  if not cfg.verify_cert:
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
  return ctx


def _reply_code(exc: BaseException) -> Optional[int]:
  """Three-digit code at the start of the server reply, if present."""
  text = str(exc)
  if len(text) >= 3 and text[:3].isdigit():
    return int(text[:3])
  return None


def _parse_feat(reply: str) -> set:
  """Feature names from the FEAT lines that start with a space."""
  feats = set()
  for line in reply.splitlines():
    if line.startswith(" ") and line.strip():
      feats.add(line.split()[0].upper())
  return feats


# ---------------------------------------------------------------------------
# File operation support
# ---------------------------------------------------------------------------

# maximum descent depth in sync (defense against cyclic structures)
MAX_SYNC_DEPTH = 32
_PART_SUFFIX = ".minimalftp-part"
_OLD_SUFFIX = ".minimalftp-old"
_TRANSPORT_ERRORS = (ftplib.Error, OSError, EOFError, ValueError)


def _rpath(path: str) -> str:
  """Remote path with `/` separator (backslashes are converted)."""
  return path.replace("\\", "/")


def _is_inside(root: str, path: str) -> bool:
  """True if `path` (resolved parent + name) stays inside the resolved `root`."""
  real_root = os.path.realpath(root)
  real = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
  if os.path.normpath(path) == os.path.normpath(root):
    real = real_root
  try:
    return os.path.commonpath([real_root, real]) == real_root
  except ValueError:
    return False


def _fold_name(name: str) -> str:
  """Name comparison key: Unicode NFC and case-folded."""
  return unicodedata.normalize("NFC", name).casefold()


def _join(base: str, name: str) -> str:
  if not base:
    return name
  return base + name if base.endswith("/") else base + "/" + name


class _ReadProbe:
  """Wraps the local file and marks the start of the data phase (first read)."""

  def __init__(self, fh, state: dict):
    self._fh = fh
    self._state = state

  def read(self, size: int = -1) -> bytes:
    self._state["started"] = True
    try:
      return self._fh.read(size)
    except OSError as exc:
      # local file error, not a connection error
      self._state["local_exc"] = exc
      raise


def _call_progress(callback, done: int, total: int, state: dict) -> None:
  """Calls the callback remembering the exception, which must come out unchanged."""
  try:
    callback(done, total)
  except BaseException as exc:
    state["cb_exc"] = exc
    raise


def _is_transport_error(exc: BaseException, state: dict) -> bool:
  return isinstance(exc, _TRANSPORT_ERRORS) and exc is not state["cb_exc"]


def _is_link_error(exc: BaseException, state: dict) -> bool:
  """True if the exception comes from the network (not from the local file or the callback)."""
  return (isinstance(exc, (OSError, EOFError)) and exc is not state["cb_exc"]
          and exc is not state.get("local_exc"))


def _local_op(state: dict, func, *args):
  """Runs an operation on the local file remembering its OSError, if any."""
  try:
    return func(*args)
  except OSError as exc:
    state["local_exc"] = exc
    raise


def _to_transfer_error(what: str, exc: BaseException) -> FtpTransferError:
  """Converts an ftplib/OSError error into FtpTransferError (without sensitive data)."""
  if isinstance(exc, ftplib.Error):
    code = _reply_code(exc)
    return FtpTransferError("%s failed: %s" % (what, str(exc)), code)
  if isinstance(exc, ValueError):
    return FtpTransferError("%s failed: invalid name" % what)
  return FtpTransferError("%s failed: %s" % (what, type(exc).__name__))



class FtpClient:
  """Open FTP/FTPS session; created with `connect()` and also usable with `with`.

  Attributes: `cfg` (the FtpConfig used); `features` (set of the FEAT names announced
  by the server, in uppercase). Not thread-safe (one client per thread) and does not
  reconnect by itself. All remote paths use "/" (backslashes are converted).
  """

  def __init__(self, ftp: ftplib.FTP, cfg: FtpConfig):
    self._ftp = ftp
    self.cfg = cfg
    self.features: set = set()
    self._closed = False

  def __repr__(self) -> str:
    return "FtpClient(host=%r, port=%r, user=%r, tls=%r)" % (
      self.cfg.host, self.cfg.port, self.cfg.user, self.cfg.tls)

  def _load_features(self) -> None:
    try:
      reply = self._ftp.sendcmd("FEAT")
    except ftplib.Error:
      # FEAT not supported: no known features
      self.features = set()
      return
    self.features = _parse_feat(reply)

  def supports(self, feature: str) -> bool:
    """True if the server announced the feature in FEAT (e.g. "MLST", "MFMT").

    The name is compared literally: per RFC 3659 MLSD is implied by "MLST" and
    usually does not appear on its own (see `list()`).
    """
    return feature.upper() in self.features

  def _has_mlsd(self) -> bool:
    """MLSD available: RFC 3659 (7.8) announces it in FEAT as "MLST"."""
    return self.supports("MLST") or self.supports("MLSD")

  @contextmanager
  def _net(self, what: str):
    """Converts transport errors into FtpTransferError; a network error closes
    the session.

    This way the sync sees the client closed and stops with the partial result instead
    of trying (and waiting for the timeout) on every remaining file.
    """
    try:
      yield
    except _TRANSPORT_ERRORS as exc:
      if isinstance(exc, (OSError, EOFError)):
        self.close()
      raise _to_transfer_error(what, exc) from None

  def close(self) -> None:
    """Closes the session (QUIT, then forced close); harmless to call more than once."""
    if self._closed:
      return
    self._closed = True
    try:
      self._ftp.quit()
    except Exception:
      pass
    try:
      self._ftp.close()
    except Exception:
      pass

  def __enter__(self) -> "FtpClient":
    return self

  def __exit__(self, exc_type, exc, tb) -> None:
    self.close()

  # -------------------------------------------------------------------------
  # File operations
  # -------------------------------------------------------------------------

  def list(self, remote_dir: str) -> "list[RemoteEntry]":
    """Listing of the remote directory as list[RemoteEntry].

    `remote_dir`: directory (absolute or relative to the login; "" = current directory).
    Uses MLSD if the server announces MLST (or MLSD) in FEAT; otherwise falls back to LIST
    (+ MDTM for files). An unrecognized LIST line makes the listing fail instead of
    being ignored. A network error closes the session.
    Errors: FtpTransferError (missing directory 550, permission denied, unrecognized
    LIST lines, network).
    """
    path = _rpath(remote_dir)
    entries: list = []
    with self._net("listing of %s" % path):
      if self._has_mlsd():
        lines: list = []
        try:
          self._ftp.retrlines(("MLSD " + path).rstrip(), lines.append)
        except ftplib.error_perm as exc:
          # RFC 3659: 501 if the path is not a directory; some servers (pyftpdlib)
          # also use it for a missing directory. It is reported as 550, like LIST
          if _reply_code(exc) == 501:
            raise FtpTransferError(
              "listing of %s failed: %s" % (path, str(exc)), 550) from None
          raise
        for line in lines:
          entry = parse_mlsd_line(line)
          if entry is not None:
            entries.append(entry)
        return entries
      lines = []
      self._ftp.retrlines(("LIST " + path).rstrip(), lines.append)
      now = datetime.now(timezone.utc)
      unknown = [line for line in lines
                 if parse_list_line(line, now) is None and not _is_ignorable_list_line(line)]
      if unknown:
        # better to stop the scan than to make an existing file look missing
        raise FtpTransferError(
          "listing of %s: %d unrecognized LIST lines (first: %r)"
          % (path, len(unknown), unknown[0].rstrip("\r\n")[:120]))
      for line in lines:
        entry = parse_list_line(line, now)
        if entry is None:
          continue
        if not entry.is_dir:
          # MDTM is in UTC and has seconds: it replaces the approximate LIST date
          mtime = self._mdtm(_join(path, entry.name))
          if mtime is not None:
            entry.mtime = mtime
            entry.approximate = False
        entries.append(entry)
    return entries

  def _mdtm(self, remote: str) -> Optional[datetime]:
    """UTC mtime from MDTM, None if the server does not provide it (best effort)."""
    try:
      reply = self._ftp.sendcmd("MDTM " + remote)
    except ftplib.error_perm:
      return None
    except ftplib.error_temp:
      return None
    parts = reply.split(None, 1)
    if len(parts) < 2 or parts[0] != "213":
      return None
    return _parse_mlsd_time(parts[1].strip())

  def _size(self, remote: str) -> int:
    """Size from SIZE in binary mode; -1 if unknown."""
    try:
      self._ftp.voidcmd("TYPE I")
      reply = self._ftp.sendcmd("SIZE " + remote)
      return int(reply.split()[1])
    except (ftplib.error_perm, ftplib.error_temp, ValueError, IndexError):
      return -1

  def _drain_reply(self) -> None:
    """Consumes the pending reply on the control channel; if that fails, closes."""
    sock = self._ftp.sock
    if self._closed or sock is None:
      return
    try:
      sock.settimeout(3)
      self._ftp.getresp()
    except ftplib.Error:
      # reply received (even an error one): the channel is aligned
      pass
    except Exception:
      self.close()
      return
    try:
      sock.settimeout(self.cfg.timeout)
    except Exception:
      self.close()

  def _abort_remote_part(self, part: str, pending_reply: bool) -> None:
    """After an interrupted upload: aligns the channel and deletes the remote partial file."""
    if pending_reply:
      self._drain_reply()
    if self._closed:
      return
    try:
      self._ftp.delete(part)
    except ftplib.Error:
      pass
    except Exception:
      self.close()

  def upload(self, local: str, remote: str,
             on_progress: Optional[Callable[[int, int], None]] = None) -> None:
    """Sends the local file `local` to the remote path `remote` (a file, not a directory).

    Writes to `<remote>.minimalftp-part` and renames at the end of the transfer; an existing
    file is replaced (a directory is not). If the server supports MFMT it aligns the remote
    date with the local one.
    `on_progress(done, total)`: optional, called for every block with bytes sent and
    total; an exception raised by the callback interrupts the upload and passes unchanged.
    A network error on the control channel closes the session.
    Errors: FtpTransferError (local file missing, permission denied, network, rename
    failed, destination is a directory, `<remote>.minimalftp-old` already present).
    """
    final = _rpath(remote)
    part = final + _PART_SUFFIX
    state = {"started": False, "done": False, "cb_exc": None, "local_exc": None}
    try:
      total = _local_op(state, os.path.getsize, local)
      sent = 0

      def on_block(block: bytes) -> None:
        nonlocal sent
        sent += len(block)
        if on_progress is not None:
          _call_progress(on_progress, sent, total, state)

      with _local_op(state, open, local, "rb") as fh:
        self._ftp.storbinary("STOR " + part, _ReadProbe(fh, state), callback=on_block)
      state["done"] = True
      if total == 0 and on_progress is not None:
        _call_progress(on_progress, 0, 0, state)
      self._finalize_upload(part, final, state)
      self._set_remote_mtime(final, local)
    except BaseException as exc:
      in_data = state["started"] and not state["done"]
      if state["started"] and not self._closed and not state.get("keep_part"):
        pending = in_data and not isinstance(exc, ftplib.Error)
        self._abort_remote_part(part, pending)
      # network error outside the data phase: the control channel is not reliable
      # (in the data phase _abort_remote_part takes care of it, closing if it does not realign)
      if _is_link_error(exc, state) and not in_data:
        self.close()
      if _is_transport_error(exc, state):
        raise _to_transfer_error("upload of %s" % final, exc) from None
      raise

  def _finalize_upload(self, part: str, final: str, state: dict) -> None:
    try:
      self._ftp.rename(part, final)
      return
    except ftplib.error_perm:
      # some servers refuse to rename over an existing file
      if not self.exists(final):
        raise
    # a directory is never replaced: nothing is moved
    if self._is_dir(final):
      raise FtpTransferError("the destination is a directory: %s" % final, 550)
    # the old file goes through a backup: nothing is lost if the rename fails
    backup = final + _OLD_SUFFIX
    if self.exists(backup):
      # a backup left over from a previous attempt is never overwritten
      state["keep_part"] = True
      raise FtpTransferError(
        "%s already exists (backup of a previous upload): remove or "
        "recover it by hand; the upload was left in %s" % (backup, part))
    self._ftp.rename(final, backup)
    # from here the partial file must no longer be deleted: it is the only copy of the new content
    state["keep_part"] = True
    try:
      self._ftp.rename(part, final)
    except ftplib.Error as exc:
      try:
        self._ftp.rename(backup, final)
      except ftplib.Error:
        raise FtpTransferError(
          "rename failed and restore failed: old file in %s, "
          "upload left in %s" % (backup, part), _reply_code(exc)) from None
      raise FtpTransferError(
        "rename failed: the upload was left in %s" % part,
        _reply_code(exc)) from None
    try:
      self._ftp.delete(backup)
    except ftplib.Error as exc:
      _log.debug("backup %s not deleted: %s", backup, type(exc).__name__)

  def _set_remote_mtime(self, final: str, local: str) -> None:
    """Aligns the remote date (MFMT, best effort): the file is already uploaded, so
    no error comes out; a network error closes the session to stop the sync."""
    if not self.supports("MFMT"):
      return
    try:
      stamp = datetime.fromtimestamp(os.path.getmtime(local), timezone.utc)
    except (OSError, ValueError, OverflowError) as exc:
      _log.debug("local date not readable for %s: %s", local, type(exc).__name__)
      return
    try:
      self._ftp.sendcmd("MFMT %s %s" % (stamp.strftime("%Y%m%d%H%M%S"), final))
    except (ftplib.Error, ValueError) as exc:
      _log.debug("MFMT failed for %s: %s", final, type(exc).__name__)
    except (OSError, EOFError) as exc:
      _log.debug("MFMT failed for %s: %s", final, type(exc).__name__)
      self.close()

  def download(self, remote: str, local: str,
               on_progress: Optional[Callable[[int, int], None]] = None) -> None:
    """Downloads the remote file `remote` to the local path `local` (a file, not a directory).

    Writes to `<local>.minimalftp-part` and replaces `local` at the end of the download (the
    local directory must exist). If the server answers MDTM it sets the local
    modification date to the remote one.
    `on_progress(done, total)`: optional, called for every block; `total` is -1 if the
    server does not answer SIZE. Exceptions from the callback pass unchanged.
    A network error on the control channel closes the session.
    Errors: FtpTransferError (remote file missing 550, local error, network).
    """
    src = _rpath(remote)
    part = local + _PART_SUFFIX
    state = {"started": False, "done": False, "cb_exc": None, "local_exc": None}
    try:
      total = self._size(src)
      got = 0
      fh = _local_op(state, open, part, "wb")
      try:

        def on_block(block: bytes) -> None:
          nonlocal got
          _local_op(state, fh.write, block)
          got += len(block)
          if on_progress is not None:
            _call_progress(on_progress, got, total, state)

        state["started"] = True
        self._ftp.retrbinary("RETR " + src, on_block)
        state["done"] = True
      finally:
        _local_op(state, fh.close)
      if got == 0 and on_progress is not None:
        _call_progress(on_progress, 0, total, state)
      # A data channel cut without TLS close_notify reads as a clean end of stream, and the
      # server still answers 226 on the control channel: only the size can expose it
      if total >= 0 and got != total:
        raise FtpTransferError(
          "download of %s failed: received %d of %d bytes" % (src, got, total))
      _local_op(state, os.replace, part, local)
    except BaseException as exc:
      in_data = state["started"] and not state["done"]
      if in_data and not isinstance(exc, ftplib.Error):
        # _drain_reply closes the session if the channel does not realign
        self._drain_reply()
      elif _is_link_error(exc, state):
        self.close()
      try:
        os.remove(part)
      except OSError:
        pass
      if _is_transport_error(exc, state):
        raise _to_transfer_error("download of %s" % src, exc) from None
      raise
    # remote modification date on the local file (best effort)
    try:
      mtime = self._mdtm(src)
    except (ftplib.Error, ValueError):
      mtime = None
    except (OSError, EOFError):
      # the file is downloaded; but the session is no longer usable
      self.close()
      mtime = None
    if mtime is not None:
      try:
        stamp = mtime.timestamp()
        os.utime(local, (stamp, stamp))
      except (OSError, ValueError, OverflowError):
        pass

  def mkdir(self, remote: str, parents: bool = False) -> None:
    """Creates the remote directory `remote`.

    `parents=True`: also creates the missing levels and does not raise an error if the directory
    already exists. With `parents=False` an existing directory raises an error.
    Errors: FtpTransferError (existing without parents, permission denied, network).
    """
    path = _rpath(remote)
    with self._net("creating directory %s" % path):
      if not parents:
        self._ftp.mkd(path)
        return
      current = "/" if path.startswith("/") else ""
      for part in [p for p in path.split("/") if p]:
        current = _join(current, part) if current else part
        try:
          self._ftp.mkd(current)
        except ftplib.error_perm:
          # already existing only if the path really is a directory
          if not self._is_dir(current):
            raise

  def _is_dir(self, path: str) -> bool:
    """True if `path` is a directory; restores the working directory."""
    original = self._ftp.pwd()
    try:
      self._ftp.cwd(path)
    except ftplib.error_perm:
      return False
    self._ftp.cwd(original)
    return True

  def remove(self, remote: str) -> None:
    """Deletes the remote file `remote` (not directories).

    Errors: FtpTransferError (missing, permission denied, network).
    """
    path = _rpath(remote)
    with self._net("deleting %s" % path):
      self._ftp.delete(path)

  def rename(self, old: str, new: str) -> None:
    """Renames or moves `old` to `new` on the server (file or directory).

    Errors: FtpTransferError (source missing, invalid destination, network).
    """
    src, dst = _rpath(old), _rpath(new)
    with self._net("renaming %s" % src):
      self._ftp.rename(src, dst)

  def exists(self, remote: str) -> bool:
    """True if `remote` exists on the server as a file or as a directory.

    Uses SIZE, then CWD: on a server without SIZE a directory that cannot be entered
    appears as nonexistent. Errors: FtpTransferError only for network problems.
    """
    path = _rpath(remote)
    with self._net("checking %s" % path):
      try:
        self._ftp.voidcmd("TYPE I")
        self._ftp.sendcmd("SIZE " + path)
        return True
      except ftplib.error_perm:
        pass
      return self._is_dir(path)


  # -------------------------------------------------------------------------
  # Sync
  # -------------------------------------------------------------------------

  def _scan_local(self, path: str) -> dict:
    """Entries of a local directory: name -> (kind, RemoteEntry); links are not followed."""
    found: dict = {}
    with os.scandir(path) as it:
      for de in it:
        if de.name.endswith((_PART_SUFFIX, _OLD_SUFFIX)):
          continue
        if de.is_symlink():
          found[de.name] = ("link", None)
        elif de.is_dir(follow_symlinks=False):
          found[de.name] = ("dir", RemoteEntry(de.name, True, 0, None))
        elif de.is_file(follow_symlinks=False):
          st = de.stat(follow_symlinks=False)
          mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc)
          found[de.name] = ("file", RemoteEntry(de.name, False, st.st_size, mtime))
    return found

  def _scan_remote(self, path: str, rel: str, actions: list) -> dict:
    """Entries of a remote directory: name -> (kind, RemoteEntry); errors propagated.

    Hostile names (empty, `.`, `..`, with `/`, `\\` or NUL) are never transferred:
    they become skip actions "unsafe-name".
    """
    found: dict = {}
    for e in self.list(path):
      if e.name.endswith((_PART_SUFFIX, _OLD_SUFFIX)):
        continue
      if (not e.name or e.name in (".", "..")
          or any(c in e.name for c in ("/", "\\", "\0"))):
        rel_p = e.name if rel == "." else rel + "/" + e.name
        actions.append(Action("skip", rel_p, "unsafe-name", 0))
        continue
      found[e.name] = ("dir" if e.is_dir else "file", e)
    return found

  def _plan_dir(self, direction: str, rel: str, local_path: str, remote_path: str,
                dest_missing: bool, rules: Rules, now: datetime,
                actions: list, depth: int = 0) -> None:
    """Plans a directory and, recursively, its subdirectories."""
    up = direction == "up"
    try:
      if up:
        src = self._scan_local(local_path)
        dest: dict = {}
        if not dest_missing:
          try:
            dest = self._scan_remote(remote_path, rel, actions)
          except FtpTransferError as exc:
            # only the root can turn out missing: everything else is missing as a consequence
            if rel != "." or exc.code != 550:
              raise
            actions.append(Action("mkdir", ".", "missing", 0))
            dest_missing = True
      else:
        src = self._scan_remote(remote_path, rel, actions)
        dest = {} if dest_missing else self._scan_local(local_path)
    except (OSError, FtpTransferError) as exc:
      # an unreadable subdirectory (local, or remote with 550) is skipped without
      # stopping the plan; the root and network errors come out as before
      remote_denied = isinstance(exc, FtpTransferError) and exc.code == 550
      if rel == "." or not (remote_denied or isinstance(exc, OSError)):
        raise
      if actions and actions[-1] == Action("mkdir", rel, "missing", 0):
        actions.pop()
      actions.append(Action("skip", rel, "unreadable", 0))
      return
    # names compared case-insensitively and with Unicode NFC: macOS, Windows and many
    # Windows servers do not distinguish "README.TXT" from "readme.txt"
    dest_folded = {}
    for name in sorted(dest):
      dest_folded.setdefault(_fold_name(name), name)
    seen_src = set()
    for name in sorted(src):
      rel_p = name if rel == "." else rel + "/" + name
      src_kind, src_entry = src[name]
      key = _fold_name(name)
      if key in seen_src:
        # two source names that would be the same file at the destination
        actions.append(Action("skip", rel_p, "name-case-conflict", 0))
        continue
      seen_src.add(key)
      if name in dest:
        dest_kind, dest_entry = dest[name]
      elif key in dest_folded:
        # same file with a differently spelled name: never treat it as missing
        actions.append(Action("skip", rel_p, "name-case-conflict", 0))
        continue
      else:
        dest_kind, dest_entry = None, None
      if src_kind == "link" or dest_kind == "link":
        actions.append(Action("skip", rel_p, "symlink", 0))
        continue
      if src_kind == "dir":
        if any(fnmatch.fnmatchcase(p, pat) for pat in rules.exclude
               for p in (rel_p, rel_p + "/")):
          continue
        if dest_kind == "file":
          actions.append(Action("skip", rel_p, "type-conflict", 0))
          continue
        if depth + 1 > MAX_SYNC_DEPTH:
          actions.append(Action("skip", rel_p, "max-depth", 0))
          continue
        if dest_kind is None:
          actions.append(Action("mkdir", rel_p, "missing", 0))
        self._plan_dir(direction, rel_p, os.path.join(local_path, name),
                       _join(remote_path, name), dest_kind is None, rules, now,
                       actions, depth + 1)
        continue
      if dest_kind == "dir":
        actions.append(Action("skip", rel_p, "type-conflict", 0))
        continue
      actions.append(decide(rel_p, src_entry, dest_entry, rules, now, direction))

  def plan_sync(self, direction: str, local_dir: str, remote_dir: str,
                rules: Optional[Rules] = None) -> "list[Action]":
    """Sync plan as list[Action], without transferring or creating anything.

    `direction`: "up" (local -> remote) or "down" (remote -> local); `local_dir`,
    `remote_dir`: root directories; `rules`: Rules (None = default). Never includes
    deletions. If the destination directory is missing, the plan starts with
    Action("mkdir", ".", "missing", 0). Subdirectories are visited down to
    MAX_SYNC_DEPTH levels; local symbolic links are skipped ("symlink"); unreadable
    subdirectories are skipped ("unreadable"); names that differ only by case or
    Unicode normalization are skipped ("name-case-conflict").
    Errors: ValueError (direction), FileNotFoundError (up with a missing local
    directory), FtpTransferError (remote directory missing in "down", network).
    """
    if direction not in ("up", "down"):
      raise ValueError("direction must be 'up' or 'down'")
    rules = rules if rules is not None else Rules()
    now = datetime.now(timezone.utc)
    actions: list = []
    dest_missing = False
    if direction == "up":
      if not os.path.isdir(local_dir):
        raise FileNotFoundError("local directory missing: %s" % local_dir)
    elif not os.path.isdir(local_dir):
      actions.append(Action("mkdir", ".", "missing", 0))
      dest_missing = True
    self._plan_dir(direction, ".", local_dir, _rpath(remote_dir), dest_missing,
                   rules, now, actions)
    return actions

  def sync(self, direction: str, local_dir: str, remote_dir: str,
           rules: Optional[Rules] = None, dry_run: bool = False,
           on_progress: Optional[Callable[[Action, int, int], None]] = None,
           cancel: Optional[threading.Event] = None) -> SyncResult:
    """Synchronizes in one direction according to the rules; deletes nothing.

    `direction`, `local_dir`, `remote_dir`, `rules`: as in `plan_sync`.
    `dry_run`: if True returns only the plan, touching nothing.
    `on_progress(action, done, total)`: optional, called for every block of the current file.
    `cancel`: threading.Event; if set, the sync stops before the next file
    (`cancelled=True`); the current file finishes.
    An error on a single file goes into `SyncResult.errors` and the sync continues; if the
    connection drops (session closed) the sync stops and returns the partial
    result. Exceptions from `on_progress` and scan errors (as in
    plan_sync) come out.
    """
    actions = self.plan_sync(direction, local_dir, remote_dir, rules)
    skipped = sum(1 for a in actions if a.kind == "skip")
    result = SyncResult(actions, 0, skipped, [], False)
    if dry_run:
      return result
    remote_root = _rpath(remote_dir)
    for action in actions:
      if cancel is not None and cancel.is_set():
        result.cancelled = True
        break
      if action.kind == "skip":
        continue
      if self._closed:
        # session closed after a network error (even if the file succeeded): partial result
        result.errors.append((action.rel_path, "connection closed: sync interrupted"))
        break
      parts = [] if action.rel_path == "." else action.rel_path.split("/")
      local_path = os.path.join(local_dir, *parts)
      # defense in depth: nothing outside local_dir (hostile names, links)
      if not _is_inside(local_dir, local_path):
        result.errors.append((action.rel_path, "local path outside the directory"))
        continue
      remote_path = _join(remote_root, "/".join(parts)) if parts else remote_root
      if action.kind == "mkdir":
        if direction == "up":
          try:
            self.mkdir(remote_path, parents=True)
          except FtpTransferError as exc:
            result.errors.append((action.rel_path, str(exc)))
            if self._closed:
              break
        else:
          os.makedirs(local_path, exist_ok=True)
        continue

      def progress(done: int, total: int, action=action) -> None:
        if on_progress is not None:
          on_progress(action, done, total)

      try:
        if action.kind == "upload":
          self.upload(local_path, remote_path, progress)
        else:
          self.download(remote_path, local_path, progress)
        result.transferred += 1
      except FtpTransferError as exc:
        result.errors.append((action.rel_path, str(exc)))
        if self._closed:
          break
    return result

  def upload_dir(self, local_dir: str, remote_dir: str, **kw) -> SyncResult:
    """Recursive copy local -> server: `sync("up", local_dir, remote_dir, **kw)`.

    `kw`: rules (default Rules()), dry_run, on_progress, cancel. Returns SyncResult.
    """
    kw.setdefault("rules", Rules())
    return self.sync("up", local_dir, remote_dir, **kw)

  def download_dir(self, remote_dir: str, local_dir: str, **kw) -> SyncResult:
    """Recursive copy server -> local: `sync("down", local_dir, remote_dir, **kw)`.

    Mind the order: the REMOTE directory first, then the local one.
    `kw`: rules (default Rules()), dry_run, on_progress, cancel. Returns SyncResult.
    """
    kw.setdefault("rules", Rules())
    return self.sync("down", local_dir, remote_dir, **kw)


def _new_ftp(cfg: FtpConfig) -> ftplib.FTP:
  if cfg.tls == "none":
    return ftplib.FTP(timeout=cfg.timeout, encoding=cfg.encoding)
  if not cfg.verify_cert:
    _log.warning(
      "TLS certificate verification disabled for %s:%s", cfg.host, cfg.port)
  ctx = _make_ssl_context(cfg)
  cls = _ImplicitFTP_TLS if cfg.tls == "implicit" else _ReuseSessionFTP_TLS
  return cls(context=ctx, timeout=cfg.timeout, encoding=cfg.encoding)


def _force_close(ftp: ftplib.FTP) -> None:
  try:
    ftp.close()
  except Exception:
    pass


def connect(cfg: FtpConfig) -> FtpClient:
  """Opens and authenticates a session according to `cfg` and returns an FtpClient.

  Order: resolves the password, connects, AUTH TLS (if explicit), login, PROT P (if TLS),
  passive mode, FEAT. On error it closes everything.
  Errors: FtpAuthError (login rejected), FtpConnectionError (network, timeout, invalid
  certificate, AUTH TLS or PROT P refused), FtpError (password_env not set or
  password with line breaks).
  """
  password = cfg.resolve_password()
  ftp = _new_ftp(cfg)
  where = "%s:%s" % (cfg.host, cfg.port)
  try:
    # Phase 1: connection and, if explicit, AUTH TLS
    try:
      ftp.connect(cfg.host, cfg.port)
      if cfg.tls == "explicit":
        ftp.auth()
    except ssl.SSLCertVerificationError as exc:
      raise FtpConnectionError(
        "TLS certificate verification failed for %s: %s" % (where, exc.verify_message)
      ) from None
    except (OSError, EOFError) as exc:
      raise FtpConnectionError(
        "connection to %s failed: %s" % (where, type(exc).__name__)) from exc
    except ftplib.Error as exc:
      raise FtpConnectionError(
        "server %s refused the connection or AUTH TLS" % where,
        _reply_code(exc)) from None

    # Phase 2: login; the server reply is not included in the message
    try:
      ftp.login(cfg.user, password)
    except ftplib.error_perm as exc:
      code = _reply_code(exc)
      raise FtpAuthError("login rejected (code %s)" % code, code) from None
    except (OSError, EOFError) as exc:
      raise FtpConnectionError(
        "connection to %s lost during login: %s" % (where, type(exc).__name__)
      ) from None
    except ftplib.Error as exc:
      code = _reply_code(exc)
      raise FtpConnectionError(
        "login not completed on %s (code %s)" % (where, code), code) from None

    # Phase 3: protected data channel and passive mode
    try:
      if cfg.tls != "none":
        ftp.prot_p()
      ftp.set_pasv(True)
      client = FtpClient(ftp, cfg)
      client._load_features()
    except (OSError, EOFError) as exc:
      raise FtpConnectionError(
        "connection to %s lost after login: %s" % (where, type(exc).__name__)
      ) from None
    except ftplib.Error as exc:
      code = _reply_code(exc)
      raise FtpConnectionError(
        "server %s refused PROT P (code %s)" % (where, code), code) from None
  except BaseException:
    _force_close(ftp)
    raise
  return client
