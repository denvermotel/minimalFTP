# minimalFTP

**English** | [Italiano](#minimalftp---versione-italiana)

An FTP and FTPS client in a single Python file with no dependencies: `minimalftp.py`. Use it
as a library inside other programs (upload, download, list, sync folders). It also ships with a
small optional graphical interface (`minimalftp_gui.py`).

Repository: <https://github.com/denvermotel/minimalFTP>
License: MIT (see [LICENSE](LICENSE)).

Design choices: standard library only, one file to copy, no deletions in sync, and the
password never ends up in logs, `repr` or error messages.

## Contents

1. [Requirements and installation](#requirements-and-installation)
2. [Quick start](#quick-start)
3. [Configuration: FtpConfig](#configuration-ftpconfig)
4. [How to pass the password](#how-to-pass-the-password)
5. [Paths](#paths)
6. [File and folder operations](#file-and-folder-operations)
7. [Sync](#sync)
8. [FTPS: explicit TLS, implicit TLS, certificates](#ftps-explicit-tls-implicit-tls-certificates)
9. [Errors](#errors)
10. [Dates, time zones and MFMT](#dates-time-zones-and-mfmt)
11. [Graphical interface](#graphical-interface)
12. [Tests](#tests)
13. [Known limits](#known-limits)

## Requirements and installation

- Python 3.9 or later (tested end to end on 3.9.6, test suite on 3.14).
- Standard library only: no `pip install` needed to use the library.
- `minimalftp_gui.py` also needs `tkinter` (included in the official Python installers; on some
  Linux distributions it is the `python3-tk` package).

Installation: copy `minimalftp.py` into your program's folder (or into a folder on
`PYTHONPATH`) and import it.

```python
from minimalftp import FtpConfig, Rules, connect
```

`minimalftp_gui.py` is optional: if you want the window, copy it next to `minimalftp.py`.

Public names in `minimalftp.py`: `FtpConfig`, `connect`, `FtpClient`, `Rules`, `Action`,
`SyncResult`, `RemoteEntry`, `decide`, `parse_mlsd_line`, `parse_list_line`,
`MAX_SYNC_DEPTH`, `FtpError`, `FtpConnectionError`, `FtpAuthError`, `FtpTransferError`,
`__version__`. Everything else (names with a leading `_`) is internal and may change.

## Quick start

First put the password in an environment variable, outside the code:

```sh
export FTP_PASSWORD='your-password'
```

<!-- ex:quickstart -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")

with connect(cfg) as ftp:
  # upload a file (remote folders must be created first)
  ftp.mkdir("/documents", parents=True)
  ftp.upload("report.pdf", "/documents/report.pdf")

  # download the same file under another name
  ftp.download("/documents/report.pdf", "report-copy.pdf")

  # sync the local folder "site" to "/www" (copies only what is needed)
  result = ftp.sync("up", "site", "/www")
  print(result.transferred, "files transferred,", len(result.errors), "errors")
```

On the Internet always use TLS: `tls="explicit"` (see [FTPS](#ftps-explicit-tls-implicit-tls-certificates)).
The default `tls="none"` is plain FTP, password included.

`connect()` opens the connection, logs in and returns an `FtpClient`. With `with` the
connection closes by itself; without `with`, call `ftp.close()`.

## Configuration: FtpConfig

`FtpConfig` is a dataclass: create it with keyword arguments and pass it to `connect(cfg)`.
Only `host` is required.

| Parameter | Type | Default | Meaning | Example |
|---|---|---|---|---|
| `host` | `str` | required | Server name or address | `"ftp.example.com"` |
| `port` | `int` | `21` | Control channel port (990 is the usual one for implicit FTPS) | `port=2121` |
| `user` | `str` | `"anonymous"` | User name | `user="mario"` |
| `password` | `str` or `None` | `None` | Password in plain text in the code (discouraged, see below) | `password="test-secret"` |
| `password_env` | `str` or `None` | `None` | Name of the environment variable that holds the password, read at `connect()` | `password_env="FTP_PASSWORD"` |
| `password_provider` | `Callable[[], str]` or `None` | `None` | Function with no arguments that returns the password, called at `connect()` | `password_provider=lambda: getpass.getpass()` |
| `tls` | `str` | `"none"` | `"none"` (plain FTP), `"explicit"` (AUTH TLS, usually port 21) or `"implicit"` (TLS from the start, usually port 990) | `tls="explicit"` |
| `verify_cert` | `bool` | `True` | Verifies the certificate and the host name. `False` turns every check off and writes a warning to the log | `verify_cert=False` |
| `ca_file` | `str` or `None` | `None` | PEM file with the CA or the certificate (even self-signed) to trust; `None` uses the system CAs | `ca_file="/etc/ftp/server.pem"` |
| `timeout` | `float` | `30.0` | Seconds to wait for each network operation | `timeout=10.0` |
| `encoding` | `str` | `"utf-8"` | Encoding of commands and file names on the control channel | `encoding="latin-1"` |

Validation rules (`ValueError` at creation):

- `tls` must be one of `"none"`, `"explicit"`, `"implicit"`;
- at most one of `password`, `password_env`, `password_provider`. None of the three means an
  empty password (useful with `user="anonymous"`).

`repr(cfg)` and the logs never show `password` or `password_provider`.

## How to pass the password

Three ways, your choice, mutually exclusive. For real programs use the second or the third:
this way the password ends up neither in the source code nor in version control.

### 1. `password`: a string in the code

Fine for tests and local experiments.

<!-- ex:password -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password="test-secret")
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### 2. `password_env`: environment variable (recommended for scripts and servers)

You pass the **name** of the variable, not the value. It is read at `connect()`, not when you
create the configuration. If the variable does not exist `connect()` raises `FtpError`.

<!-- ex:password_env -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### 3. `password_provider`: function (recommended for interactive use and keyrings)

A function with no arguments that returns the password. It is called at every `connect()`.
With `getpass` the password is asked at the terminal without echoing it:

<!-- ex:password_provider -->
```python
import getpass
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_provider=lambda: getpass.getpass("FTP password: "),
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

The same pattern works for a keyring (for example the `keyring` library, if you use it) or a
secrets manager: all you need is a function that returns the string.

The password cannot contain line breaks (`connect()` raises `FtpError`).

### From environment variables: `FtpConfig.from_env`

Builds the whole configuration from the environment. The default prefix is `FTP_`
(`from_env(prefix="FTP_")`).

| Variable | Field | Notes |
|---|---|---|
| `FTP_HOST` | `host` | Required: if missing, `ValueError` |
| `FTP_PORT` | `port` | Integer |
| `FTP_USER` | `user` | |
| `FTP_PASSWORD` | `password` | Goes into the `password` field |
| `FTP_TLS` | `tls` | `none`, `explicit` or `implicit` |
| `FTP_VERIFY_CERT` | `verify_cert` | `1`, `true`, `0` or `false` |
| `FTP_CA_FILE` | `ca_file` | Path of the PEM file |
| `FTP_TIMEOUT` | `timeout` | Number (seconds) |

Missing variables leave the default. An invalid value (non-numeric port, unknown boolean,
wrong `tls`) raises `ValueError`. `password_env`, `password_provider` and `encoding` have no
variable: if you need them, build `FtpConfig` directly.

<!-- ex:from_env -->
```python
from minimalftp import FtpConfig, connect

# with: export FTP_HOST=ftp.example.com FTP_USER=mario FTP_PASSWORD=... FTP_TLS=explicit
cfg = FtpConfig.from_env()
cfg_staging = FtpConfig.from_env(prefix="STAGING_FTP_")  # reads STAGING_FTP_HOST, ...

with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### From a dictionary: `FtpConfig.from_dict`

The keys are the field names; the types must already be the right ones (for example `port`
as an integer). An unknown key or a missing `host` raises `TypeError`. A dictionary read from
a JSON file must not contain the password: add it afterwards, through `password_env` or a
provider.

<!-- ex:from_dict -->
```python
import json
from minimalftp import FtpConfig, connect

data = json.loads('{"host": "ftp.example.com", "port": 21, "user": "mario"}')
cfg = FtpConfig.from_dict({**data, "password_env": "FTP_PASSWORD"})
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### Logging the configuration without secrets: `to_safe_dict`

`cfg.to_safe_dict()` returns a dictionary with all the fields **except** `password` and
`password_provider`; it still contains `password_env` (only the name of the variable).
Do not use `dataclasses.asdict(cfg)` to save or log the configuration: it would include the
password.

<!-- ex:to_safe_dict -->
```python
import json
from minimalftp import FtpConfig

cfg = FtpConfig(host="ftp.example.com", user="mario", password="test-secret")
print(json.dumps(cfg.to_safe_dict()))   # no password
print(cfg)                              # repr: no password
```

## Paths

These rules apply to every **remote** path (arguments of `list`, `upload`, `download`,
`mkdir`, `remove`, `rename`, `exists`, `plan_sync`, `sync`, ...):

- **Absolute on the server** if they start with `/`: `"/documents/report.pdf"`.
  Note: on many servers a user's folder is "locked" (chroot) and `/` is its root, not the
  disk's.
- **Relative to the login folder** otherwise: `"documents/report.pdf"`.
- The separator is **always `/`**, even if you work from Windows. Backslashes in remote paths
  are converted to `/` (`"documents\\report.pdf"` is the same as `"documents/report.pdf"`), so
  a remote name that really contains a backslash cannot be reached.
- **Local** paths follow the operating system's rules instead (on Windows both `\` and `/`
  work); use raw strings (`r"C:\data\site"`) or `/`.
- Spaces, accents and characters such as `#`, `%`, `;` are allowed and are written as they
  are, with no quotes or encoding: `"/clients/Rossi & Sons/quote 2026.pdf"`.
- Names with line breaks, or not representable in the configured `encoding`, are rejected
  with `FtpTransferError` ("invalid name").
- The destination folder of `upload` and the folder of `download` must already exist
  (`mkdir(..., parents=True)` for remote ones). `sync`, instead, creates subfolders by itself.
- For `sync`, `upload_dir` and `download_dir` the paths are **root folders**; inside the plan
  each file has a `rel_path` relative to the root, always with `/`.

<!-- ex:paths -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  ftp.mkdir("/clients/Rossi & Sons", parents=True)      # absolute, with spaces
  ftp.upload("quote.pdf", "/clients/Rossi & Sons/quote 2026.pdf")
  ftp.upload("quote.pdf", "clients\\Rossi & Sons\\copy.pdf")  # backslash -> /
  print(ftp.exists("clients/Rossi & Sons/copy.pdf"))          # relative to the login
```

## File and folder operations

They all raise `FtpTransferError` (with `.code`, the FTP code, for example 550 if the file
does not exist) when the operation fails.

| Method | What it does |
|---|---|
| `list(remote_dir)` | Lists the folder: a list of `RemoteEntry(name, is_dir, size, mtime, approximate)` |
| `upload(local, remote, on_progress=None)` | Uploads a file; writes to `<remote>.minimalftp-part` and renames at the end; replaces an existing file, never a folder (error 550) |
| `download(remote, local, on_progress=None)` | Downloads a file; writes to `<local>.minimalftp-part` and renames at the end; a size different from the announced one is an error |
| `mkdir(remote, parents=False)` | Creates a folder; with `parents=True` also the missing levels, and no error if it already exists |
| `remove(remote)` | Deletes a **file** (not folders) |
| `rename(old, new)` | Renames or moves a file or a folder |
| `exists(remote)` | `True` if a file or a folder exists |
| `supports(name)` | `True` if the server announced the feature in `FEAT` (`"MLST"`, `"MFMT"`, ...); the name is compared literally |
| `close()` | Closes the session (harmless if called more than once) |

### Upload and download with progress

`on_progress(done, total)` is optional; it is called for every block with the bytes
transferred and the total bytes. In downloads `total` is `-1` if the server does not answer
`SIZE`. An exception raised by the callback stops the transfer and reaches you unchanged.

<!-- ex:progress -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")

def show(done, total):
  percent = 100 * done // total if total > 0 else 0
  print("\r%3d%%" % percent, end="", flush=True)

with connect(cfg) as ftp:
  ftp.upload("report.pdf", "/report.pdf", on_progress=show)
  print()
  ftp.download("/report.pdf", "downloaded.pdf", on_progress=show)
  print()
```

### List, create, rename, delete, check

<!-- ex:operations -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  ftp.mkdir("/archive/2026/november", parents=True)   # creates every level
  ftp.upload("report.pdf", "/archive/2026/november/report.pdf")

  for entry in ftp.list("/archive/2026/november"):
    kind = "folder" if entry.is_dir else "file"
    print(kind, entry.name, entry.size, entry.mtime)    # mtime: UTC datetime or None

  ftp.rename("/archive/2026/november/report.pdf", "/archive/2026/report-november.pdf")
  print(ftp.exists("/archive/2026/report-november.pdf"))   # True
  ftp.remove("/archive/2026/report-november.pdf")
  print(ftp.exists("/archive/2026/report-november.pdf"))   # False
```

Notes:

- `list()` uses `MLSD` if the server announces `MLST` in `FEAT` (per RFC 3659 `MLST` implies
  `MLSD`, which usually does not appear on its own; an explicit `MLSD` works too). Otherwise
  it falls back to `LIST` (Unix formats, also without the group column, and Windows/IIS) and
  asks for the exact date of each file with `MDTM`. If a `LIST` line is not recognized the
  listing fails with `FtpTransferError`: an unread file would look absent and the sync would
  overwrite it without comparing dates. See [Dates, time zones and MFMT](#dates-time-zones-and-mfmt).
- If the server refuses to rename over an existing file, `upload()` moves the old file to
  `<remote>.minimalftp-old`, renames the new one and deletes the backup. If
  `<remote>.minimalftp-old` already exists (left over from a previous attempt) the upload
  stops with an `FtpTransferError` that names it, without touching it; the upload stays in
  `<remote>.minimalftp-part`.
- A network error on the control channel (connection closed, timeout) closes the session:
  afterwards every operation fails until you open a new connection.
- `exists()` tries `SIZE` and then `CWD`: on a server without `SIZE`, a folder you cannot
  enter looks nonexistent.
- A file named `*.minimalftp-part` on the server is the remainder of an interrupted transfer
  (connection lost midway); you can delete it with `remove()`.

## Sync

Sync copies **in one direction only** (`"up"`: local -> server, `"down"`: server -> local),
recursively, creating missing folders. It **never deletes anything** and, by default, does not
overwrite a newer destination file.

Signatures:

```
ftp.plan_sync(direction, local_dir, remote_dir, rules=None)          -> list[Action]
ftp.sync(direction, local_dir, remote_dir, rules=None, dry_run=False,
         on_progress=None, cancel=None)                              -> SyncResult
ftp.upload_dir(local_dir, remote_dir, **kw)      # = sync("up", ...)
ftp.download_dir(remote_dir, local_dir, **kw)    # = sync("down", ...): REMOTE folder first
```

`kw` accepts `rules`, `dry_run`, `on_progress`, `cancel`. If you do not pass `rules`,
`Rules()` applies.

- `plan_sync` computes the plan without transferring or creating anything. Each entry is an
  `Action(kind, rel_path, reason, size)`, with `kind` one of `"upload"`, `"download"`,
  `"skip"`, `"mkdir"`.
- `sync` runs the plan. With `dry_run=True` it returns at once a `SyncResult` with only the
  plan in `.actions` (`transferred` stays 0).
- `SyncResult` has: `actions` (the plan), `transferred` (files transferred), `skipped`
  (`skip` entries), `errors` (list of `(rel_path, message)` for the failed files) and
  `cancelled`.
- `on_progress(action, done, total)` is called for every block of the current file.
- If the destination folder does not exist the plan starts with
  `Action("mkdir", ".", "missing", 0)`: the `rel_path` of the root is `"."`.
- A missing local folder in `"up"` raises `FileNotFoundError`; a missing remote folder in
  `"down"` raises `FtpTransferError` (code 550).

### Rules parameters

`Rules` decides which files to sync. All fields are optional.

| Parameter | Type | Default | Meaning | Example |
|---|---|---|---|---|
| `max_age_days` | `float` or `None` | `None` | Only files modified in the last N days (even if missing at the destination). `None`: no limit | `Rules(max_age_days=7)` |
| `modified_after` | `datetime` **with time zone** or `None` | `None` | Only files modified after this instant. If `max_age_days` is also set the more recent limit applies | `Rules(modified_after=datetime(2026, 1, 1, tzinfo=timezone.utc))` |
| `newer_than_dest` | `bool` | `True` | Transfer if the source is newer than the destination beyond the tolerance | `Rules(newer_than_dest=False)` |
| `size_differs` | `bool` | `True` | Transfer if the size differs from the destination | `Rules(size_differs=False)` |
| `overwrite_newer` | `bool` | `False` | Allow overwriting a newer destination when the size differs | `Rules(overwrite_newer=True)` |
| `mtime_tolerance_s` | `float` | `2.0` | Date difference ignored, in seconds. Rises to at least 60 if one of the two dates comes from `LIST` | `Rules(mtime_tolerance_s=5)` |
| `include` | sequence of `str` | `()` | `fnmatch` patterns: if not empty, only files whose relative path matches are transferred | `Rules(include=["*.html", "*.css"])` |
| `exclude` | sequence of `str` | `()` | `fnmatch` patterns to exclude; wins over `include`. If it matches a folder, the whole folder is excluded | `Rules(exclude=["*.tmp", "drafts/*"])` |

Pattern details: they are `fnmatch` **case-sensitive**, applied to the path relative to the
root folder with `/` as separator on every operating system; `*` crosses `/` (so `"*.log"`
also matches `"a/b/c.log"`). `include` applies to files, not folders (folders are always
visited); `exclude` applies to files and folders, and an excluded folder does not appear in
the plan.

### Preview (dry run) and sync

<!-- ex:sync_base -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  # 1. preview: no transfer
  plan = ftp.plan_sync("up", "site", "/www", Rules())
  for a in plan:
    if a.kind != "skip":
      print(a.kind, a.rel_path, a.reason, a.size)

  # same thing, but as a SyncResult
  trial = ftp.sync("up", "site", "/www", dry_run=True)
  print(len(trial.actions), "entries in the plan,", trial.transferred, "transferred")

  # 2. real run
  result = ftp.sync("up", "site", "/www")
  print(result.transferred, "transferred,", result.skipped, "skipped")

  # a second sync transfers nothing (if the server supports MFMT, see Dates)
  print(ftp.sync("up", "site", "/www").transferred)
```

### Only files modified in the last N days

<!-- ex:sync_recent -->
```python
from datetime import datetime, timezone
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  # last 7 days
  r = ftp.sync("up", "site", "/www", Rules(max_age_days=7))
  print(r.transferred)

  # or from a precise date (datetime WITH time zone)
  since_january = Rules(modified_after=datetime(2026, 1, 1, tzinfo=timezone.utc))
  r = ftp.sync("up", "site", "/www", since_january)
  print(r.transferred)
```

The age filter comes before everything else: a file older than N days is skipped even if it
is missing at the destination.

### Only if the size differs

<!-- ex:sync_size -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  # ignore dates: transfer missing files and files with a different size
  size_only = Rules(newer_than_dest=False, size_differs=True)
  r = ftp.sync("up", "site", "/www", size_only)
  print(r.transferred)
```

Useful with servers that do not keep the date (see [Dates](#dates-time-zones-and-mfmt)).

### Include and exclude patterns

<!-- ex:sync_patterns -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  rules = Rules(
    include=["*.html", "*.css", "img/*"],   # only these...
    exclude=["*.tmp", "drafts/*"],          # ...except these (exclude wins)
  )
  for a in ftp.plan_sync("up", "site", "/www", rules):
    print(a.kind, a.rel_path, a.reason)
```

### Overwriting a newer destination file too

By default, if the destination file is newer and the size differs, the entry is `skip` with
reason `conflict-dest-newer` and nothing is overwritten. With `overwrite_newer=True` you
overwrite it, but only if `size_differs` is also on (the default).

<!-- ex:sync_overwrite -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  conflicts = [a for a in ftp.plan_sync("up", "site", "/www") if a.reason == "conflict-dest-newer"]
  print(len(conflicts), "files on the server newer than the local ones")

  r = ftp.sync("up", "site", "/www", Rules(overwrite_newer=True))
  print(r.transferred)
```

### upload_dir and download_dir

Shortcuts for `sync("up", ...)` and `sync("down", ...)` with the default rules: they copy
recursively what is missing or changed, without deleting and without overwriting the newer
file. Mind the argument order of `download_dir`: **remote folder first**.

<!-- ex:dir -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  up = ftp.upload_dir("site", "/www")                  # local -> server
  down = ftp.download_dir("/www", "site-copy")         # server -> local (remote first!)
  print(up.transferred, down.transferred)

  # with rules, preview or callback: the same arguments as sync
  trial = ftp.upload_dir("site", "/www", rules=Rules(max_age_days=3), dry_run=True)
  print(len(trial.actions))
```

### Cancelling a sync with threading.Event

Pass a `threading.Event` as `cancel`. When you `set()` it, the sync **stops before the next
file** (the current file finishes) and `SyncResult.cancelled` is `True`. An `FtpClient` is not
safe across threads: the worker thread creates its own connection, and the rest of the
program touches only the `Event`.

<!-- ex:cancel -->
```python
import threading
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
stop = threading.Event()
outcomes = []

def work():
  with connect(cfg) as ftp:                      # one client per thread
    outcomes.append(ftp.sync("up", "site", "/www", cancel=stop))

t = threading.Thread(target=work)
t.start()
# ... elsewhere (Cancel button, Ctrl+C, timeout):
stop.set()
t.join()
print("cancelled:", outcomes[0].cancelled, "- transferred before the stop:", outcomes[0].transferred)
```

### Collecting the errors of single files

An error on one file **does not stop** the sync: it is added to `SyncResult.errors` as a
`(rel_path, message)` tuple and the sync moves on to the next file. If the connection drops
(even between two files, or right after a successful upload) the library closes the session,
the sync stops without trying the remaining files and returns the partial result: `errors`
contains the file in progress, or the next one with the message "connection closed: sync
interrupted". Scan errors (remote folder missing in `"down"`, network failure while listing,
unrecognized `LIST` lines) and exceptions from your own `on_progress` come out as exceptions
instead.

<!-- ex:sync_errors -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")

def progress(action, done, total):
  if done == total:
    print("finished:", action.rel_path)

with connect(cfg) as ftp:
  result = ftp.sync("up", "site", "/www", on_progress=progress)

for rel_path, message in result.errors:
  print("ERROR", rel_path, "-", message)
if result.cancelled:
  print("sync cancelled")
print(result.transferred, "transferred,", result.skipped, "skipped,",
      len(result.errors), "errors")
```

### Decision order

For each **file** of the source the library applies, in this order, the first rule that holds
(this is the pure function `decide()`):

| # | Condition | Outcome | `reason` |
|---|---|---|---|
| 1 | The path matches an `exclude` pattern | `skip` | `excluded` |
| 2 | `include` is not empty and the path matches none of its patterns | `skip` | `not-included` |
| 3 | There is an age limit (`max_age_days` or `modified_after`) and the source date is unknown | `skip` | `age-unknown` |
| 4 | There is an age limit and the source is older than the limit | `skip` | `too-old` |
| 5 | The destination does not exist | transfer | `missing` |
| 6 | The date is unknown on at least one side and the size is the same | `skip` | `unchanged` |
| 7 | The date is unknown on at least one side, the size differs and `overwrite_newer=False` | `skip` | `conflict-dest-newer` |
| 8 | As 7, but `overwrite_newer=True` and `size_differs=True` | transfer | `size-differs` |
| 9 | As 7, but `overwrite_newer=True` and `size_differs=False` | `skip` | `unchanged` |
| 10 | The destination is newer than the source beyond the tolerance and the size is the same | `skip` | `unchanged` |
| 11 | As 10, but the size differs and `overwrite_newer=False` | `skip` | `conflict-dest-newer` |
| 12 | As 11, but `overwrite_newer=True` and `size_differs=True` | transfer | `size-differs` |
| 13 | As 11, but `overwrite_newer=True` and `size_differs=False` | `skip` | `unchanged` |
| 14 | `newer_than_dest=True` and the source is newer than the destination beyond the tolerance | transfer | `newer` |
| 15 | `size_differs=True` and the size differs | transfer | `size-differs` |
| 16 | None of the above | `skip` | `unchanged` |

With an unknown date (6-9) there is no way to tell which side is newer: with different sizes
the destination might be the new version, so it is treated as a conflict and nothing is
overwritten without `overwrite_newer=True`. The tolerance is `mtime_tolerance_s`, but at
least 60 seconds if one of the two dates comes from `LIST`. The `newer_than_dest` and
`size_differs` criteria combine with OR (one is enough); age and patterns narrow with AND.

### Action.reason values

Besides the outcomes of the previous table (the `upload`/`download`/`skip` entries produced
by `decide()`), the plan contains these entries generated by the scan:

| `kind` | `reason` | Meaning |
|---|---|---|
| `mkdir` | `missing` | Destination folder missing: it will be created. `rel_path` is `"."` for the root |
| `skip` | `symlink` | Local symbolic link: never followed |
| `skip` | `type-conflict` | A file on one side and a folder on the other with the same name |
| `skip` | `max-depth` | Folder deeper than `MAX_SYNC_DEPTH` (32) levels: protection against cyclic structures |
| `skip` | `unsafe-name` | The server returned an empty name, `.`, `..` or one with `/`, backslash or NUL: never transferred |
| `skip` | `name-case-conflict` | The name differs only by case or Unicode normalization (NFC/NFD) from one existing at the destination, or from another source name (the first in alphabetical order is processed, the others are skipped). Applies to files and folders |
| `skip` | `unreadable` | A subfolder that cannot be read: local with permission denied, or remote whose listing answers 550. The rest of the sync goes on; an unreadable root folder raises the error instead |

Why `name-case-conflict`: the macOS and Windows file systems, and many FTP servers on
Windows, do not tell `README.TXT` from `readme.txt` (macOS not even `café` NFC from NFD).
Treating the file as absent would overwrite it without comparing dates; the sync skips it and
reports it, to be fixed by hand by renaming one of the two.

Files named `*.minimalftp-part` and `*.minimalftp-old` (the library's temporary files) are
ignored by the scan and do not appear in the plan.

## FTPS: explicit TLS, implicit TLS, certificates

| Mode | Configuration | When |
|---|---|---|
| Plain FTP | `tls="none"` (default) | Trusted networks or local tests only |
| **Explicit** FTPS | `tls="explicit"` | The client connects in the clear (usually port 21) and switches to TLS with `AUTH TLS`. The most common case |
| **Implicit** FTPS | `tls="implicit", port=990` | TLS from the very first connection (usually port 990) |

With TLS on, the data channel is protected (`PROT P`) and the TLS session of the control
channel is reused on the data channel, as many servers require. TLS 1.2 is the minimum
version. Certificate and host name verification is on by default.

<!-- ex:tls_explicit -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_env="FTP_PASSWORD",
  tls="explicit",            # certificate verified against the system CAs
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  port=990,
  user="mario",
  password_env="FTP_PASSWORD",
  tls="implicit",
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### Self-signed certificate: ca_file (recommended)

If the server uses a self-signed certificate or one from a private CA, **do not turn
verification off**: tell the library to trust that certificate with `ca_file`, a PEM file
(the server's certificate, or the CA that signed it).

<!-- ex:tls_ca_file -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_env="FTP_PASSWORD",
  tls="explicit",
  ca_file="ftp-ca.pem",      # server certificate exported as PEM
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

The name in `host` must appear in the certificate (`CN` or `subjectAltName`): name
verification stays on even with `ca_file`. If the certificate was issued for another name,
regenerate it with the right name, or use in `host` the name it contains.

### verify_cert=False: last resort

`verify_cert=False` turns off **both the certificate and the name verification**: anyone on
the network path can pose as the server and read the password. Use it only for tests on
trusted networks. At every connection the library writes a warning to the `minimalftp` logger
("TLS certificate verification disabled for host:port").

<!-- ex:tls_no_verify -->
```python
import logging
from minimalftp import FtpConfig, connect

logging.basicConfig(level=logging.WARNING)   # shows the library's warning

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_env="FTP_PASSWORD",
  tls="explicit",
  verify_cert=False,         # TESTS ONLY: no check on the server
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

If verification fails `connect()` raises `FtpConnectionError` with the reason (for example
untrusted self-signed certificate, expired, name mismatch).

### FileZilla note

The FileZilla fields (Site Manager) translate like this:

| FileZilla | `FtpConfig` |
|---|---|
| Host, Port, User, Password | `host`, `port`, `user`, and the password through one of the [three ways](#how-to-pass-the-password) |
| Encryption: "Only use plain FTP (insecure)" | `tls="none"` |
| Encryption: "Require explicit FTP over TLS" | `tls="explicit"` |
| Encryption: "Require implicit FTP over TLS" | `tls="implicit"` (port 990) |
| "Use explicit FTP over TLS if available" | No equivalent: the library does not fall back to plain text, choose `explicit` or `none` |
| Logon type "Anonymous" | `user="anonymous"` with no password |
| Transfer mode | **Passive** only (the library does not support active mode) |
| "Unknown certificate" dialog (accept) | `ca_file` with the server certificate as PEM |

FileZilla Server generally requires explicit TLS and the reuse of the TLS session on the data
channel: the library does both. It has not been tested against a real FileZilla Server (only
against a test server), so if you see handshake errors on the data channel, please report
them.

## Errors

All the library's errors derive from `FtpError` and have the `.code` attribute: the
three-digit FTP reply code when the server gave one, otherwise `None`. Messages never contain
the password.

| Class | When | Typical `.code` |
|---|---|---|
| `FtpError` | Base. Raised directly: `password_env` variable not set, password with line breaks | `None` |
| `FtpConnectionError` | Network unreachable, timeout, lost connection; invalid TLS certificate; `AUTH TLS` or `PROT P` refused | `None`, or the refusal code |
| `FtpAuthError` | Login refused (wrong user or password) | `530` |
| `FtpTransferError` | Failed file operation: listing, `upload`, `download`, `mkdir`, `remove`, `rename`, `exists`, sync scan, final rename | `550` (missing, permission denied), `None` for local or network errors |

Other standard exceptions you may meet:

- `ValueError`: invalid `tls`, several password sources, `direction` other than
  `"up"`/`"down"`, invalid environment variable in `from_env`;
- `TypeError`: unknown key or missing `host` in `from_dict`;
- `FileNotFoundError`: `plan_sync`/`sync` in `"up"` with a missing local folder;
- the exceptions of your own `on_progress` or `password_provider`, which pass through
  unchanged.

<!-- ex:error_handling -->
```python
from minimalftp import (FtpAuthError, FtpConfig, FtpConnectionError, FtpError,
                        FtpTransferError, connect)

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
try:
  with connect(cfg) as ftp:
    ftp.download("/does-not-exist.txt", "x.txt")
except FtpAuthError:
  print("wrong user or password")
except FtpConnectionError as exc:
  print("server not reachable:", exc)
except FtpTransferError as exc:
  print("operation failed:", exc, "- code", exc.code)    # 550
except FtpError as exc:
  print("other error:", exc)
```

## Dates, time zones and MFMT

- **Remote dates are always UTC `datetime`s with a time zone** (`tzinfo=timezone.utc`).
  Compare them with other time-zone-aware `datetime`s (`datetime.now(timezone.utc)`); to
  show them in local time use `entry.mtime.astimezone()`. Mixing aware and naive datetimes
  raises `TypeError`: `Rules.modified_after` must have a time zone too.
- **MLSD and MDTM** (RFC 3659) give the date in UTC with seconds: they are the preferred
  source. `list()` uses `MLSD` when the server announces `MLST` (or `MLSD`) in `FEAT`.
- **LIST (fallback)**: if the server announces neither `MLST` nor `MLSD`, the library reads
  the output of `LIST` (Unix or Windows/IIS), which does not give the time zone nor, often,
  the year and the seconds. The server time zone is assumed to be UTC, the missing year is
  inferred (if the date would fall in the future the previous year is used) and the entry
  carries `approximate=True`. For files the library then tries `MDTM`: if it answers, the
  date becomes exact and `approximate` goes back to `False`. If a date stays approximate, the
  sync tolerance rises to at least 60 seconds.
- **Servers with LIST only and no MDTM**: the `LIST` dates are assumed UTC. On a server whose
  clock is west of UTC (for example UTC-5) the remote dates come out older than the truth by
  that offset; a remote change made within the offset and with the same size can therefore be
  skipped as `unchanged` in `"down"`. East of UTC the effect is the opposite (remote dates
  look newer). If the server allows it, use one with `MLSD` or `MDTM`, or
  `Rules(newer_than_dest=False)` and compare only the size.
- **MFMT** (changing the date of a remote file): after an `upload` the library sets the
  remote date equal to the local file's, if the server announces `MFMT`
  (`ftp.supports("MFMT")`). This way a second sync sees equal dates and transfers nothing.
- **Download**: if the server answers `MDTM`, the modification date of the local file becomes
  the remote one (`os.utime`).
- **Servers without MFMT**: after the upload the remote date is the time of the upload, newer
  than the local one. An unmodified file has the same size and is skipped (`unchanged`); a
  local file modified after the upload looks newer and is uploaded again. One case stays
  uncovered: a local file with a date before the upload but different content (for example a
  restored backup): with a different size it is reported `conflict-dest-newer`, with the same
  size it is skipped. To force it use `Rules(overwrite_newer=True)`.

<!-- ex:dates -->
```python
from datetime import datetime, timedelta, timezone
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  print("MLST:", ftp.supports("MLST"), "- MFMT:", ftp.supports("MFMT"))
  yesterday = datetime.now(timezone.utc) - timedelta(days=1)
  for entry in ftp.list("/"):
    if entry.mtime is not None and entry.mtime > yesterday:
      print(entry.name, entry.mtime.astimezone(), "(approximate)" if entry.approximate else "")
```

## Graphical interface

`minimalftp_gui.py` is a small `tkinter` window: two panes, local and remote, to move files
and folders without writing code, plus a collapsible section to sync whole folders. It uses
the library in a worker thread and never touches `ftplib` directly. The text is available in
English and Italian.

Start it (with `minimalftp.py` in the same folder):

```sh
python minimalftp_gui.py
```

**Connection bar.** Profile (`Save` and `Delete`, the latter with confirmation), Host, Port,
User, Password (masked), TLS (`none`, `explicit`, `implicit`), then `Connect` and
`Disconnect`. Profiles live in `~/.minimalftp/profiles.json`. **The password is never
saved**: type it again at every start and every profile change. An empty user means
`anonymous`; an empty port means 21; the port must be a number from 1 to 65535, otherwise a
message appears in the log and nothing starts. The connection stays open between operations.
The language selector (English or Italiano) is remembered, together with the size and
position of the window, in `~/.minimalftp/ui.json`; the window always comes back inside the
screen.

**Panes.** The local pane is on the left and the remote one on the right, each with a path bar
(type a path and press Enter), a `..` row to go up, and columns for name, size and date.
Folders come first. Double-click or Enter opens a folder; you can select several rows. Each
pane has its own `New folder` button. `F5` reloads the active pane. The handle between the
panes and the lower part of the window adjusts their height.

**Transfers.** `Upload >` sends the rows selected in the local pane to the remote folder
shown; `< Download` gets the rows selected in the remote pane into the local folder shown.
Files and folders can be mixed. Nothing is ever overwritten without a yes:

- If a file already exists at the destination (names are compared ignoring case and Unicode
  normalization), a window shows both copies side by side, with size and date and time. The
  newer date is highlighted in amber and marked "(newer)", and a line says which file is
  newer and which is larger. The buttons are `Overwrite`, `Skip` (the default, so Enter and
  Esc skip) and `Cancel all`, which sends nothing. When there are several conflicts a box
  "Apply to all conflicts" applies the choice to the rest.
- For a folder, the two sides are compared first. If some files exist on both sides and
  differ, a window lists them in a table with both sizes and dates; `Overwrite all`, `Skip
  existing` (the default) or `Cancel`. Files that do not exist at the destination are always
  transferred.
- A name that is a file on one side and a folder on the other is reported in the log and
  nothing is sent.

**Sync section.** Under the panes, "Sync folders" opens and closes a section that uses the
open connection: **Local folder** (with `Choose...`), the **direction** (the central button
switches between `UP >>>`, local -> server, and `<<< DOWN`, server -> local), **Remote
folder** (required: for the server root write `/`) and the **criteria**: "only last N days"
(`max_age_days`), "different size" (`size_differs`), "newer" (`newer_than_dest`), "overwrite
if newer" (`overwrite_newer`). `Preview` computes and shows the plan without transferring
anything (the `skip` rows are visible with their reason; a filter above the list shows
everything, only the transfers, or only conflicts and errors). `Run` starts the sync with a
progress bar and the log; it computes the plan and then runs it again inside `sync`, so the
remote folder is read twice. `Cancel` stops the sync when the current file finishes.

The window has no fields for `include`/`exclude`, `ca_file`, `verify_cert` or `timeout`: it
uses the defaults (certificate verification on). For those options use the library.

**Shortcuts.** `F5` reloads the active pane, `Enter` opens the selected folder, `Ctrl+Enter`
(`Cmd+Enter` on macOS) runs the sync, `Esc` cancels the running job, `Tab` moves between
the fields with a visible focus ring. In the overwrite windows `Enter` and `Esc` skip, and
the underlined letter of `Overwrite` (`O` in English, `S` in Italian) overwrites. Closing the
window during a job cancels it and waits up to about 5 seconds; closing it with an overwrite
window open counts as "Cancel all".

**Look.** A light palette with a teal accent, amber for conflicts and brick red for errors.
The contrast ratios are checked by the tests (text at least 4.5 to 1, control borders at
least 3 to 1) and the selected row is clearly visible.

**Limits of the window.** No delete, no rename, no drag and drop; one operation at a time;
`Cancel` acts between one file and the next; large folder listings are loaded in one go.

## Tests

The tests use `pytest` and a local FTP/FTPS server (`pyftpdlib`); these are development
dependencies, not library ones.

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest
```

The FTPS tests also need the `openssl` command; without `openssl` or without `pyopenssl`
those tests are skipped.

## Known limits

The main ones, in short:

- No deletions, no two-way sync, no active mode, no SFTP.
- One `FtpClient` per thread; no automatic reconnection.
- Cancelling acts only between one file and the next.
- A connection lost midway through an upload can leave a `<name>.minimalftp-part` file on
  the server.
- Servers without `MFMT`: the second sync may behave differently (see
  [Dates](#dates-time-zones-and-mfmt)); there is no state file.
- Implicit FTPS and TLS session reuse on servers that require it are not tested against real
  servers; test suite on Python 3.14, on 3.9.6 only an end-to-end sync (`up` and `down`) was
  tried.
- Servers with `LIST` only and no `MDTM`, west of UTC: a remote change within the offset with
  the same size can be skipped (see [Dates](#dates-time-zones-and-mfmt)).
- Names that differ only by case or Unicode normalization are skipped (`name-case-conflict`),
  not synced.
- Local symbolic links are skipped; remote ones are not recognized.
- Graphical interface: profiles are saved without the password and not encrypted; no delete,
  rename or drag and drop.

---

# minimalFTP - versione italiana

[English](#minimalftp) | **Italiano**

Client FTP e FTPS in un solo file Python, senza dipendenze: `minimalftp.py`. Si usa come
libreria dentro altri programmi (carica, scarica, elenca, sincronizza cartelle) e include una
piccola interfaccia grafica opzionale (`minimalftp_gui.py`).

Repository: <https://github.com/denvermotel/minimalFTP>
Licenza: MIT (vedi [LICENSE](LICENSE)).

Scelte di fondo: solo libreria standard, un file da copiare, nessuna cancellazione nella
sync, la password non finisce mai in log, `repr` o messaggi di errore.

## Indice

1. [Requisiti e installazione](#requisiti-e-installazione)
2. [Avvio rapido](#avvio-rapido)
3. [Configurazione: FtpConfig](#configurazione-ftpconfig)
4. [Come passare la password](#come-passare-la-password)
5. [Percorsi](#percorsi)
6. [Operazioni su file e cartelle](#operazioni-su-file-e-cartelle)
7. [Sync](#sync)
8. [FTPS: TLS esplicito, implicito, certificati](#ftps-tls-esplicito-implicito-certificati)
9. [Errori](#errori)
10. [Date, fusi orari e MFMT](#date-fusi-orari-e-mfmt)
11. [Interfaccia grafica](#interfaccia-grafica)
12. [Test](#test)
13. [Limiti noti](#limiti-noti)

## Requisiti e installazione

- Python 3.9 o successivo (provato end to end su 3.9.6, suite di test su 3.14).
- Solo libreria standard: nessun `pip install` per usare la libreria.
- `minimalftp_gui.py` richiede anche `tkinter` (incluso negli installer ufficiali di Python; su alcune
  distribuzioni Linux è il pacchetto `python3-tk`).

Installazione: copia il file `minimalftp.py` nella cartella del tuo programma (o in una
cartella del `PYTHONPATH`) e importalo.

```python
from minimalftp import FtpConfig, Rules, connect
```

`minimalftp_gui.py` è facoltativo: se vuoi la finestra copia anche quello, accanto a `minimalftp.py`.

Nomi pubblici di `minimalftp.py`: `FtpConfig`, `connect`, `FtpClient`, `Rules`, `Action`,
`SyncResult`, `RemoteEntry`, `decide`, `parse_mlsd_line`, `parse_list_line`,
`MAX_SYNC_DEPTH`, `FtpError`, `FtpConnectionError`, `FtpAuthError`, `FtpTransferError`,
`__version__`. Tutto il resto (nomi con il `_` iniziale) è interno e può cambiare.

## Avvio rapido

Prima imposta la password in una variabile d'ambiente, fuori dal codice:

```sh
export FTP_PASSWORD='la-tua-password'
```

<!-- ex:quickstart -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")

with connect(cfg) as ftp:
  # carica un file (le cartelle remote vanno create prima)
  ftp.mkdir("/documenti", parents=True)
  ftp.upload("rapporto.pdf", "/documenti/rapporto.pdf")

  # scarica lo stesso file con un altro nome
  ftp.download("/documenti/rapporto.pdf", "copia-rapporto.pdf")

  # sincronizza la cartella locale "sito" verso "/www" (copia solo il necessario)
  result = ftp.sync("up", "sito", "/www")
  print(result.transferred, "file trasferiti,", len(result.errors), "errori")
```

Su Internet usa sempre TLS: `tls="explicit"` (vedi [FTPS](#ftps-tls-esplicito-implicito-certificati)).
Il default `tls="none"` è FTP in chiaro, password compresa.

`connect()` apre la connessione, fa il login e restituisce un `FtpClient`. Con `with` la
connessione si chiude da sola; senza `with` chiama `ftp.close()`.

## Configurazione: FtpConfig

`FtpConfig` è un dataclass: si crea con argomenti con nome e si passa a `connect(cfg)`.
Solo `host` è obbligatorio.

| Parametro | Tipo | Default | Significato | Esempio |
|---|---|---|---|---|
| `host` | `str` | obbligatorio | Nome o indirizzo del server | `"ftp.example.com"` |
| `port` | `int` | `21` | Porta del canale di controllo (990 è l'uso tipico per FTPS implicito) | `port=2121` |
| `user` | `str` | `"anonymous"` | Nome utente | `user="mario"` |
| `password` | `str` o `None` | `None` | Password in chiaro nel codice (sconsigliato, vedi sotto) | `password="segreto-di-prova"` |
| `password_env` | `str` o `None` | `None` | Nome della variabile d'ambiente che contiene la password, letta a `connect()` | `password_env="FTP_PASSWORD"` |
| `password_provider` | `Callable[[], str]` o `None` | `None` | Funzione senza argomenti che restituisce la password, chiamata a `connect()` | `password_provider=lambda: getpass.getpass()` |
| `tls` | `str` | `"none"` | `"none"` (FTP in chiaro), `"explicit"` (AUTH TLS, di solito porta 21) o `"implicit"` (TLS fin dall'inizio, di solito porta 990) | `tls="explicit"` |
| `verify_cert` | `bool` | `True` | Verifica del certificato e del nome host. `False` spegne ogni controllo e scrive un avviso nel log | `verify_cert=False` |
| `ca_file` | `str` o `None` | `None` | File PEM con la CA o il certificato (anche autofirmato) da considerare attendibile; `None` usa le CA di sistema | `ca_file="/etc/ftp/server.pem"` |
| `timeout` | `float` | `30.0` | Secondi di attesa per ogni operazione di rete | `timeout=10.0` |
| `encoding` | `str` | `"utf-8"` | Codifica di comandi e nomi di file sul canale di controllo | `encoding="latin-1"` |

Regole di validazione (errore `ValueError` alla creazione):

- `tls` deve essere uno tra `"none"`, `"explicit"`, `"implicit"`;
- al massimo una tra `password`, `password_env`, `password_provider`. Nessuna delle tre
  significa password vuota (utile con `user="anonymous"`).

`repr(cfg)` e i log non mostrano mai `password` né `password_provider`.

## Come passare la password

Tre modi, a scelta, mutuamente esclusivi. Per i programmi veri usa il secondo o il terzo:
così la password non finisce nel codice sorgente né nel controllo di versione.

### 1. `password`: stringa nel codice

Va bene per prove e test locali.

<!-- ex:password -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password="segreto-di-prova")
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### 2. `password_env`: variabile d'ambiente (consigliato per script e server)

Si passa il **nome** della variabile, non il valore. La lettura avviene a `connect()`, non
quando crei la configurazione. Se la variabile non esiste `connect()` solleva `FtpError`.

<!-- ex:password_env -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### 3. `password_provider`: funzione (consigliato per uso interattivo e portachiavi)

Una funzione senza argomenti che restituisce la password. È chiamata a ogni `connect()`.
Con `getpass` la password viene chiesta al terminale senza mostrarla:

<!-- ex:password_provider -->
```python
import getpass
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_provider=lambda: getpass.getpass("Password FTP: "),
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

Lo stesso schema vale per un portachiavi (ad esempio la libreria `keyring`, se la usi) o per
un gestore di segreti: basta una funzione che restituisca la stringa.

La password non può contenere ritorni a capo (`connect()` solleva `FtpError`).

### Da variabili d'ambiente: `FtpConfig.from_env`

Costruisce l'intera configurazione dall'ambiente. Il prefisso predefinito è `FTP_`
(`from_env(prefix="FTP_")`).

| Variabile | Campo | Note |
|---|---|---|
| `FTP_HOST` | `host` | Obbligatoria: se manca, `ValueError` |
| `FTP_PORT` | `port` | Intero |
| `FTP_USER` | `user` | |
| `FTP_PASSWORD` | `password` | Va nel campo `password` |
| `FTP_TLS` | `tls` | `none`, `explicit` o `implicit` |
| `FTP_VERIFY_CERT` | `verify_cert` | `1`, `true`, `0` o `false` |
| `FTP_CA_FILE` | `ca_file` | Percorso del file PEM |
| `FTP_TIMEOUT` | `timeout` | Numero (secondi) |

Le variabili assenti lasciano il default. Un valore non valido (porta non numerica, booleano
sconosciuto, `tls` errato) solleva `ValueError`. `password_env`, `password_provider` ed
`encoding` non hanno variabile: se servono, costruisci `FtpConfig` direttamente.

<!-- ex:from_env -->
```python
from minimalftp import FtpConfig, connect

# con: export FTP_HOST=ftp.example.com FTP_USER=mario FTP_PASSWORD=... FTP_TLS=explicit
cfg = FtpConfig.from_env()
cfg_staging = FtpConfig.from_env(prefix="STAGING_FTP_")  # legge STAGING_FTP_HOST, ...

with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### Da dizionario: `FtpConfig.from_dict`

Le chiavi sono i nomi dei campi; i tipi devono essere già quelli giusti (ad esempio `port`
intero). Una chiave sconosciuta o `host` mancante solleva `TypeError`. Un dizionario letto da
un file JSON non deve contenere la password: aggiungila dopo, via `password_env` o provider.

<!-- ex:from_dict -->
```python
import json
from minimalftp import FtpConfig, connect

dati = json.loads('{"host": "ftp.example.com", "port": 21, "user": "mario"}')
cfg = FtpConfig.from_dict({**dati, "password_env": "FTP_PASSWORD"})
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### Registrare la configurazione senza segreti: `to_safe_dict`

`cfg.to_safe_dict()` restituisce un dizionario con tutti i campi **tranne** `password` e
`password_provider`; contiene ancora `password_env` (solo il nome della variabile).
Non usare `dataclasses.asdict(cfg)` per salvare o registrare la configurazione: includerebbe la
password.

<!-- ex:to_safe_dict -->
```python
import json
from minimalftp import FtpConfig

cfg = FtpConfig(host="ftp.example.com", user="mario", password="segreto-di-prova")
print(json.dumps(cfg.to_safe_dict()))   # senza password
print(cfg)                              # repr: senza password
```

## Percorsi

Valgono per tutti i percorsi **remoti** (argomenti di `list`, `upload`, `download`, `mkdir`,
`remove`, `rename`, `exists`, `plan_sync`, `sync`, ...):

- **Assoluti sul server** se iniziano con `/`: `"/documenti/rapporto.pdf"`.
  Attenzione: su molti server la cartella di un utente è "chiusa" (chroot) e `/` è la sua
  radice, non quella del disco.
- **Relativi alla cartella di login** altrimenti: `"documenti/rapporto.pdf"`.
- Il separatore è **sempre `/`**, anche se lavori da Windows. I backslash nei percorsi remoti
  sono convertiti in `/` (`"documenti\\rapporto.pdf"` equivale a `"documenti/rapporto.pdf"`),
  quindi un nome remoto che contiene davvero un backslash non è raggiungibile.
- I percorsi **locali** seguono invece le regole del sistema operativo (su Windows vanno bene
  sia `\` sia `/`); usa stringhe raw (`r"C:\dati\sito"`) o `/`.
- Spazi, accenti e caratteri come `#`, `%`, `;` sono ammessi e vanno scritti così come sono,
  senza virgolette né codifiche: `"/clienti/Rossi & Figli/preventivo 2026.pdf"`.
- Nomi con ritorni a capo, o non rappresentabili nella `encoding` configurata, sono rifiutati
  con `FtpTransferError` ("invalid name").
- La cartella di destinazione di `upload` e la cartella di `download` devono già esistere
  (`mkdir(..., parents=True)` per quelle remote). `sync` invece crea da sé le sottocartelle.
- Per `sync`, `upload_dir` e `download_dir` i percorsi sono **cartelle radice**; dentro il
  piano ogni file ha un `rel_path` relativo alla radice, sempre con `/`.

<!-- ex:percorsi -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  ftp.mkdir("/clienti/Rossi & Figli", parents=True)      # assoluto, con spazi
  ftp.upload("preventivo.pdf", "/clienti/Rossi & Figli/preventivo 2026.pdf")
  ftp.upload("preventivo.pdf", "clienti\\Rossi & Figli\\copia.pdf")  # backslash -> /
  print(ftp.exists("clienti/Rossi & Figli/copia.pdf"))               # relativo al login
```

## Operazioni su file e cartelle

Tutte sollevano `FtpTransferError` (con `.code`, il codice FTP, ad esempio 550 se il file non
esiste) quando l'operazione fallisce.

| Metodo | Cosa fa |
|---|---|
| `list(remote_dir)` | Elenco della cartella: lista di `RemoteEntry(name, is_dir, size, mtime, approximate)` |
| `upload(local, remote, on_progress=None)` | Carica un file; scrive su `<remote>.minimalftp-part` e rinomina a fine invio; sostituisce un file esistente, mai una cartella (errore 550) |
| `download(remote, local, on_progress=None)` | Scarica un file; scrive su `<local>.minimalftp-part` e rinomina a fine download; una dimensione diversa da quella annunciata è un errore |
| `mkdir(remote, parents=False)` | Crea una cartella; con `parents=True` anche i livelli mancanti e nessun errore se esiste già |
| `remove(remote)` | Cancella un **file** (non le cartelle) |
| `rename(old, new)` | Rinomina o sposta un file o una cartella |
| `exists(remote)` | `True` se esiste un file o una cartella |
| `supports(name)` | `True` se il server ha annunciato la funzionalità in `FEAT` (`"MLST"`, `"MFMT"`, ...); il nome è confrontato alla lettera |
| `close()` | Chiude la sessione (innocuo se chiamato più volte) |

### Upload e download con avanzamento

`on_progress(done, total)` è facoltativo; viene chiamato a ogni blocco con i byte trasferiti
e i byte totali. In download `total` è `-1` se il server non risponde a `SIZE`.
Un'eccezione sollevata dal callback interrompe il trasferimento e arriva a te inalterata.

<!-- ex:progress -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")

def mostra(done, total):
  percento = 100 * done // total if total > 0 else 0
  print("\r%3d%%" % percento, end="", flush=True)

with connect(cfg) as ftp:
  ftp.upload("rapporto.pdf", "/rapporto.pdf", on_progress=mostra)
  print()
  ftp.download("/rapporto.pdf", "scaricato.pdf", on_progress=mostra)
  print()
```

### Elencare, creare, rinominare, cancellare, verificare

<!-- ex:operazioni -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  ftp.mkdir("/archivio/2026/novembre", parents=True)   # crea tutti i livelli
  ftp.upload("rapporto.pdf", "/archivio/2026/novembre/rapporto.pdf")

  for voce in ftp.list("/archivio/2026/novembre"):
    tipo = "cartella" if voce.is_dir else "file"
    print(tipo, voce.name, voce.size, voce.mtime)       # mtime: datetime UTC o None

  ftp.rename("/archivio/2026/novembre/rapporto.pdf", "/archivio/2026/rapporto-novembre.pdf")
  print(ftp.exists("/archivio/2026/rapporto-novembre.pdf"))   # True
  ftp.remove("/archivio/2026/rapporto-novembre.pdf")
  print(ftp.exists("/archivio/2026/rapporto-novembre.pdf"))   # False
```

Note:

- `list()` usa `MLSD` se il server annuncia `MLST` in `FEAT` (per RFC 3659 `MLST` implica
  `MLSD`, che di solito non compare da solo; vale anche `MLSD` esplicito). Altrimenti ripiega
  su `LIST` (formati Unix, anche senza la colonna del gruppo, e Windows/IIS) e chiede la data
  esatta di ogni file con `MDTM`. Se una riga di `LIST` non è riconosciuta l'elenco fallisce
  con `FtpTransferError`: un file non letto sembrerebbe assente e la sync lo sovrascriverebbe
  senza confrontare le date. Vedi [Date, fusi orari e MFMT](#date-fusi-orari-e-mfmt).
- Se il server rifiuta di rinominare sopra un file esistente, `upload()` sposta il vecchio
  file in `<remote>.minimalftp-old`, rinomina il nuovo e cancella il backup. Se
  `<remote>.minimalftp-old` esiste già (avanzo di un tentativo precedente) l'upload si ferma
  con `FtpTransferError` che lo nomina, senza toccarlo; il caricamento resta in
  `<remote>.minimalftp-part`.
- Un errore di rete sul canale di controllo (connessione chiusa, timeout) chiude la sessione:
  dopo, ogni operazione fallisce finché non apri una nuova connessione.
- `exists()` prova `SIZE` e poi `CWD`: su un server senza `SIZE`, una cartella in cui non puoi
  entrare risulta inesistente.
- Un file con nome `*.minimalftp-part` sul server è l'avanzo di un trasferimento interrotto
  (connessione persa a metà); si può cancellare con `remove()`.

## Sync

La sync copia **in un solo senso** (`"up"`: locale -> server, `"down"`: server -> locale),
ricorsivamente, creando le cartelle mancanti. **Non cancella mai nulla** e, per default, non
sovrascrive un file di destinazione più recente.

Firme:

```
ftp.plan_sync(direction, local_dir, remote_dir, rules=None)          -> list[Action]
ftp.sync(direction, local_dir, remote_dir, rules=None, dry_run=False,
         on_progress=None, cancel=None)                              -> SyncResult
ftp.upload_dir(local_dir, remote_dir, **kw)      # = sync("up", ...)
ftp.download_dir(remote_dir, local_dir, **kw)    # = sync("down", ...): prima la cartella REMOTA
```

`kw` accetta `rules`, `dry_run`, `on_progress`, `cancel`. Se non passi `rules`, vale `Rules()`.

- `plan_sync` calcola il piano senza trasferire né creare nulla. Ogni voce è un
  `Action(kind, rel_path, reason, size)`, con `kind` tra `"upload"`, `"download"`, `"skip"`,
  `"mkdir"`.
- `sync` esegue il piano. Con `dry_run=True` restituisce subito il `SyncResult` con il solo
  piano in `.actions` (`transferred` resta 0).
- `SyncResult` ha: `actions` (il piano), `transferred` (file trasferiti), `skipped` (voci
  `skip`), `errors` (lista di `(rel_path, messaggio)` dei file falliti) e `cancelled`.
- `on_progress(action, done, total)` è chiamato a ogni blocco del file in corso.
- Se la cartella di destinazione non esiste il piano comincia con
  `Action("mkdir", ".", "missing", 0)`: il `rel_path` della radice è `"."`.
- La cartella locale mancante in `"up"` solleva `FileNotFoundError`; la cartella remota
  mancante in `"down"` solleva `FtpTransferError` (codice 550).

### Parametri di Rules

`Rules` decide quali file sincronizzare. Tutti i campi sono facoltativi.

| Parametro | Tipo | Default | Significato | Esempio |
|---|---|---|---|---|
| `max_age_days` | `float` o `None` | `None` | Solo file modificati negli ultimi N giorni (anche se mancano a destinazione). `None`: nessun limite | `Rules(max_age_days=7)` |
| `modified_after` | `datetime` **con fuso** o `None` | `None` | Solo file modificati dopo questo istante. Se c'è anche `max_age_days` vale il limite più recente | `Rules(modified_after=datetime(2026, 1, 1, tzinfo=timezone.utc))` |
| `newer_than_dest` | `bool` | `True` | Trasferisci se la sorgente è più recente della destinazione oltre la tolleranza | `Rules(newer_than_dest=False)` |
| `size_differs` | `bool` | `True` | Trasferisci se la dimensione è diversa dalla destinazione | `Rules(size_differs=False)` |
| `overwrite_newer` | `bool` | `False` | Consenti di sovrascrivere una destinazione più recente quando la dimensione differisce | `Rules(overwrite_newer=True)` |
| `mtime_tolerance_s` | `float` | `2.0` | Differenza di data ignorata, in secondi. Sale ad almeno 60 se una delle due date arriva da `LIST` | `Rules(mtime_tolerance_s=5)` |
| `include` | sequenza di `str` | `()` | Pattern `fnmatch`: se non vuota, trasferisce solo i file il cui percorso relativo combacia | `Rules(include=["*.html", "*.css"])` |
| `exclude` | sequenza di `str` | `()` | Pattern `fnmatch` da escludere; vince su `include`. Se combacia con una cartella, la esclude per intero | `Rules(exclude=["*.tmp", "bozze/*"])` |

Dettagli sui pattern: sono `fnmatch` **con distinzione di maiuscole**, applicati al percorso
relativo alla cartella radice con `/` come separatore su ogni sistema operativo; `*`
attraversa le `/` (quindi `"*.log"` trova anche `"a/b/c.log"`). `include` vale per i file,
non per le cartelle (le cartelle si visitano sempre); `exclude` vale per file e cartelle, e
una cartella esclusa non compare nel piano.

### Anteprima (dry run) e sync

<!-- ex:sync_base -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  # 1. anteprima: nessun trasferimento
  piano = ftp.plan_sync("up", "sito", "/www", Rules())
  for a in piano:
    if a.kind != "skip":
      print(a.kind, a.rel_path, a.reason, a.size)

  # stessa cosa, ma come SyncResult
  prova = ftp.sync("up", "sito", "/www", dry_run=True)
  print(len(prova.actions), "voci nel piano,", prova.transferred, "trasferiti")

  # 2. esecuzione vera
  risultato = ftp.sync("up", "sito", "/www")
  print(risultato.transferred, "trasferiti,", risultato.skipped, "saltati")

  # una seconda sync non ritrasferisce nulla (se il server supporta MFMT, vedi Date)
  print(ftp.sync("up", "sito", "/www").transferred)
```

### Solo i file modificati negli ultimi N giorni

<!-- ex:sync_recenti -->
```python
from datetime import datetime, timezone
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  # ultimi 7 giorni
  r = ftp.sync("up", "sito", "/www", Rules(max_age_days=7))
  print(r.transferred)

  # oppure da una data precisa (datetime CON fuso)
  da_gennaio = Rules(modified_after=datetime(2026, 1, 1, tzinfo=timezone.utc))
  r = ftp.sync("up", "sito", "/www", da_gennaio)
  print(r.transferred)
```

Il filtro d'età viene prima di tutto il resto: un file più vecchio di N giorni è saltato anche
se manca a destinazione.

### Solo se la dimensione è diversa

<!-- ex:sync_dimensione -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  # ignora le date: trasferisce i file mancanti e quelli con dimensione diversa
  solo_dimensione = Rules(newer_than_dest=False, size_differs=True)
  r = ftp.sync("up", "sito", "/www", solo_dimensione)
  print(r.transferred)
```

Utile con server che non conservano la data (vedi [Date](#date-fusi-orari-e-mfmt)).

### Pattern include ed exclude

<!-- ex:sync_pattern -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  regole = Rules(
    include=["*.html", "*.css", "img/*"],   # solo questi...
    exclude=["*.tmp", "bozze/*"],           # ...tranne questi (exclude vince)
  )
  for a in ftp.plan_sync("up", "sito", "/www", regole):
    print(a.kind, a.rel_path, a.reason)
```

### Sovrascrivere anche un file di destinazione più nuovo

Per default, se il file di destinazione è più recente e la dimensione differisce, la voce è
`skip` con motivo `conflict-dest-newer` e niente viene sovrascritto. Con `overwrite_newer=True`
lo sovrascrivi, ma solo se anche `size_differs` è attivo (il default).

<!-- ex:sync_overwrite -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  conflitti = [a for a in ftp.plan_sync("up", "sito", "/www") if a.reason == "conflict-dest-newer"]
  print(len(conflitti), "file sul server più recenti del locale")

  r = ftp.sync("up", "sito", "/www", Rules(overwrite_newer=True))
  print(r.transferred)
```

### upload_dir e download_dir

Scorciatoie di `sync("up", ...)` e `sync("down", ...)` con le regole predefinite: copiano
ricorsivamente quello che manca o è cambiato, senza cancellare e senza sovrascrivere il più
nuovo. Attenzione all'ordine degli argomenti di `download_dir`: **prima la cartella remota**.

<!-- ex:dir -->
```python
from minimalftp import FtpConfig, Rules, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  su = ftp.upload_dir("sito", "/www")                 # locale -> server
  giu = ftp.download_dir("/www", "copia-sito")        # server -> locale (remota prima!)
  print(su.transferred, giu.transferred)

  # con regole, anteprima o callback: gli stessi argomenti di sync
  prova = ftp.upload_dir("sito", "/www", rules=Rules(max_age_days=3), dry_run=True)
  print(len(prova.actions))
```

### Annullare una sync con threading.Event

Passa un `threading.Event` come `cancel`. Quando lo imposti con `set()`, la sync **si ferma
prima del file successivo** (il file in corso termina) e `SyncResult.cancelled` vale `True`.
Un `FtpClient` non è sicuro tra thread: il thread di lavoro crea la propria connessione, il
resto del programma tocca solo l'`Event`.

<!-- ex:cancel -->
```python
import threading
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
stop = threading.Event()
esiti = []

def lavoro():
  with connect(cfg) as ftp:                      # un client per thread
    esiti.append(ftp.sync("up", "sito", "/www", cancel=stop))

t = threading.Thread(target=lavoro)
t.start()
# ... altrove (pulsante Annulla, Ctrl+C, timeout):
stop.set()
t.join()
print("annullata:", esiti[0].cancelled, "- trasferiti prima dello stop:", esiti[0].transferred)
```

### Raccogliere gli errori dei singoli file

Un errore su un file **non ferma** la sync: viene aggiunto a `SyncResult.errors` come tupla
`(rel_path, messaggio)` e si passa al file successivo. Se la connessione cade (anche tra un
file e l'altro, o subito dopo un upload riuscito) la libreria chiude la sessione, la sync si
interrompe senza tentare i file rimasti e restituisce il risultato parziale: in `errors`
compare il file in corso, oppure il successivo con il messaggio "connection closed: sync
interrupted". Gli errori di scansione (cartella remota assente in `"down"`, rete durante
l'elenco, righe `LIST` non riconosciute) e le eccezioni del tuo `on_progress` invece escono
come eccezioni.

<!-- ex:errori_sync -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")

def avanzamento(azione, fatto, totale):
  if fatto == totale:
    print("finito:", azione.rel_path)

with connect(cfg) as ftp:
  risultato = ftp.sync("up", "sito", "/www", on_progress=avanzamento)

for rel_path, messaggio in risultato.errors:
  print("ERRORE", rel_path, "-", messaggio)
if risultato.cancelled:
  print("sync annullata")
print(risultato.transferred, "trasferiti,", risultato.skipped, "saltati,",
      len(risultato.errors), "errori")
```

### Ordine di decisione

Per ogni **file** della sorgente la libreria applica, in quest'ordine, la prima regola che
si verifica (è la funzione pura `decide()`):

| # | Condizione | Esito | `reason` |
|---|---|---|---|
| 1 | Il percorso combacia con un pattern di `exclude` | `skip` | `excluded` |
| 2 | `include` non è vuota e il percorso non combacia con nessun pattern | `skip` | `not-included` |
| 3 | C'è un limite d'età (`max_age_days` o `modified_after`) e la data della sorgente è ignota | `skip` | `age-unknown` |
| 4 | C'è un limite d'età e la sorgente è più vecchia del limite | `skip` | `too-old` |
| 5 | La destinazione non esiste | trasferisci | `missing` |
| 6 | La data è ignota da almeno una parte e la dimensione è uguale | `skip` | `unchanged` |
| 7 | La data è ignota da almeno una parte, la dimensione differisce e `overwrite_newer=False` | `skip` | `conflict-dest-newer` |
| 8 | Come 7, ma `overwrite_newer=True` e `size_differs=True` | trasferisci | `size-differs` |
| 9 | Come 7, ma `overwrite_newer=True` e `size_differs=False` | `skip` | `unchanged` |
| 10 | La destinazione è più recente della sorgente oltre la tolleranza e la dimensione è uguale | `skip` | `unchanged` |
| 11 | Come 10, ma la dimensione differisce e `overwrite_newer=False` | `skip` | `conflict-dest-newer` |
| 12 | Come 11, ma `overwrite_newer=True` e `size_differs=True` | trasferisci | `size-differs` |
| 13 | Come 11, ma `overwrite_newer=True` e `size_differs=False` | `skip` | `unchanged` |
| 14 | `newer_than_dest=True` e la sorgente è più recente della destinazione oltre la tolleranza | trasferisci | `newer` |
| 15 | `size_differs=True` e la dimensione differisce | trasferisci | `size-differs` |
| 16 | Nessuna delle precedenti | `skip` | `unchanged` |

Con una data ignota (6-9) non si può sapere quale lato sia più recente: con dimensioni
diverse la destinazione potrebbe essere la versione nuova, quindi si tratta come un conflitto
e niente viene sovrascritto senza `overwrite_newer=True`. La tolleranza è `mtime_tolerance_s`,
ma almeno 60 secondi se una delle due date arriva da `LIST`. I criteri `newer_than_dest` e
`size_differs` si combinano in OR (ne basta uno); l'età e i pattern restringono in AND.

### Valori di Action.reason

Oltre agli esiti della tabella precedente (le voci `upload`/`download`/`skip` prodotte da
`decide()`), il piano contiene queste voci generate dalla scansione:

| `kind` | `reason` | Significato |
|---|---|---|
| `mkdir` | `missing` | Cartella di destinazione assente: sarà creata. `rel_path` è `"."` per la radice |
| `skip` | `symlink` | Collegamento simbolico locale: non si segue mai |
| `skip` | `type-conflict` | File da una parte e cartella dall'altra con lo stesso nome |
| `skip` | `max-depth` | Cartella oltre `MAX_SYNC_DEPTH` (32) livelli: difesa da strutture cicliche |
| `skip` | `unsafe-name` | Il server ha restituito un nome vuoto, `.`, `..` o con `/`, backslash o NUL: non si trasferisce mai |
| `skip` | `name-case-conflict` | Il nome differisce solo per maiuscole o normalizzazione Unicode (NFC/NFD) da uno esistente a destinazione, oppure da un altro nome della sorgente (si elabora il primo in ordine alfabetico, gli altri sono saltati). Vale per file e cartelle |
| `skip` | `unreadable` | Sottocartella che non si può leggere: locale con permesso negato, o remota il cui elenco risponde 550. Il resto della sync prosegue; la cartella radice illeggibile invece solleva l'errore |

Perché `name-case-conflict`: i filesystem di macOS e Windows, e molti server FTP su Windows,
non distinguono `README.TXT` da `readme.txt` (macOS nemmeno `café` NFC da NFD). Trattare il
file come assente lo farebbe sovrascrivere senza confrontare le date; la sync lo salta e lo
segnala, da sistemare a mano rinominando una delle due parti.

I file chiamati `*.minimalftp-part` e `*.minimalftp-old` (temporanei della libreria) sono
ignorati dalla scansione e non compaiono nel piano.

## FTPS: TLS esplicito, implicito, certificati

| Modo | Configurazione | Quando |
|---|---|---|
| FTP in chiaro | `tls="none"` (default) | Solo reti fidate o prove locali |
| FTPS **esplicito** | `tls="explicit"` | Il client si connette in chiaro (di solito porta 21) e passa a TLS con `AUTH TLS`. È il caso più comune |
| FTPS **implicito** | `tls="implicit", port=990` | TLS fin dalla prima connessione (di solito porta 990) |

Con TLS attivo il canale dati è protetto (`PROT P`) e la sessione TLS del canale di controllo
è riusata sul canale dati, come richiedono molti server. La verifica del certificato e del nome
host è attiva per default. La versione minima è TLS 1.2.

<!-- ex:tls_esplicito -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_env="FTP_PASSWORD",
  tls="explicit",            # certificato verificato con le CA di sistema
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  port=990,
  user="mario",
  password_env="FTP_PASSWORD",
  tls="implicit",
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

### Certificato autofirmato: ca_file (consigliato)

Se il server usa un certificato autofirmato o di una CA privata, **non spegnere la verifica**:
indica alla libreria di fidarsi di quel certificato con `ca_file`, un file PEM (il certificato
del server, o la CA che lo ha firmato).

<!-- ex:tls_ca_file -->
```python
from minimalftp import FtpConfig, connect

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_env="FTP_PASSWORD",
  tls="explicit",
  ca_file="ftp-ca.pem",      # certificato del server esportato in formato PEM
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

Il nome in `host` deve comparire nel certificato (campo `CN` o `subjectAltName`): la verifica
del nome resta attiva anche con `ca_file`. Se il certificato è emesso per un altro nome,
rigeneralo con il nome giusto, oppure usa nel `host` il nome che contiene.

### verify_cert=False: ultima risorsa

`verify_cert=False` spegne **sia la verifica del certificato sia quella del nome**: chiunque
si trovi nel percorso di rete può fingersi il server e leggere la password. Usalo solo per
prove su reti fidate. A ogni connessione la libreria scrive un avviso sul logger
`minimalftp` ("TLS certificate verification disabled for host:port").

<!-- ex:tls_no_verify -->
```python
import logging
from minimalftp import FtpConfig, connect

logging.basicConfig(level=logging.WARNING)   # fa vedere l'avviso della libreria

cfg = FtpConfig(
  host="ftp.example.com",
  user="mario",
  password_env="FTP_PASSWORD",
  tls="explicit",
  verify_cert=False,         # SOLO PER PROVE: nessun controllo sul server
)
with connect(cfg) as ftp:
  print(ftp.list("/"))
```

Se la verifica fallisce `connect()` solleva `FtpConnectionError` con il motivo (ad esempio
certificato autofirmato non attendibile, scaduto, nome non corrispondente).

### Nota FileZilla

I campi di FileZilla (Gestore siti) si traducono così:

| FileZilla | `FtpConfig` |
|---|---|
| Host, Porta, Utente, Password | `host`, `port`, `user`, e la password per uno dei [tre modi](#come-passare-la-password) |
| Cifratura: "Solo FTP semplice (insicuro)" | `tls="none"` |
| Cifratura: "Richiedi FTP su TLS esplicito" | `tls="explicit"` |
| Cifratura: "Richiedi FTP su TLS implicito" | `tls="implicit"` (porta 990) |
| "Usa FTP su TLS esplicito se disponibile" | Nessun equivalente: la libreria non ripiega sul chiaro, scegli `explicit` o `none` |
| Tipo di accesso "Anonimo" | `user="anonymous"` senza password |
| Modalità di trasferimento | Solo **passiva** (la libreria non supporta la modalità attiva) |
| Finestra "Certificato sconosciuto" (accetta) | `ca_file` con il certificato del server in PEM |

FileZilla Server richiede in genere TLS esplicito e il riuso della sessione TLS sul canale
dati: la libreria lo fa. Non è stato provato contro un FileZilla Server reale (solo contro
un server di prova), quindi se vedi errori di handshake sul canale dati segnalali.

## Errori

Tutti gli errori della libreria derivano da `FtpError` e hanno l'attributo `.code`: il codice
di risposta FTP a tre cifre quando il server ne ha dato uno, altrimenti `None`. I messaggi non
contengono mai la password.

| Classe | Quando | `.code` tipico |
|---|---|---|
| `FtpError` | Base. Direttamente: variabile `password_env` non impostata, password con ritorni a capo | `None` |
| `FtpConnectionError` | Rete irraggiungibile, timeout, connessione persa; certificato TLS non valido; `AUTH TLS` o `PROT P` rifiutati | `None`, o il codice del rifiuto |
| `FtpAuthError` | Login rifiutato (utente o password errati) | `530` |
| `FtpTransferError` | Operazione su file fallita: elenco, `upload`, `download`, `mkdir`, `remove`, `rename`, `exists`, scansione della sync, rinomina finale | `550` (assente, permesso negato), `None` per errori locali o di rete |

Altre eccezioni standard che puoi incontrare:

- `ValueError`: `tls` non valido, più fonti password, `direction` diversa da `"up"`/`"down"`,
  variabile d'ambiente non valida in `from_env`;
- `TypeError`: chiave sconosciuta o `host` mancante in `from_dict`;
- `FileNotFoundError`: `plan_sync`/`sync` in `"up"` con cartella locale assente;
- le eccezioni del tuo `on_progress` o del tuo `password_provider`, che passano inalterate.

<!-- ex:gestione_errori -->
```python
from minimalftp import (FtpAuthError, FtpConfig, FtpConnectionError, FtpError,
                        FtpTransferError, connect)

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
try:
  with connect(cfg) as ftp:
    ftp.download("/non-esiste.txt", "x.txt")
except FtpAuthError:
  print("utente o password errati")
except FtpConnectionError as exc:
  print("server non raggiungibile:", exc)
except FtpTransferError as exc:
  print("operazione fallita:", exc, "- codice", exc.code)    # 550
except FtpError as exc:
  print("altro errore:", exc)
```

## Date, fusi orari e MFMT

- **Le date remote sono sempre `datetime` in UTC con fuso** (`tzinfo=timezone.utc`).
  Confrontale con altri `datetime` con fuso (`datetime.now(timezone.utc)`); per mostrarle
  nell'ora locale usa `voce.mtime.astimezone()`. Mescolare datetime con e senza fuso solleva
  `TypeError`: anche `Rules.modified_after` deve avere il fuso.
- **MLSD e MDTM** (RFC 3659) danno la data in UTC con i secondi: sono la fonte preferita.
  `list()` usa `MLSD` quando il server annuncia `MLST` (o `MLSD`) in `FEAT`.
- **LIST (ripiego)**: se il server non annuncia né `MLST` né `MLSD`, la libreria legge l'output di `LIST` (Unix o
  Windows/IIS), che non indica il fuso orario né, spesso, l'anno e i secondi. Il fuso del
  server è assunto UTC, l'anno mancante è dedotto (se la data cadrebbe nel futuro si usa
  l'anno precedente) e la voce porta `approximate=True`. Per i file la libreria prova poi
  `MDTM`: se risponde, la data diventa esatta e `approximate` torna `False`. Se una data
  resta approssimata, la tolleranza della sync sale ad almeno 60 secondi.
- **Server con solo LIST e senza MDTM**: le date di `LIST` sono assunte UTC. Su un server
  con orologio a ovest di UTC (ad esempio UTC-5) le date remote risultano più vecchie del
  vero di quell'offset; una modifica remota fatta entro l'offset e con la stessa dimensione
  può quindi essere saltata come `unchanged` in `"down"`. A est di UTC l'effetto è opposto
  (le date remote sembrano più recenti). Se il server lo permette, usa un server con
  `MLSD` o `MDTM`, oppure `Rules(newer_than_dest=False)` e confronta solo la dimensione.
- **MFMT** (modifica della data di un file remoto): dopo un `upload` la libreria imposta la data
  remota uguale a quella del file locale, se il server annuncia `MFMT` (`ftp.supports("MFMT")`).
  Così una seconda sync vede le date uguali e non ritrasferisce nulla.
- **Download**: se il server risponde a `MDTM`, la data di modifica del file locale diventa
  quella remota (`os.utime`).
- **Server senza MFMT**: dopo l'upload la data remota è quella dell'invio, più recente della
  locale. Un file non modificato ha la stessa dimensione e viene saltato (`unchanged`); un
  file locale modificato dopo l'upload risulta più recente e viene ricaricato. Resta scoperto
  il caso di un file locale con data precedente all'upload ma contenuto diverso (ad esempio
  un backup ripristinato): con dimensione diversa è segnalato `conflict-dest-newer`, con la
  stessa dimensione è saltato. Per forzarlo usa `Rules(overwrite_newer=True)`.

<!-- ex:date -->
```python
from datetime import datetime, timedelta, timezone
from minimalftp import FtpConfig, connect

cfg = FtpConfig(host="ftp.example.com", user="mario", password_env="FTP_PASSWORD")
with connect(cfg) as ftp:
  print("MLST:", ftp.supports("MLST"), "- MFMT:", ftp.supports("MFMT"))
  ieri = datetime.now(timezone.utc) - timedelta(days=1)
  for voce in ftp.list("/"):
    if voce.mtime is not None and voce.mtime > ieri:
      print(voce.name, voce.mtime.astimezone(), "(approssimata)" if voce.approximate else "")
```

## Interfaccia grafica

`minimalftp_gui.py` è una piccola finestra `tkinter`: due pannelli, locale e remoto, per
spostare file e cartelle senza scrivere codice, più una sezione richiudibile per sincronizzare
intere cartelle. Usa la libreria in un thread di lavoro e non tocca mai `ftplib` direttamente.
I testi sono disponibili in inglese e in italiano.

Avvio (con `minimalftp.py` nella stessa cartella):

```sh
python minimalftp_gui.py
```

**Barra della connessione.** Profilo (`Salva` ed `Elimina`, quest'ultimo con conferma), Host,
Porta, Utente, Password (mascherata), TLS (`none`, `explicit`, `implicit`), poi `Connetti` e
`Disconnetti`. I profili stanno in `~/.minimalftp/profiles.json`. **La password non viene mai
salvata**: va reinserita a ogni avvio e a ogni cambio di profilo. Utente vuoto = `anonymous`;
porta vuota = 21; la porta deve essere un numero tra 1 e 65535, altrimenti un messaggio nel
log e non parte nulla. La connessione resta aperta tra un'operazione e l'altra. La lingua
scelta (English o Italiano) si ricorda, insieme a dimensione e posizione della finestra, in
`~/.minimalftp/ui.json`; la finestra torna sempre dentro lo schermo.

**Pannelli.** A sinistra il pannello locale, a destra quello remoto, ciascuno con la barra del
percorso (scrivi un percorso e premi Invio), una riga `..` per salire e le colonne nome,
dimensione e data. Le cartelle vengono per prime. Doppio clic o Invio apre una cartella; si
possono selezionare più righe. Ogni pannello ha il suo pulsante `Nuova cartella`. `F5` ricarica
il pannello attivo. La maniglia tra i pannelli e la parte bassa della finestra regola la loro
altezza.

**Trasferimenti.** `Carica >` invia le righe selezionate nel pannello locale alla cartella
remota mostrata; `< Scarica` porta le righe selezionate nel pannello remoto nella cartella
locale mostrata. File e cartelle si possono mescolare. Non si sovrascrive mai nulla senza un
sì:

- Se un file esiste già a destinazione (i nomi si confrontano senza distinguere maiuscole e
  normalizzazione Unicode), una finestra mostra le due copie affiancate, con dimensione, data e
  ora. La data più recente è evidenziata in ambra e porta la scritta "(piu' recente)", e una
  riga dice quale file è più recente e quale più grande. I pulsanti sono `Sovrascrivi`, `Salta`
  (il predefinito: Invio ed Esc saltano) e `Annulla tutto`, che non invia nulla. Con più
  conflitti la casella "Applica a tutti i conflitti" estende la scelta ai restanti.
- Per una cartella si confrontano prima i due lati. Se alcuni file esistono da entrambe le
  parti e sono diversi, una finestra li elenca in una tabella con dimensioni e date dei due
  lati; `Sovrascrivi tutti`, `Salta esistenti` (il predefinito) o `Annulla`. I file che non
  esistono a destinazione si trasferiscono sempre.
- Un nome che è un file da una parte e una cartella dall'altra viene segnalato nel log e non
  si invia nulla.

**Sezione di sincronizzazione.** Sotto i pannelli, "Sincronizza cartelle" apre e chiude una
sezione che usa la connessione aperta: **Cartella locale** (con `Scegli...`), la **direzione**
(il pulsante centrale alterna `SU >>>`, locale -> server, e `<<< GIU'`, server -> locale),
**Cartella remota** (obbligatoria: per la radice del server scrivi `/`) e i **criteri**:
"solo ultimi N giorni" (`max_age_days`), "dimensione diversa" (`size_differs`), "piu' recente"
(`newer_than_dest`), "sovrascrivi se piu' nuovo" (`overwrite_newer`). `Anteprima` calcola e
mostra il piano senza trasferire nulla (le righe `skip` sono visibili con il motivo; il filtro
sopra l'elenco mostra tutto, solo i trasferimenti o solo conflitti ed errori). `Esegui` avvia
la sync con barra di avanzamento e log; calcola il piano e poi lo riesegue dentro `sync`,
quindi la cartella remota viene letta due volte. `Annulla` ferma la sync al termine del file in
corso.

La finestra non ha campi per `include`/`exclude`, `ca_file`, `verify_cert` o `timeout`: usa i
default (verifica del certificato attiva). Per quelle opzioni usa la libreria.

**Scorciatoie.** `F5` ricarica il pannello attivo, `Invio` apre la cartella selezionata,
`Ctrl+Invio` (`Cmd+Invio` su macOS) esegue la sync, `Esc` annulla il lavoro in corso, `Tab`
passa da un campo all'altro con il focus visibile. Nelle finestre di sovrascrittura `Invio` ed
`Esc` saltano, e la lettera sottolineata di `Sovrascrivi` (`S` in italiano, `O` in inglese)
sovrascrive. Chiudere la finestra durante un lavoro lo annulla e attende fino a circa 5
secondi; chiuderla con una finestra di sovrascrittura aperta equivale a "Annulla tutto".

**Aspetto.** Palette chiara con accento ottanio, ambra per i conflitti e mattone per gli
errori. I contrasti sono verificati dai test (testo almeno 4,5 a 1, bordi dei controlli almeno
3 a 1) e la riga selezionata si vede bene.

**Limiti della finestra.** Niente elimina, niente rinomina, niente trascinamento; un'operazione
alla volta; `Annulla` agisce tra un file e l'altro; gli elenchi di cartelle molto grandi si
caricano in una volta sola.

## Test

I test usano `pytest` e un server FTP/FTPS locale (`pyftpdlib`); sono dipendenze di sviluppo,
non della libreria.

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest
```

I test FTPS richiedono anche il comando `openssl`; senza `openssl` o senza `pyopenssl` quei
test vengono saltati.

## Limiti noti

I principali, in breve (l'elenco completo con impatti e alternative è in
`dev/LIMITI_NOTI.md`, documento di lavoro locale non pubblicato):

- Niente cancellazioni, niente sync bidirezionale, niente modalità attiva, niente SFTP.
- Un `FtpClient` per thread; nessuna riconnessione automatica.
- L'annullamento agisce solo tra un file e l'altro.
- Una connessione persa a metà di un upload può lasciare sul server un file
  `<nome>.minimalftp-part`.
- Server senza `MFMT`: la seconda sync può comportarsi diversamente (vedi
  [Date](#date-fusi-orari-e-mfmt)); nessun file di stato.
- FTPS implicito e riuso della sessione TLS su server che lo impongono non sono provati
  contro server reali; suite di test su Python 3.14, su 3.9.6 provata una sync end to end
  (`up` e `down`).
- Server con solo `LIST` e senza `MDTM` a ovest di UTC: una modifica remota entro l'offset
  con la stessa dimensione può essere saltata (vedi [Date](#date-fusi-orari-e-mfmt)).
- Nomi che differiscono solo per maiuscole o normalizzazione Unicode sono saltati
  (`name-case-conflict`), non sincronizzati.
- I link simbolici locali sono saltati; quelli remoti non sono riconosciuti.
- Interfaccia grafica: i profili si salvano senza la password e non cifrati; niente elimina,
  rinomina o trascinamento.
