# Changelog

## 1.0 - 2026-11-02

First release.

### Library (`minimalftp.py`)

- FTP and FTPS (explicit and implicit TLS, TLS 1.2 minimum, certificate and host name
  verification on by default, `ca_file` for self-signed certificates).
- Upload and download through a temporary file renamed at the end; a download whose size
  differs from the announced one is an error, so a truncated file is never left behind.
- Listings with `MLSD`, falling back to `LIST` plus `MDTM`; dates are UTC datetimes.
- One-way folder sync (`sync`, `plan_sync`, `upload_dir`, `download_dir`) with selection
  criteria (age, size, newer, include and exclude patterns), preview (dry run) and
  cancellation. It never deletes anything and never overwrites a newer destination file
  unless asked to.
- The password never appears in `repr`, logs or error messages; three ways to pass it
  (`password`, `password_env`, `password_provider`).

### Graphical interface (`minimalftp_gui.py`)

- Two panes, local and remote, with path bars, multiple selection, "Upload >", "< Download"
  and "New folder"; the connection stays open between operations.
- Confirmation before overwriting: one window per existing file with both sizes and dates (the
  newer one highlighted; Skip is the default; "Apply to all"), and a table of differences for
  folders. Nothing is overwritten without a yes.
- Collapsible folder sync section with preview, plan list, filter, progress and cancel.
- English and Italian interface; window size, position and language are remembered.
- Light palette with contrast ratios checked by the tests.

### Documentation

- Bilingual README, English first and then Italian.
