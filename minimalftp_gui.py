"""Minimal tkinter GUI for minimalFTP: one window, no external dependencies.

The non-graphical logic (profiles, rules, action descriptions) lives in module-level
functions, testable without a display. Worker threads never touch the widgets:
they talk to the window only through a queue drained by `after`.
"""
import json
import logging
import math
import os
import queue
import re
import socket
import tempfile
import threading
import time
import unicodedata
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, List, Mapping, Optional

from minimalftp import (Action, FtpConfig, FtpConnectionError, FtpError, RemoteEntry, Rules,
                        __version__, connect)

_state_log = logging.getLogger("minimalftp.gui")

# ---------------------------------------------------------------------------
# Palette (see the aesthetic plan)
# ---------------------------------------------------------------------------

# Surfaces and text. Contrast ratios (WCAG) are checked by the tests: text at least 4.5 to 1,
# control borders at least 3 to 1.
NEBBIA = "#E6EBEE"          # window background
GHIACCIO = "#F7F9FA"        # fields, lists, log
GHIACCIO_SCURO = "#EDF1F3"  # alternate list rows
INCHIOSTRO = "#12202A"      # main text
INCHIOSTRO_2 = "#44535D"    # field labels
GRAFITE = "#5B6A74"         # skipped rows, muted text
FERRO = "#7A8892"           # control borders, switched-off rail
FILO = "#B9C3C9"            # decorative separators only
# Dominant accent and state colors
OTTANIO = "#0B6E6D"         # main action, transfers, rail, focus ring
OTTANIO_SCURO = "#095857"   # pressed or hovered primary button
SELEZIONE = "#CDE6E5"       # selected list row
AMBRA = "#7A4F00"           # conflicts and warnings (text)
AMBRA_CHIARO = "#F6E7C3"    # conflict row background
MATTONE = "#9A2424"         # errors (text)
MATTONE_CHIARO = "#F7DAD6"  # error row background

# Stable internal ids of the plan filter, in the order of the combobox values
FILTER_IDS = ("all", "transfers", "conflicts_errors")
FILTER_KEYS = ("filter_all", "filter_transfers", "filter_conflicts_errors")

# Language names shown in the selector: never translated
LANGUAGE_NAMES = ("English", "Italiano")
LANGUAGE_CODES = ("en", "it")

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

STRINGS: "dict[str, dict[str, str]]" = {
  "window_title": {"en": "minimalFTP v" + __version__, "it": "minimalFTP v" + __version__},
  # labels and buttons
  "lbl_profile": {"en": "Profile", "it": "Profilo"},
  "btn_save": {"en": "Save", "it": "Salva"},
  "btn_delete": {"en": "Delete", "it": "Elimina"},
  "lbl_host": {"en": "Host", "it": "Host"},
  "lbl_port": {"en": "Port", "it": "Porta"},
  "lbl_user": {"en": "User", "it": "Utente"},
  "lbl_password": {"en": "Password", "it": "Password"},
  "lbl_tls": {"en": "TLS", "it": "TLS"},
  "lbl_local_dir": {"en": "Local folder", "it": "Cartella locale"},
  "btn_choose": {"en": "Choose...", "it": "Scegli..."},
  "lbl_remote_dir": {"en": "Remote folder", "it": "Cartella remota"},
  "dir_up": {"en": "UP >>>", "it": "SU >>>"},
  "dir_down": {"en": "<<< DOWN", "it": "<<< GIU'"},
  "lbl_criteria": {"en": "Criteria", "it": "Criteri"},
  "chk_age": {"en": "only last", "it": "solo ultimi"},
  "lbl_days": {"en": "days", "it": "giorni"},
  "chk_size": {"en": "different size", "it": "dimensione diversa"},
  "chk_newer": {"en": "newer", "it": "piu' recente"},
  "chk_overwrite": {"en": "overwrite if newer", "it": "sovrascrivi se piu' nuovo"},
  "btn_preview": {"en": "Preview", "it": "Anteprima"},
  "btn_run": {"en": "Run", "it": "Esegui"},
  "btn_cancel": {"en": "Cancel", "it": "Annulla"},
  "lbl_plan": {"en": "Plan", "it": "Piano"},
  "lbl_filter": {"en": "Filter", "it": "Filtro"},
  "filter_all": {"en": "all", "it": "tutto"},
  "filter_transfers": {"en": "transfers only", "it": "solo trasferimenti"},
  "filter_conflicts_errors": {"en": "conflicts and errors", "it": "conflitti e errori"},
  "col_action": {"en": "action", "it": "azione"},
  "col_path": {"en": "path", "it": "percorso"},
  "col_note": {"en": "note", "it": "nota"},
  "col_size": {"en": "size", "it": "dimensione"},
  # first column of the plan rows
  "word_up": {"en": "UP", "it": "SU"},
  "word_down": {"en": "DOWN", "it": "GIU"},
  "word_skip": {"en": "skip", "it": "salta"},
  "word_conflict": {"en": "CONFLICT", "it": "CONFL"},
  "word_err": {"en": "ERR", "it": "ERR"},
  "word_dir": {"en": "dir", "it": "dir"},
  # notes of the plan rows, one per reason code
  "note_missing": {"en": "new", "it": "nuovo"},
  "note_newer": {"en": "newer", "it": "piu' recente"},
  "note_size_differs": {"en": "different size", "it": "dimensione diversa"},
  "note_unchanged": {"en": "unchanged", "it": "invariato"},
  "note_too_old": {"en": "too old", "it": "troppo vecchio"},
  "note_excluded": {"en": "excluded", "it": "escluso"},
  "note_not_included": {"en": "not included", "it": "non incluso"},
  "note_age_unknown": {"en": "unknown date", "it": "data sconosciuta"},
  "note_conflict_dest_newer": {"en": "destination is newer", "it": "destinazione piu' recente"},
  "note_name_case_conflict": {"en": "name differs only by case",
                              "it": "nome diverso solo per maiuscole"},
  "note_unreadable": {"en": "unreadable folder", "it": "cartella illeggibile"},
  # validation messages
  "err_days_invalid": {
    "en": "the number of days is not valid: use a number, for example 7",
    "it": "il numero di giorni non e valido: usare un numero, ad esempio 7"},
  "err_days_negative": {"en": "the number of days cannot be negative",
                        "it": "il numero di giorni non puo essere negativo"},
  "err_host_required": {"en": "Enter the host.", "it": "Indicare l'host."},
  "err_local_required": {"en": "Enter the local folder.",
                         "it": "Indicare la cartella locale."},
  "err_remote_required": {
    "en": "Enter the remote folder (write / for the server root).",
    "it": "Indicare la cartella remota (per la radice del server scrivere /)."},
  "err_port_range": {"en": "The port must be a number between 1 and 65535.",
                     "it": "La porta deve essere un numero tra 1 e 65535."},
  # log, dialogs
  "error_prefix": {"en": "Error: ", "it": "Errore: "},
  "log_unexpected": {"en": "unexpected error: {name}", "it": "errore inatteso: {name}"},
  "log_internal_error": {"en": "Internal error: {name}", "it": "Errore interno: {name}"},
  "log_profile_name_required": {
    "en": "Write a name for the profile before saving.",
    "it": "Scrivere un nome per il profilo prima di salvare."},
  "log_profile_not_saved": {"en": "Profile not saved: {error}",
                            "it": "Profilo non salvato: {error}"},
  "log_profile_saved": {"en": "Profile saved: {name} (the password is not saved).",
                        "it": "Profilo salvato: {name} (la password non viene salvata)."},
  "log_profile_none": {"en": "No profile with this name.",
                       "it": "Nessun profilo con questo nome."},
  "dlg_delete_profile_title": {"en": "Delete profile", "it": "Elimina profilo"},
  "dlg_delete_profile_msg": {"en": "Delete the profile '{name}'?",
                             "it": "Eliminare il profilo '{name}'?"},
  "log_profiles_not_updated": {"en": "Profiles not updated on disk: {error}",
                               "it": "Profili non aggiornati su disco: {error}"},
  "log_profile_deleted": {"en": "Profile deleted: {name}.", "it": "Profilo eliminato: {name}."},
  "dlg_choose_local_title": {"en": "Local folder", "it": "Cartella locale"},
  "log_preview_running": {"en": "Preview in progress...", "it": "Anteprima in corso..."},
  "log_run_running": {"en": "Run in progress...", "it": "Esecuzione in corso..."},
  "log_cancel_requested": {
    "en": "Cancel requested: it stops when the current file finishes.",
    "it": "Annullamento richiesto: si ferma al termine del file in corso."},
  "log_preview_done": {
    "en": "Preview: {moves} to transfer, {skipped} skipped, {conflicts} conflicts.",
    "it": "Anteprima: {moves} da trasferire, {skipped} saltati, {conflicts} conflitti."},
  "log_item_error": {"en": "ERR {rel}: {text}", "it": "ERR {rel}: {text}"},
  "log_cancelled": {"en": "Cancelled: {count} files transferred before the stop.",
                    "it": "Annullato: {count} file trasferiti prima dello stop."},
  "log_done": {"en": "Done: {transferred} transferred, {skipped} skipped, {errors} errors.",
               "it": "Finito: {transferred} trasferiti, {skipped} saltati, {errors} errori."},
  # pane logic: dates, sizes and conflict verdicts
  "when_unknown": {"en": "unknown date", "it": "data ignota"},
  "size_with_human": {"en": "{grouped} bytes ({human})", "it": "{grouped} byte ({human})"},
  "verdict_local_newer": {"en": "The local file is newer.", "it": "Il file locale e' piu' recente."},
  "verdict_remote_newer": {"en": "The remote file is newer.",
                           "it": "Il file remoto e' piu' recente."},
  "verdict_same_date": {"en": "The dates are the same.", "it": "Le date coincidono."},
  "verdict_unknown_date": {"en": "The date of one file is unknown.",
                           "it": "La data di uno dei file e' ignota."},
  "verdict_same_size": {"en": "The sizes are the same.", "it": "Le dimensioni coincidono."},
  "verdict_local_larger": {"en": "The local file is larger by {diff}.",
                           "it": "Il file locale e' piu' grande di {diff}."},
  "verdict_remote_larger": {"en": "The remote file is larger by {diff}.",
                            "it": "Il file remoto e' piu' grande di {diff}."},
  "err_type_mismatch": {
    "en": "'{name}' is a file on one side and a folder on the other.",
    "it": "'{name}' e' un file da una parte e una cartella dall'altra."},
  # panes and connection bar
  "lbl_local_pane": {"en": "Local", "it": "Locale"},
  "lbl_remote_pane": {"en": "Remote", "it": "Remoto"},
  "col_name": {"en": "name", "it": "nome"},
  "col_date": {"en": "date", "it": "data"},
  "btn_connect": {"en": "Connect", "it": "Connetti"},
  "btn_disconnect": {"en": "Disconnect", "it": "Disconnetti"},
  "btn_upload": {"en": "Upload >", "it": "Carica >"},
  "btn_download": {"en": "< Download", "it": "< Scarica"},
  "btn_mkdir": {"en": "New folder", "it": "Nuova cartella"},
  "lbl_sync_section": {"en": "Sync folders", "it": "Sincronizza cartelle"},
  "status_not_connected": {"en": "Not connected", "it": "Non connesso"},
  "status_connecting": {"en": "Connecting...", "it": "Connessione in corso..."},
  "status_connected": {"en": "Connected to {host}", "it": "Connesso a {host}"},
  "dlg_new_folder_title": {"en": "New folder", "it": "Nuova cartella"},
  "dlg_new_folder_prompt": {"en": "Name of the new folder:",
                            "it": "Nome della nuova cartella:"},
  "err_folder_name": {
    "en": "Not a valid folder name: it cannot be empty, '.' or '..', or contain / or \\.",
    "it": "Nome di cartella non valido: non puo' essere vuoto, '.' o '..', ne contenere / o \\."},
  "log_connected": {"en": "Connected to {host}.", "it": "Connesso a {host}."},
  "log_disconnected": {"en": "Disconnected: {reason}", "it": "Disconnesso: {reason}"},
  "log_pane_error": {"en": "Cannot read {path}: {error}",
                     "it": "Impossibile leggere {path}: {error}"},
  "log_folder_created": {"en": "Folder created: {name}", "it": "Cartella creata: {name}"},
  "log_folder_failed": {"en": "Folder not created: {error}",
                        "it": "Cartella non creata: {error}"},
  "log_transfer_running": {"en": "Transfer in progress...", "it": "Trasferimento in corso..."},
  "log_transfer_done": {
    "en": "Transfer done: {transferred} transferred, {skipped} skipped (already there), {errors} errors.",
    "it": "Trasferimento finito: {transferred} trasferiti, {skipped} saltati (gia' presenti), {errors} errori."},
  "log_transfer_cancelled": {
    "en": "Transfer cancelled: {transferred} transferred before the stop.",
    "it": "Trasferimento annullato: {transferred} trasferiti prima dello stop."},
  "log_transfer_declined": {"en": "Transfer cancelled: nothing was sent.",
                            "it": "Trasferimento annullato: non e' stato trasferito nulla."},
  # overwrite confirmation windows
  "dlg_conflict_title": {"en": "File already exists", "it": "Il file esiste gia'"},
  "dlg_conflict_counter": {"en": "Conflict {index} of {total}",
                           "it": "Conflitto {index} di {total}"},
  "dlg_conflict_newer": {"en": "(newer)", "it": "(piu' recente)"},
  "lbl_size_row": {"en": "Size", "it": "Dimensione"},
  "lbl_date_row": {"en": "Date", "it": "Data"},
  "btn_overwrite": {"en": "Overwrite", "it": "Sovrascrivi"},
  "btn_skip": {"en": "Skip", "it": "Salta"},
  "btn_cancel_all": {"en": "Cancel all", "it": "Annulla tutto"},
  "chk_apply_all": {"en": "Apply to all conflicts", "it": "Applica a tutti i conflitti"},
  "key_overwrite": {"en": "o", "it": "s"},
  "dlg_dir_title": {"en": "Folders with differences", "it": "Cartelle con differenze"},
  "dlg_dir_intro": {
    "en": "These files exist on both sides and differ ({local} and {remote}). "
          "Overwriting replaces the copies at the destination, even when they are newer.",
    "it": "Questi file esistono da entrambe le parti e sono diversi ({local} e {remote}). "
          "Sovrascrivendo si sostituiscono le copie di destinazione, anche se piu' recenti."},
  "dlg_dir_col_file": {"en": "file", "it": "file"},
  "dlg_dir_col_local_size": {"en": "local size", "it": "dim. locale"},
  "dlg_dir_col_local_date": {"en": "local date", "it": "data locale"},
  "dlg_dir_col_remote_size": {"en": "remote size", "it": "dim. remota"},
  "dlg_dir_col_remote_date": {"en": "remote date", "it": "data remota"},
  "btn_overwrite_all": {"en": "Overwrite all", "it": "Sovrascrivi tutti"},
  "btn_skip_existing": {"en": "Skip existing", "it": "Salta esistenti"},
  # connection worker messages
  "busy": {"en": "An operation is already in progress.",
           "it": "Un'operazione e' gia' in corso."},
  "not_connected": {"en": "Not connected to a server.", "it": "Non connesso a un server."},
  "unknown_command": {"en": "Unknown command: {cmd}.", "it": "Comando sconosciuto: {cmd}."},
  "unexpected_error": {"en": "Unexpected error: {name}.", "it": "Errore imprevisto: {name}."},
  "connection_closed": {"en": "The connection was closed.",
                        "it": "La connessione e' stata chiusa."},
  "reconnecting": {"en": "Reconnecting.", "it": "Riconnessione in corso."},
  "disconnected_by_user": {"en": "Disconnected.", "it": "Disconnesso."},
}

_LANGS = ("en", "it")


def detect_language(env: "Mapping[str, str]" = os.environ) -> str:
  """"it" if LC_ALL, LC_MESSAGES or LANG (first one set) starts with "it", else "en"."""
  for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
    value = (env.get(name) or "").strip().lower()
    if value:
      return "it" if value.startswith("it") else "en"
  return "en"


class Translator:
  """Active language, text lookup and change notifications."""

  def __init__(self, lang: str = "en"):
    self.lang = "en"
    self._subscribers: "List[Callable[[], None]]" = []
    self.set_language(lang)

  def tr(self, key: str, **kw) -> str:
    """Text of `key` in the active language; unknown key -> the key itself, never raises."""
    texts = STRINGS.get(key)
    if texts is None:
      return key
    text = texts.get(self.lang) or texts.get("en") or key
    if kw:
      try:
        return text.format(**kw)
      except (KeyError, IndexError, ValueError):
        return text
    return text

  def set_language(self, lang: str) -> None:
    """Switches language ("en" or "it") and notifies the subscribers."""
    if lang not in _LANGS:
      raise ValueError("unsupported language: %r" % (lang,))
    self.lang = lang
    for callback in list(self._subscribers):
      callback()

  def subscribe(self, callback: "Callable[[], None]") -> None:
    """Registers a callback called after every language change."""
    if callback not in self._subscribers:
      self._subscribers.append(callback)

  def unsubscribe(self, callback: "Callable[[], None]") -> None:
    """Removes a callback; unknown callbacks are ignored."""
    if callback in self._subscribers:
      self._subscribers.remove(callback)


translator = Translator(detect_language())
tr = translator.tr

# ---------------------------------------------------------------------------
# Non-graphical logic
# ---------------------------------------------------------------------------


def default_profiles_path() -> Path:
  """Default path of the profiles: ~/.minimalftp/profiles.json."""
  return Path.home() / ".minimalftp" / "profiles.json"


def load_profiles(path: Path) -> dict:
  """Reads the profiles; missing or corrupt file -> empty dict, never exceptions."""
  try:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  if not isinstance(data, dict):
    return {}
  clean: dict = {}
  for name, profile in data.items():
    if isinstance(profile, dict):
      clean[str(name)] = {k: v for k, v in profile.items() if k != "password"}
  return clean


def save_profiles(path: Path, profiles: dict) -> None:
  """Writes the profiles as JSON without passwords, atomically (temp + replace)."""
  safe = {
    name: {k: v for k, v in profile.items() if k != "password"}
    for name, profile in profiles.items()
  }
  _write_json_atomic(Path(path), safe, ".profiles-")


def _write_json_atomic(path: Path, data: dict, prefix: str) -> None:
  """Writes `data` as JSON to a temp file in the same directory, then replaces `path`."""
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".tmp")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
      json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp_name, str(path))
  except BaseException:
    try:
      os.unlink(tmp_name)
    except OSError:
      pass
    raise


# -- remembered window state -------------------------------------------------

DEFAULT_WINDOW_SIZE = (960, 720)
MIN_WINDOW_SIZE = (760, 560)
# what the window actually allows (root.minsize); clamp_geometry uses it in the App
APP_MIN_SIZE = (760, 640)
# at least this many pixels of the window must stay on screen, horizontally and from the top
MIN_VISIBLE_PX = 120
SASH_RANGE = (0.15, 0.9)


def ui_state_path() -> Path:
  """Default path of the window state: ~/.minimalftp/ui.json."""
  return Path.home() / ".minimalftp" / "ui.json"


def load_ui_state(path: Path) -> dict:
  """Reads the window state; missing or corrupt file -> empty dict, never exceptions.

  Unknown keys are kept, so a newer version does not lose what an older one wrote.
  """
  try:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  return data if isinstance(data, dict) else {}


def save_ui_state(path: Path, state: dict) -> None:
  """Writes the window state atomically (temp + replace), creating the directory."""
  _write_json_atomic(Path(path), state, ".ui-")


def _number(value) -> "Optional[float]":
  """`value` if it is a finite int or float (not a bool), else None."""
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return None
  return float(value) if math.isfinite(value) else None


def clamp_geometry(state: dict, screen: "tuple[int, int, int, int]",
                   min_size: "tuple[int, int]" = MIN_WINDOW_SIZE) -> dict:
  """Window size and position from the saved `state`, brought inside the screen.

  `screen` is the virtual screen (x, y, width, height); x and y may be negative on a
  secondary monitor. The size ends up between `min_size` and the screen size (default
  960 x 720 if missing). The position is moved so that at least MIN_VISIBLE_PX of the
  width and of the top part stay visible; if x or y is missing both are None (the
  system places the window).
  """
  sx, sy, sw, sh = screen
  width = _number(state.get("width"))
  height = _number(state.get("height"))
  width = int(width if width is not None else DEFAULT_WINDOW_SIZE[0])
  height = int(height if height is not None else DEFAULT_WINDOW_SIZE[1])
  width = max(min_size[0], min(width, sw))
  height = max(min_size[1], min(height, sh))
  x, y = _number(state.get("x")), _number(state.get("y"))
  if x is None or y is None:
    return {"width": width, "height": height, "x": None, "y": None}
  px = int(max(sx - width + MIN_VISIBLE_PX, min(x, sx + sw - MIN_VISIBLE_PX)))
  py = int(max(sy, min(y, sy + sh - MIN_VISIBLE_PX)))
  return {"width": width, "height": height, "x": px, "y": py}


def clamp_sash(value, lo: float = SASH_RANGE[0], hi: float = SASH_RANGE[1]) -> float:
  """Sash position as a fraction of the split height, kept within [lo, hi]."""
  number = _number(value)
  if number is None:
    return lo
  return max(lo, min(number, hi))


def parse_geometry(text: str) -> "Optional[tuple[int, int, int, int]]":
  """(width, height, x, y) from a Tk geometry string such as `960x720+10+-5`."""
  m = re.fullmatch(r"(\d+)x(\d+)([+-])(-?\d+)([+-])(-?\d+)", text)
  if not m:
    return None
  w, h, sx, x, sy, y = m.groups()
  return int(w), int(h), int(x) * (-1 if sx == "-" else 1), int(y) * (-1 if sy == "-" else 1)


def build_rules(max_age_days_text: str, size_differs: bool, newer: bool,
                overwrite_newer: bool = False) -> Rules:
  """Builds the rules from the fields; empty text = no age limit."""
  text = (max_age_days_text or "").strip()
  max_age: Optional[float] = None
  if text:
    try:
      max_age = float(text.replace(",", "."))
    except ValueError:
      raise ValueError(tr("err_days_invalid"))
    if max_age != max_age or max_age in (float("inf"), float("-inf")):
      raise ValueError(tr("err_days_invalid"))
    if max_age < 0:
      raise ValueError(tr("err_days_negative"))
    if max_age == int(max_age):
      max_age = int(max_age)
  return Rules(max_age_days=max_age, size_differs=size_differs,
               newer_than_dest=newer, overwrite_newer=overwrite_newer)


def _parse_port(port_text: str) -> int:
  """Port from the field text (empty = 21); ValueError with a message for the log."""
  text = (port_text or "").strip() or "21"
  try:
    port = int(text)
  except ValueError:
    raise ValueError(tr("err_port_range")) from None
  if not 1 <= port <= 65535:
    raise ValueError(tr("err_port_range"))
  return port


def validate_connection(host: str, port_text: str) -> "tuple[str, int]":
  """Checks the host and port fields for Connect; (host, port)."""
  host = (host or "").strip()
  if not host:
    raise ValueError(tr("err_host_required"))
  return host, _parse_port(port_text)


def validate_folder_name(name: str) -> str:
  """Name for a new folder, stripped; ValueError for the names a sync never transfers."""
  name = (name or "").strip()
  if not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\0")):
    raise ValueError(tr("err_folder_name"))
  return name


def validate_fields(host: str, port_text: str, local: str,
                    remote: str) -> "tuple[str, int, str, str]":
  """Checks the connection and path fields; (host, port, local, remote).

  An empty port means 21; the remote directory is required (for the server root
  write "/"). Errors: ValueError with a message for the log.
  """
  host = (host or "").strip()
  local = (local or "").strip()
  remote = (remote or "").strip()
  if not host:
    raise ValueError(tr("err_host_required"))
  if not local:
    raise ValueError(tr("err_local_required"))
  if not remote:
    raise ValueError(tr("err_remote_required"))
  return host, _parse_port(port_text), local, remote


_NOTE_KEYS = {
  "missing": "note_missing",
  "newer": "note_newer",
  "size-differs": "note_size_differs",
  "unchanged": "note_unchanged",
  "too-old": "note_too_old",
  "excluded": "note_excluded",
  "not-included": "note_not_included",
  "age-unknown": "note_age_unknown",
  "conflict-dest-newer": "note_conflict_dest_newer",
  "name-case-conflict": "note_name_case_conflict",
  "unreadable": "note_unreadable",
}


def describe_action(action: Action) -> "tuple[str, str, str]":
  """(word, relative path, note) for a row of the plan list, in the active language."""
  key = _NOTE_KEYS.get(action.reason)
  note = tr(key) if key else action.reason
  if action.kind == "upload":
    word = tr("word_up")
  elif action.kind == "download":
    word = tr("word_down")
  elif action.kind == "mkdir":
    word = tr("word_dir")
  elif action.reason == "conflict-dest-newer":
    word = tr("word_conflict")
  else:
    word = tr("word_skip")
  return word, action.rel_path, note


def action_tag(action: Action) -> str:
  """Color tag of the row: transfer, skip, conflict or dir."""
  if action.kind in ("upload", "download"):
    return "transfer"
  if action.kind == "mkdir":
    return "dir"
  if action.reason == "conflict-dest-newer":
    return "conflict"
  return "skip"


def contrast_ratio(fg: str, bg: str) -> float:
  """WCAG contrast ratio (1 to 21) between two `#RRGGBB` colors."""

  def luminance(color: str) -> float:
    channels = []
    for i in (1, 3, 5):
      c = int(color[i:i + 2], 16) / 255
      channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

  lighter, darker = sorted((luminance(fg), luminance(bg)), reverse=True)
  return (lighter + 0.05) / (darker + 0.05)


def rail_colors(direction: str) -> "tuple[str, str]":
  """Colors (left, right) of the route line: SU lights the left half."""
  return (OTTANIO, FERRO) if direction == "up" else (FERRO, OTTANIO)


def format_size(size: int) -> str:
  """Human-readable size (B, KB, MB, GB)."""
  value = float(size)
  for unit in ("B", "KB", "MB"):
    if value < 1024:
      return ("%d %s" % (value, unit)) if unit == "B" else ("%.1f %s" % (value, unit))
    value /= 1024
  return "%.1f GB" % value


# ---------------------------------------------------------------------------
# Pane logic (no window)
# ---------------------------------------------------------------------------

# Height of a list row in pixels, rows requested for a pane and rows a pane never goes below
ROW_HEIGHT = 22
PANE_ROWS = 14
PANE_MIN_ROWS = 5

# Dates closer than this many seconds are considered the same
SAME_DATE_SECONDS = 2


def _fold(name: str) -> str:
  """Comparison key of a file name: NFC-normalized and case-folded."""
  return unicodedata.normalize("NFC", name).casefold()


def sort_entries(entries: "List[RemoteEntry]") -> "List[RemoteEntry]":
  """Folders first, then files, each group in case-insensitive name order."""
  return sorted(entries, key=lambda e: (not e.is_dir, _fold(e.name)))


def list_local(path: str) -> "List[RemoteEntry]":
  """Entries of a local folder, sorted; raises OSError if it cannot be read.

  Symbolic links are listed as ordinary files or folders; a broken link is a
  file with size 0 and unknown date. Dates are aware UTC datetimes.
  """
  entries = []
  with os.scandir(path) as it:
    for item in it:
      try:
        is_dir = item.is_dir()
        info = item.stat()
        size = 0 if is_dir else info.st_size
        mtime = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)
      except (OSError, OverflowError, ValueError):
        entries.append(RemoteEntry(item.name, False, 0, None))
        continue
      entries.append(RemoteEntry(item.name, is_dir, size, mtime))
  return sort_entries(entries)


def join_remote(base: str, name: str) -> str:
  """Joins a remote folder and a name with "/" (backslashes are normalized)."""
  base = base.replace("\\", "/")
  if base.endswith("/"):
    return base + name
  return base + "/" + name


def parent_remote(path: str) -> str:
  """Parent of a remote path; the root "/" is its own parent."""
  path = path.replace("\\", "/")
  trimmed = path.rstrip("/")
  if not trimmed:
    return "/"
  head = trimmed.rpartition("/")[0]
  if head:
    return head
  return "/" if trimmed.startswith("/") else ""


def parent_local(path: str) -> str:
  """Parent of a local path; a root is its own parent."""
  stripped = path.rstrip("/\\" if os.sep == "\\" else "/")
  if not stripped:
    return path
  # A drive root such as "C:\" strips to "C:", which has no parent: keep the root itself
  if not os.path.splitdrive(stripped)[1]:
    return path
  return os.path.dirname(stripped) or stripped


def pane_row_values(entry: RemoteEntry) -> "tuple[str, str, str]":
  """(name, size, date) texts of a pane row; folders get a trailing slash and no size."""
  if entry.name == "..":
    return "..", "", ""
  name = entry.name + "/" if entry.is_dir else entry.name
  size = "" if entry.is_dir else format_size(entry.size)
  when = describe_when(entry.mtime) if entry.mtime is not None else ""
  return name, size, when


def describe_when(dt: "Optional[datetime]") -> str:
  """Local time as YYYY-MM-DD HH:MM, or the "unknown date" text."""
  if dt is None:
    return tr("when_unknown")
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone().strftime("%Y-%m-%d %H:%M")


@dataclass
class ConflictRow:
  """A file that exists on both sides, with the comparison of its two copies."""

  name: str
  local: RemoteEntry
  remote: RemoteEntry
  newer: str  # "local", "remote", "same" or "unknown"
  same_size: bool


def build_conflict(name: str, local: RemoteEntry, remote: RemoteEntry) -> ConflictRow:
  """Compares the two copies of a file by date and size."""
  if local.mtime is None or remote.mtime is None:
    newer = "unknown"
  else:
    delta = (local.mtime - remote.mtime).total_seconds()
    if abs(delta) <= SAME_DATE_SECONDS:
      newer = "same"
    else:
      newer = "local" if delta > 0 else "remote"
  return ConflictRow(name, local, remote, newer, local.size == remote.size)


def _size_text(size: int) -> str:
  """Bytes with a grouping separator plus the human-readable form."""
  grouped = "{:,}".format(size)
  if translator.lang == "it":
    grouped = grouped.replace(",", " ")
  return tr("size_with_human", grouped=grouped, human=format_size(size))


def conflict_texts(row: ConflictRow) -> "dict[str, str]":
  """Texts of the conflict dialog, in the active language."""
  verdict = [tr({
    "local": "verdict_local_newer",
    "remote": "verdict_remote_newer",
    "same": "verdict_same_date",
  }.get(row.newer, "verdict_unknown_date"))]
  if row.same_size:
    verdict.append(tr("verdict_same_size"))
  elif row.local.size > row.remote.size:
    verdict.append(tr("verdict_local_larger", diff=format_size(row.local.size - row.remote.size)))
  else:
    verdict.append(tr("verdict_remote_larger", diff=format_size(row.remote.size - row.local.size)))
  return {
    "local_size": _size_text(row.local.size),
    "remote_size": _size_text(row.remote.size),
    "local_when": describe_when(row.local.mtime),
    "remote_when": describe_when(row.remote.mtime),
    "verdict": " ".join(verdict),
  }


def find_conflicts(selected: "List[RemoteEntry]", dest_entries: "List[RemoteEntry]",
                   selected_side: str) -> "List[ConflictRow]":
  """Selected files that already exist at the destination (names compared NFC + casefold).

  `selected_side` is "local" or "remote". Folders and the ".." row give no file
  conflict; a file against a same-named folder (or the reverse) raises ValueError.
  """
  dest_by_name = {_fold(e.name): e for e in dest_entries if e.name != ".."}
  rows = []
  for entry in selected:
    if entry.name == "..":
      continue
    other = dest_by_name.get(_fold(entry.name))
    if other is None:
      continue
    if entry.is_dir != other.is_dir:
      raise ValueError(tr("err_type_mismatch", name=entry.name))
    if entry.is_dir:
      continue
    if selected_side == "local":
      rows.append(build_conflict(entry.name, entry, other))
    else:
      rows.append(build_conflict(entry.name, other, entry))
  return rows


class ConflictResolver:
  """Asks what to do for each conflict, honoring "apply to all" and cancel."""

  def __init__(self, ask: "Callable[[ConflictRow, int], tuple]"):
    self.ask = ask

  def resolve(self, rows: "List[ConflictRow]") -> "tuple[set, set, bool]":
    """(names to overwrite, names to skip, cancelled)."""
    overwrite: "set[str]" = set()
    skip: "set[str]" = set()
    sticky = None
    for index, row in enumerate(rows):
      if sticky is None:
        decision, apply_all = self.ask(row, len(rows) - index)
        if decision == "cancel":
          return overwrite, skip, True
        if apply_all:
          sticky = decision
      else:
        decision = sticky
      (overwrite if decision == "overwrite" else skip).add(row.name)
    return overwrite, skip, False


# ---------------------------------------------------------------------------
# Overwrite confirmation windows
# ---------------------------------------------------------------------------


class _ConfirmWindow(tk.Toplevel):
  """Base of the modal confirmation windows: Esc keeps the default answer, X cancels."""

  default_result: object = None

  def __init__(self, parent, title: str):
    super().__init__(parent)
    self.result = self.default_result
    self.title(title)
    self.configure(background=NEBBIA)
    self.resizable(False, False)
    try:
      self.transient(parent)
    except tk.TclError:
      pass
    style = ttk.Style(self)
    style.configure("Newer.TLabel", background=AMBRA_CHIARO, foreground=AMBRA)
    self.bind("<Escape>", self._on_escape)
    self.protocol("WM_DELETE_WINDOW", self.cancel)

  def _finish(self, result) -> None:
    self.result = result
    self.destroy()

  def _on_escape(self, _event=None) -> str:
    self._finish(self.default_result)
    return "break"

  def cancel(self) -> None:
    raise NotImplementedError

  def _center_on(self, parent) -> None:
    self.update_idletasks()
    try:
      x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
      y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
      self.geometry("+%d+%d" % (max(0, x), max(0, y)))
    except tk.TclError:
      pass

  def show(self):
    """Waits for the answer (modal) and returns `result`."""
    try:
      self.wait_visibility()
      self.grab_set()
    except tk.TclError:
      pass
    self.wait_window(self)
    return self.result


class ConflictDialog(_ConfirmWindow):
  """One file that exists on both sides: sizes and dates side by side.

  `result` is (decision, apply_to_all) with decision "overwrite", "skip" or "cancel";
  the default is ("skip", False). Enter and Esc skip, the underlined letter overwrites.
  """

  default_result = ("skip", False)

  def __init__(self, parent, row: ConflictRow, index: int, total: int):
    super().__init__(parent, tr("dlg_conflict_title"))
    texts = conflict_texts(row)
    self.cells: "dict[str, ttk.Label]" = {}
    body = ttk.Frame(self, padding=(16, 14, 16, 8))
    body.pack(fill="both", expand=True)
    body.columnconfigure(1, weight=1)
    body.columnconfigure(2, weight=1)
    line = 0
    if total > 1:
      ttk.Label(body, text=tr("dlg_conflict_counter", index=index, total=total),
                style="Cap.TLabel").grid(row=line, column=0, columnspan=3, sticky="w")
      line += 1
    self.cells["name"] = ttk.Label(body, text=row.name, font=("TkDefaultFont", 12, "bold"))
    self.cells["name"].grid(row=line, column=0, columnspan=3, sticky="w", pady=(0, 8))
    ttk.Label(body, text=tr("lbl_local_pane"), style="Cap.TLabel", padding=(4, 0)).grid(
      row=line + 1, column=1, sticky="w", padx=(0, 16))
    ttk.Label(body, text=tr("lbl_remote_pane"), style="Cap.TLabel", padding=(4, 0)).grid(
      row=line + 1, column=2, sticky="w", padx=(0, 16))
    for offset, (label_key, key) in enumerate((("lbl_size_row", "size"), ("lbl_date_row", "when"))):
      grid_row = line + 2 + offset
      ttk.Label(body, text=tr(label_key), style="Cap.TLabel").grid(
        row=grid_row, column=0, sticky="w", padx=(0, 12), pady=2)
      for column, side in ((1, "local"), (2, "remote")):
        text = texts[side + "_" + key]
        style = ""
        if key == "when" and row.newer == side:
          text += " " + tr("dlg_conflict_newer")
          style = "Newer.TLabel"
        cell = ttk.Label(body, text=text, padding=(4, 1))
        if style:
          cell.configure(style=style)
        cell.grid(row=grid_row, column=column, sticky="w", padx=(0, 16), pady=2)
        self.cells[side + "_" + key] = cell
    self.cells["verdict"] = ttk.Label(body, text=texts["verdict"])
    self.cells["verdict"].grid(row=line + 4, column=0, columnspan=3, sticky="w", pady=(8, 0))
    self.var_all = tk.BooleanVar(value=False)
    self.chk_all = None
    if total > 1:
      self.chk_all = ttk.Checkbutton(body, text=tr("chk_apply_all"), variable=self.var_all)
      self.chk_all.grid(row=line + 5, column=0, columnspan=3, sticky="w", pady=(8, 0))
    bar = ttk.Frame(self, padding=(16, 4, 16, 14))
    bar.pack(fill="x")
    key = tr("key_overwrite")
    self.btn_overwrite = ttk.Button(bar, text=tr("btn_overwrite"), underline=0,
                                    command=lambda: self._choose("overwrite"))
    self.btn_skip = ttk.Button(bar, text=tr("btn_skip"), style="Primary.TButton",
                               command=lambda: self._choose("skip"))
    self.btn_cancel = ttk.Button(bar, text=tr("btn_cancel_all"), command=self.cancel)
    for button in (self.btn_overwrite, self.btn_skip, self.btn_cancel):
      button.pack(side="left", padx=(0, 8))
    self.bind("<Return>", lambda _e: self._choose("skip"))
    self.bind("<Key-%s>" % key.lower(), self._on_overwrite_key)
    self.bind("<Key-%s>" % key.upper(), self._on_overwrite_key)
    self.btn_skip.focus_set()
    self._center_on(parent)

  def _choose(self, decision: str) -> None:
    self._finish((decision, bool(self.var_all.get()) and decision != "cancel"))

  def _on_overwrite_key(self, _event=None) -> str:
    self._choose("overwrite")
    return "break"

  def cancel(self) -> None:
    self._finish(("cancel", False))


class DirConflictDialog(_ConfirmWindow):
  """Files of a folder transfer that exist on both sides and differ.

  `rows` (optional) maps the relative path of an action to its ConflictRow, to show both
  sizes and dates. `result` is "overwrite", "skip" (default) or "cancel".
  """

  default_result = "skip"

  def __init__(self, parent, actions: "List[Action]", local_dir: str, remote_dir: str,
               rows: "Optional[dict]" = None):
    super().__init__(parent, tr("dlg_dir_title"))
    rows = rows or {}
    body = ttk.Frame(self, padding=(16, 14, 16, 8))
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=tr("dlg_dir_intro", local=local_dir, remote=remote_dir),
              wraplength=640, justify="left").pack(anchor="w", pady=(0, 8))
    frame = ttk.Frame(body)
    frame.pack(fill="both", expand=True)
    columns = (("file", "dlg_dir_col_file", 240, "w"),
               ("lsize", "dlg_dir_col_local_size", 90, "e"),
               ("ldate", "dlg_dir_col_local_date", 150, "w"),
               ("rsize", "dlg_dir_col_remote_size", 90, "e"),
               ("rdate", "dlg_dir_col_remote_date", 150, "w"))
    self.table = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                              height=min(max(len(actions), 3), 10), selectmode="none")
    for ident, key, width, anchor in columns:
      self.table.heading(ident, text=tr(key))
      self.table.column(ident, width=width, anchor=anchor, stretch=ident == "file")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
    self.table.configure(yscrollcommand=scroll.set)
    self.table.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    for action in actions:
      row = rows.get(action.rel_path)
      if row is None:
        values = (action.rel_path, "", "", "", "")
      else:
        values = (action.rel_path, format_size(row.local.size), describe_when(row.local.mtime),
                  format_size(row.remote.size), describe_when(row.remote.mtime))
      self.table.insert("", "end", values=values)
    bar = ttk.Frame(self, padding=(16, 4, 16, 14))
    bar.pack(fill="x")
    self.btn_overwrite = ttk.Button(bar, text=tr("btn_overwrite_all"),
                                    command=lambda: self._finish("overwrite"))
    self.btn_skip = ttk.Button(bar, text=tr("btn_skip_existing"), style="Primary.TButton",
                               command=lambda: self._finish("skip"))
    self.btn_cancel = ttk.Button(bar, text=tr("btn_cancel"), command=self.cancel)
    for button in (self.btn_overwrite, self.btn_skip, self.btn_cancel):
      button.pack(side="left", padx=(0, 8))
    self.bind("<Return>", lambda _e: self._finish("skip"))
    self.btn_skip.focus_set()
    self._center_on(parent)

  def cancel(self) -> None:
    self._finish("cancel")


# ---------------------------------------------------------------------------
# Connection worker
# ---------------------------------------------------------------------------

# Reasons of plan actions that are real conflicts; a plain "newer" is a normal transfer
_DIR_CONFLICT_REASONS = ("conflict-dest-newer", "size-differs")
_PROGRESS_INTERVAL_S = 0.1
_STOP = object()


class _Aborted(Exception):
  """Raised inside a progress callback to interrupt the current file while stopping."""


class _ProgressThrottle:
  """Turns per-block callbacks into at most one message per interval per file.

  The last update of a file is always delivered: either as it arrives (when done reaches
  total) or by `finish()` when the file ends.
  """

  def __init__(self, emit: "Callable[[tuple], None]"):
    self._emit = emit
    self._name: "Optional[str]" = None
    self._last_sent = 0.0
    self._pending: "Optional[tuple]" = None

  def update(self, name: str, done: int, total: int) -> None:
    if name != self._name:
      self.finish()
      self._name = name
      self._last_sent = 0.0
    now = time.monotonic()
    if (total >= 0 and done >= total) or now - self._last_sent >= _PROGRESS_INTERVAL_S:
      self._last_sent = now
      self._pending = None
      self._emit(("progress", name, done, total))
    else:
      self._pending = (name, done, total)

  def finish(self) -> None:
    """Delivers the withheld last update of the current file, if any."""
    pending, self._pending = self._pending, None
    self._name = None
    if pending is not None:
      name, done, total = pending
      # unknown total (-1): the file is complete, so total equals done
      self._emit(("progress", name, done, done if total < 0 else total))


class ConnectionWorker:
  """Runs the FTP client in one dedicated thread; no Tk, testable headless.

  Commands are sent with `submit(cmd, **kw)` and results come back as tuples through the
  `emit` callable given to the constructor (called from the worker thread, never while
  holding the internal lock, except the "busy" rejection which is emitted by the thread
  that called `submit`). One command at a time.

  Commands: connect(cfg), disconnect(), list_remote(path),
  transfer(direction, items, overwrite, overwrite_dirs), mkdir_remote(path),
  plan_dirs(direction, items), run_sync(job).
  `direction` is "up" or "down"; `items` are (local_path, remote_path, is_dir) tuples.
  Messages: ("connected",), ("disconnected", reason), ("remote_listing", path, entries),
  ("busy", bool), ("progress", name, done, total),
  ("transfer_done", {"transferred", "skipped", "errors", "cancelled"}) where errors is a
  list of (name, message), ("dir_conflicts", actions, details) where details maps the
  relative path of an action to a ConflictRow when both copies could be read,
  ("sync_done", result),
  ("error", text).
  """

  def __init__(self, emit: "Callable[[tuple], None]"):
    self._emit_fn = emit
    self.cancel_event = threading.Event()
    self._queue: "queue.Queue" = queue.Queue()
    self._lock = threading.Lock()
    self._busy = False
    self._stopping = False
    self._stopped = False
    self._client = None
    self._thread = threading.Thread(target=self._run, name="ftp-worker", daemon=True)
    self._started = False

  # -- public API (any thread) -------------------------------------------------

  @property
  def connected(self) -> bool:
    return self._client is not None

  @property
  def busy(self) -> bool:
    with self._lock:
      return self._busy

  def start(self) -> None:
    """Starts the worker thread (once)."""
    with self._lock:
      if self._started or self._stopping:
        return
      self._started = True
    self._thread.start()

  def submit(self, cmd: str, **kw) -> None:
    """Queues a command; while another one is running it is rejected, not executed."""
    with self._lock:
      if self._stopping:
        return
      reject = self._busy
      if not reject:
        self._busy = True
    if reject:
      self._emit(("error", tr("busy")))
      return
    self._queue.put((cmd, kw))

  def stop(self, timeout: float = 5.0) -> None:
    """Cancels, ends the thread and closes the connection; safe to call repeatedly.

    Never closes the client from the calling thread (FtpClient is not thread-safe): the
    in-flight transfer is interrupted from inside its own progress callback, and the
    worker thread closes its own client when it leaves. If the thread is still alive
    after `timeout` seconds, only the control socket is shut down as a last resort to
    unblock it. From the moment `stop` is called no further message is emitted.
    """
    with self._lock:
      self._stopping = True
      self._stopped = True
      started = self._started
    self.cancel_event.set()
    self._queue.put(_STOP)
    if not started:
      return
    self._thread.join(timeout)
    if self._thread.is_alive():
      client = self._client
      sock = getattr(getattr(client, "_ftp", None), "sock", None)
      if sock is not None:
        try:
          sock.shutdown(socket.SHUT_RDWR)
        except OSError:
          pass

  # -- internals (worker thread) -------------------------------------------------

  def _emit(self, msg: tuple) -> None:
    if self._stopped:
      return
    try:
      self._emit_fn(msg)
    except Exception:
      # a faulty consumer must never kill the worker thread
      pass

  def _run(self) -> None:
    try:
      while True:
        item = self._queue.get()
        if item is _STOP:
          break
        cmd, kw = item
        self._execute(cmd, kw)
    finally:
      client, self._client = self._client, None
      if client is not None:
        client.close()

  def _execute(self, cmd: str, kw: dict) -> None:
    if not self._stopping:
      self.cancel_event.clear()
    self._emit(("busy", True))
    try:
      handler = getattr(self, "_cmd_" + cmd, None) if cmd.isidentifier() else None
      if handler is None:
        self._emit(("error", tr("unknown_command", cmd=cmd)))
      elif cmd != "connect" and self._client is None:
        self._emit(("error", tr("not_connected")))
      else:
        handler(**kw)
    except FtpError as exc:
      self._fail(str(exc) or exc.__class__.__name__,
                 connection=isinstance(exc, FtpConnectionError))
    except _Aborted:
      # interrupted by stop(): the worker is about to leave and closes its client
      pass
    except (OSError, EOFError) as exc:
      # local problems (missing folder, permissions) do not drop a healthy connection;
      # the library closes the client itself on real network errors (see _fail)
      self._fail(str(exc) or exc.__class__.__name__, connection=False)
    except ValueError as exc:
      self._fail(str(exc) or exc.__class__.__name__, connection=False)
    except Exception as exc:  # last safety net: never a traceback in the interface
      self._fail(tr("unexpected_error", name=exc.__class__.__name__), connection=False)
    finally:
      # a client closed by a network error is dropped, whatever the command did
      self._check_client_alive()
      with self._lock:
        self._busy = False
      self._emit(("busy", False))

  def _fail(self, text: str, connection: bool) -> None:
    """Reports an error; a connection-level one also drops the client."""
    client = self._client
    if client is not None and (connection or client._closed):
      self._drop_client(text)
    else:
      self._emit(("error", text))

  def _drop_client(self, reason: str) -> None:
    client, self._client = self._client, None
    if client is not None:
      client.close()
      self._emit(("disconnected", reason))

  def _check_client_alive(self) -> None:
    client = self._client
    if client is not None and client._closed:
      self._drop_client(tr("connection_closed"))

  # -- commands ------------------------------------------------------------------

  def _cmd_connect(self, cfg: FtpConfig) -> None:
    if self._client is not None:
      self._drop_client(tr("reconnecting"))
    # the password is resolved only here, inside connect()
    self._client = connect(cfg)
    self._emit(("connected",))

  def _cmd_disconnect(self) -> None:
    self._drop_client(tr("disconnected_by_user"))

  def _cmd_list_remote(self, path: str) -> None:
    entries = self._client.list(path)
    self._emit(("remote_listing", path, entries))

  def _cmd_mkdir_remote(self, path: str) -> None:
    self._client.mkdir(path)

  @staticmethod
  def _check_direction(direction: str) -> None:
    if direction not in ("up", "down"):
      raise ValueError("direction must be 'up' or 'down'")

  def _guard(self) -> None:
    """Called from progress callbacks: interrupts the file when the worker is stopping."""
    if self._stopping:
      raise _Aborted()

  def _cmd_plan_dirs(self, direction: str, items: list) -> None:
    self._check_direction(direction)
    conflicts: list = []
    details: dict = {}
    remote_listings: dict = {}
    for local_path, remote_path, is_dir in items:
      if not is_dir:
        continue
      prefix = os.path.basename(os.path.normpath(local_path))
      for action in self._client.plan_sync(direction, local_path, remote_path, Rules()):
        if action.reason in _DIR_CONFLICT_REASONS:
          # rel_path becomes relative to the parent, so that several folders stay distinct
          shown = replace(action, rel_path=prefix + "/" + action.rel_path)
          conflicts.append(shown)
          row = self._conflict_detail(local_path, remote_path, action.rel_path, remote_listings)
          if row is not None:
            details[shown.rel_path] = row
    self._emit(("dir_conflicts", conflicts, details))

  def _conflict_detail(self, local_root: str, remote_root: str, rel: str,
                       remote_listings: dict) -> "Optional[ConflictRow]":
    """Both copies of a file in conflict, or None when one of them cannot be read."""
    parts = rel.split("/")
    try:
      local_dir = os.path.join(local_root, *parts[:-1])
      local = next(e for e in list_local(local_dir) if e.name == parts[-1])
      remote_dir = remote_root
      for part in parts[:-1]:
        remote_dir = join_remote(remote_dir, part)
      if remote_dir not in remote_listings:
        remote_listings[remote_dir] = {e.name: e for e in self._client.list(remote_dir)}
      remote = remote_listings[remote_dir][parts[-1]]
    except FtpConnectionError:
      raise
    except (OSError, FtpError, StopIteration, KeyError):
      return None
    return build_conflict(parts[-1], local, remote)

  def _cmd_transfer(self, direction: str, items: list, overwrite: "set",
                    overwrite_dirs: bool = False) -> None:
    client = self._client
    summary = {"transferred": 0, "skipped": 0, "errors": [], "cancelled": False}
    self._check_direction(direction)
    progress = _ProgressThrottle(self._emit)
    try:
      for local_path, remote_path, is_dir in items:
        if self.cancel_event.is_set():
          summary["cancelled"] = True
          break
        if client._closed:
          break
        name = os.path.basename(os.path.normpath(local_path))
        try:
          if is_dir:
            self._transfer_dir(direction, local_path, remote_path, name, overwrite_dirs,
                               summary, progress)
          else:
            self._transfer_file(direction, local_path, remote_path, name, overwrite,
                                summary, progress)
        except (FtpError, OSError, ValueError) as exc:
          if isinstance(exc, FtpConnectionError) or client._closed:
            raise
          summary["errors"].append((name, str(exc) or exc.__class__.__name__))
        finally:
          progress.finish()
    finally:
      # delivered even when a connection error is about to be reported
      self._emit(("transfer_done", summary))

  def _transfer_file(self, direction, local_path, remote_path, name, overwrite, summary,
                     progress) -> None:
    client = self._client
    if direction == "up":
      exists = client.exists(remote_path)
    else:
      exists = os.path.exists(local_path)
    if exists and name not in overwrite:
      summary["skipped"] += 1
      return

    def on_progress(done: int, total: int) -> None:
      self._guard()
      progress.update(name, done, total)

    if direction == "up":
      client.upload(local_path, remote_path, on_progress)
    else:
      client.download(remote_path, local_path, on_progress)
    summary["transferred"] += 1

  def _transfer_dir(self, direction, local_path, remote_path, name, overwrite_dirs, summary,
                    progress) -> None:
    client = self._client

    def on_progress(action, done: int, total: int) -> None:
      self._guard()
      if action.kind in ("upload", "download"):
        progress.update(name + "/" + action.rel_path, done, total)

    # without permission neither a newer destination nor a size difference is overwritten
    rules = Rules(size_differs=overwrite_dirs, overwrite_newer=overwrite_dirs)
    kw = {"rules": rules, "on_progress": on_progress,
          "cancel": self.cancel_event}
    if direction == "up":
      result = client.upload_dir(local_path, remote_path, **kw)
    else:
      result = client.download_dir(remote_path, local_path, **kw)
    summary["transferred"] += result.transferred
    summary["skipped"] += result.skipped
    summary["errors"].extend((name + "/" + rel, msg) for rel, msg in result.errors)
    if result.cancelled:
      summary["cancelled"] = True

  def _cmd_run_sync(self, job: dict) -> None:
    progress = _ProgressThrottle(self._emit)

    def on_progress(action, done: int, total: int) -> None:
      self._guard()
      if action.kind in ("upload", "download"):
        progress.update(action.rel_path, done, total)

    try:
      result = self._client.sync(
        job["direction"], job["local"], job["remote"], job.get("rules"),
        dry_run=bool(job.get("dry_run", False)), on_progress=on_progress,
        cancel=self.cancel_event)
    finally:
      progress.finish()
    self._emit(("sync_done", result))


def _pick_font(candidates: "tuple[str, ...]", fallback: str) -> str:
  """First available family among the candidates."""
  try:
    available = set(tkfont.families())
  except tk.TclError:
    return fallback
  for name in candidates:
    if name in available:
      return name
  return fallback


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


class App:
  """Main window: profile, route, criteria, plan, progress and log."""

  def __init__(self, root: tk.Tk, profiles_path: Optional[Path] = None,
               language: Optional[str] = None, state_path: Optional[Path] = None):
    self.root = root
    self.translator = translator
    self.state_path = Path(state_path) if state_path else ui_state_path()
    self._ui_state = load_ui_state(self.state_path)
    # explicit argument, then the saved choice, then the system language
    saved_language = self._ui_state.get("language")
    if not language and saved_language in LANGUAGE_CODES:
      language = saved_language
    self.translator.set_language(language if language else detect_language())
    # (widget, option, key, upper): every translatable label, button and checkbutton
    self._i18n: list = []
    # (column id, key) of the plan headings
    self._headings: list = []
    self.profiles_path = Path(profiles_path) if profiles_path else default_profiles_path()
    self.profiles = load_profiles(self.profiles_path)
    self.queue: "queue.Queue" = queue.Queue()
    self.cancel_event = threading.Event()
    self.running = False
    self.direction = "up"
    self._plan: list = []          # actions of the last plan
    self._errors: dict = {}        # relative path -> message
    self._job_buttons: list = []
    self._job_inputs: list = []
    # panes: current folders, shown entries by side and by row id, active side
    self._connected = False
    self._local_cwd: "Optional[str]" = None
    self._remote_cwd: "Optional[str]" = None
    self._pane_entries: "dict[str, list]" = {"local": [], "remote": []}
    self._pane_items: "dict[str, dict]" = {"local": {}, "remote": {}}
    self._active_side = "local"
    self._pending_reload: set = set()
    # a transfer waiting for the folder comparison, and an action to run when the worker is idle
    self._pending_transfer: "Optional[tuple]" = None
    self._deferred: "Optional[Callable[[], None]]" = None
    self._dialog = None            # the confirmation window currently open, if any
    self._status: "tuple[str, dict]" = ("status_not_connected", {})
    self._sash_guard = False
    self._sync_mode = "run"
    self.sync_open = False
    self.closing = False
    self.worker = ConnectionWorker(lambda msg: self.queue.put(("w",) + tuple(msg)))

    root.title(tr("window_title"))
    root.minsize(*APP_MIN_SIZE)
    self.restore_state()
    root.configure(bg=NEBBIA)
    self._init_fonts()
    self._init_style()
    self._build()
    self._refresh_profile_list()
    self._apply_direction()
    self._sync_language_box()
    self._update_sync_toggle()
    self._set_status(self._status[0])
    self._update_controls()
    self.translator.subscribe(self._relabel)
    root.bind_all("<Control-Return>", self._on_run_key)
    root.bind_all("<Command-Return>", self._on_run_key)
    root.bind_all("<Escape>", self._on_cancel_key)
    root.bind_all("<F5>", self._on_f5)
    self._navigate_local(self.var_local.get().strip() or os.path.expanduser("~"))
    root.protocol("WM_DELETE_WINDOW", self._on_close)
    root.after(100, self._drain_queue)

  # -- remembered window state ------------------------------------------------

  def restore_state(self) -> None:
    """Applies the saved size and position, kept inside the screen."""
    root = self.root
    vw, vh = root.winfo_vrootwidth(), root.winfo_vrootheight()
    if vw <= 1 or vh <= 1:
      vw, vh = root.winfo_screenwidth(), root.winfo_screenheight()
    screen = (root.winfo_vrootx(), root.winfo_vrooty(), vw, vh)
    geo = clamp_geometry(self._ui_state, screen, min_size=APP_MIN_SIZE)
    text = "%dx%d" % (geo["width"], geo["height"])
    if geo["x"] is not None:
      # "+-5" is a position left of the origin; "-5" would measure from the right edge
      text += "+%d+%d" % (geo["x"], geo["y"])
    root.geometry(text)

  def save_state(self) -> None:
    """Writes size, position and language; a failure is logged and never blocks closing."""
    state = dict(self._ui_state)
    state["language"] = self.translator.lang
    parsed = parse_geometry(self.root.geometry())
    if parsed:
      state["width"], state["height"], state["x"], state["y"] = parsed
    try:
      save_ui_state(self.state_path, state)
    except OSError as exc:
      _state_log.debug("window state not saved: %s", exc)
    else:
      self._ui_state = state

  # -- style ---------------------------------------------------------------

  def _init_fonts(self) -> None:
    ui = _pick_font(("Helvetica Neue", "Segoe UI", "DejaVu Sans", "Helvetica"), "TkDefaultFont")
    mono = _pick_font(("Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"), "TkFixedFont")
    self.font_ui = (ui, 12)
    self.font_label = (ui, 10)
    self.font_mono = (mono, 11)
    self.font_big = (ui, 14, "bold")

  def _init_style(self) -> None:
    style = ttk.Style(self.root)
    style.theme_use("clam")
    style.configure(".", background=NEBBIA, foreground=INCHIOSTRO, font=self.font_ui,
                    bordercolor=FERRO, lightcolor=NEBBIA, darkcolor=NEBBIA,
                    focuscolor=OTTANIO)
    style.configure("TFrame", background=NEBBIA)
    style.configure("TLabel", background=NEBBIA, foreground=INCHIOSTRO)
    style.configure("Cap.TLabel", font=self.font_label, foreground=INCHIOSTRO_2)
    for name in ("TEntry", "TCombobox"):
      style.configure(name, fieldbackground=GHIACCIO, foreground=INCHIOSTRO,
                      bordercolor=FERRO, lightcolor=GHIACCIO, darkcolor=GHIACCIO,
                      insertcolor=INCHIOSTRO, padding=3)
      style.map(name, bordercolor=[("focus", OTTANIO)], lightcolor=[("focus", OTTANIO)],
                darkcolor=[("focus", OTTANIO)])
    style.map("TCombobox", fieldbackground=[("readonly", GHIACCIO)],
              foreground=[("readonly", INCHIOSTRO)],
              selectbackground=[("readonly", GHIACCIO)],
              selectforeground=[("readonly", INCHIOSTRO)])
    style.configure("TButton", background=GHIACCIO, foreground=INCHIOSTRO, bordercolor=FERRO,
                    lightcolor=GHIACCIO, darkcolor=GHIACCIO, padding=(10, 4))
    style.map("TButton", background=[("active", NEBBIA), ("disabled", NEBBIA)],
              foreground=[("disabled", GRAFITE)],
              bordercolor=[("focus", OTTANIO)], lightcolor=[("focus", OTTANIO)],
              darkcolor=[("focus", OTTANIO)])
    style.configure("Primary.TButton", background=OTTANIO, foreground=GHIACCIO,
                    bordercolor=OTTANIO, lightcolor=OTTANIO, darkcolor=OTTANIO)
    style.map("Primary.TButton", background=[("active", OTTANIO_SCURO), ("disabled", FERRO)],
              foreground=[("disabled", GHIACCIO)],
              bordercolor=[("focus", INCHIOSTRO)], lightcolor=[("focus", INCHIOSTRO)],
              darkcolor=[("focus", INCHIOSTRO)])
    style.configure("Dir.TButton", font=self.font_big, padding=(28, 8), background=OTTANIO,
                    foreground=GHIACCIO, bordercolor=OTTANIO, lightcolor=OTTANIO,
                    darkcolor=OTTANIO)
    style.map("Dir.TButton", background=[("active", OTTANIO_SCURO), ("disabled", FERRO)],
              bordercolor=[("focus", INCHIOSTRO)], lightcolor=[("focus", INCHIOSTRO)],
              darkcolor=[("focus", INCHIOSTRO)])
    style.configure("Toggle.TButton", background=NEBBIA, bordercolor=NEBBIA,
                    lightcolor=NEBBIA, darkcolor=NEBBIA, anchor="w", padding=(2, 2))
    style.configure("TCheckbutton", background=NEBBIA, foreground=INCHIOSTRO)
    style.map("TCheckbutton", background=[("active", NEBBIA)],
              indicatorcolor=[("selected", OTTANIO), ("!selected", GHIACCIO)],
              foreground=[("disabled", GRAFITE)])
    style.configure("Treeview", background=GHIACCIO, fieldbackground=GHIACCIO,
                    foreground=INCHIOSTRO, font=self.font_mono, rowheight=ROW_HEIGHT,
                    bordercolor=FERRO)
    style.configure("Treeview.Heading", background=NEBBIA, foreground=INCHIOSTRO_2,
                    font=self.font_label, relief="flat")
    style.map("Treeview", background=[("selected", SELEZIONE)],
              foreground=[("selected", INCHIOSTRO)])
    style.configure("Horizontal.TProgressbar", background=OTTANIO, troughcolor=GHIACCIO,
                    bordercolor=FERRO, lightcolor=OTTANIO, darkcolor=OTTANIO)
    style.configure("TScrollbar", background=NEBBIA, troughcolor=GHIACCIO, bordercolor=FERRO,
                    arrowcolor=INCHIOSTRO)
    style.configure("Sep.TFrame", background=FILO)
    self.root.option_add("*TCombobox*Listbox.background", GHIACCIO)
    self.root.option_add("*TCombobox*Listbox.foreground", INCHIOSTRO)
    self.root.option_add("*TCombobox*Listbox.selectBackground", OTTANIO)
    self.root.option_add("*TCombobox*Listbox.selectForeground", GHIACCIO)
    self.root.option_add("*TCombobox*Listbox.font", self.font_ui)

  # -- construction ---------------------------------------------------------

  def _reg(self, widget, key: str, option: str = "text", upper: bool = False):
    """Registers a translatable widget option and applies it now; returns the widget."""
    self._i18n.append((widget, option, key, upper))
    self._apply_text(widget, option, key, upper)
    return widget

  @staticmethod
  def _apply_text(widget, option: str, key: str, upper: bool) -> None:
    text = tr(key)
    widget.configure(**{option: text.upper() if upper else text})

  def _label(self, parent, key: str) -> ttk.Label:
    return self._reg(ttk.Label(parent, style="Cap.TLabel"), key, upper=True)

  def _button(self, parent, key: str, **kw) -> ttk.Button:
    return self._reg(ttk.Button(parent, **kw), key)

  def _check(self, parent, key: str, **kw) -> ttk.Checkbutton:
    return self._reg(ttk.Checkbutton(parent, **kw), key)

  def _relabel(self) -> None:
    """Re-applies every registered text after a language change."""
    try:
      for widget, option, key, upper in self._i18n:
        self._apply_text(widget, option, key, upper)
      for tree, col, key in self._headings:
        tree.heading(col, text=tr(key))
      self._update_sync_toggle()
      self._set_status(self._status[0], **self._status[1])
      self._refill_panes()
      self.root.title(tr("window_title"))
      index = self.cb_filter.current()
      self.cb_filter.configure(values=[tr(k) for k in FILTER_KEYS])
      self.cb_filter.current(index if index >= 0 else 0)
      self._apply_direction()
      self._sync_language_box()
      self._refill_plan()
    except tk.TclError:
      # the window is gone: forget this callback
      self.translator.unsubscribe(self._relabel)

  def set_language(self, lang: str) -> None:
    """Switches the whole interface to "en" or "it"."""
    self.translator.set_language(lang)

  def _sync_language_box(self) -> None:
    code = self.translator.lang
    self.language_box.current(LANGUAGE_CODES.index(code))

  def _on_language_selected(self, _event=None) -> None:
    index = self.language_box.current()
    if 0 <= index < len(LANGUAGE_CODES):
      self.set_language(LANGUAGE_CODES[index])

  def _separator(self, parent) -> ttk.Frame:
    return ttk.Frame(parent, style="Sep.TFrame", height=1)

  def _build(self) -> None:
    outer = ttk.Frame(self.root, padding=(16, 12, 16, 12))
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(3, weight=1)   # panes and the part below them

    # variables
    self.var_profile = tk.StringVar()
    self.var_host = tk.StringVar()
    self.var_port = tk.StringVar(value="21")
    self.var_user = tk.StringVar()
    self.var_password = tk.StringVar()
    self.var_tls = tk.StringVar(value="none")
    self.var_local = tk.StringVar()
    self.var_remote = tk.StringVar()
    self.var_use_age = tk.BooleanVar(value=False)
    self.var_age = tk.StringVar(value="7")
    self.var_size = tk.BooleanVar(value=True)
    self.var_newer = tk.BooleanVar(value=True)
    self.var_overwrite = tk.BooleanVar(value=False)
    self.var_filter = tk.StringVar()
    self.var_progress_text = tk.StringVar(value="")
    self.local_path_var = tk.StringVar()
    self.remote_path_var = tk.StringVar()
    self.status_var = tk.StringVar()

    # profile and language
    row_profile = ttk.Frame(outer)
    row_profile.grid(row=0, column=0, sticky="ew")
    row_profile.columnconfigure(1, weight=1)
    self._label(row_profile, "lbl_profile").grid(row=0, column=0, padx=(0, 8))
    self.cb_profile = ttk.Combobox(row_profile, textvariable=self.var_profile)
    self.cb_profile.grid(row=0, column=1, sticky="ew")
    self.cb_profile.bind("<<ComboboxSelected>>", self._on_profile_selected)
    self.btn_save = self._button(row_profile, "btn_save", command=self.save_profile)
    self.btn_save.grid(row=0, column=2, padx=(8, 0))
    self.btn_delete = self._button(row_profile, "btn_delete", command=self.delete_profile)
    self.btn_delete.grid(row=0, column=3, padx=(6, 0))
    # language selector: the names are not translated
    self.language_box = ttk.Combobox(row_profile, state="readonly", width=9,
                                     values=LANGUAGE_NAMES)
    self.language_box.grid(row=0, column=4, padx=(14, 0))
    self.language_box.bind("<<ComboboxSelected>>", self._on_language_selected)

    # connection: the password sits right after the user
    row_conn = ttk.Frame(outer)
    row_conn.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    row_conn.columnconfigure(1, weight=1)
    self._label(row_conn, "lbl_host").grid(row=0, column=0, sticky="w")
    self.ent_host = ttk.Entry(row_conn, textvariable=self.var_host)
    self.ent_host.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 10))
    self._label(row_conn, "lbl_port").grid(row=0, column=2, sticky="w")
    self.ent_port = ttk.Entry(row_conn, textvariable=self.var_port, width=6)
    self.ent_port.grid(row=1, column=2, padx=(0, 10))
    self._label(row_conn, "lbl_user").grid(row=0, column=3, sticky="w")
    self.ent_user = ttk.Entry(row_conn, textvariable=self.var_user, width=14)
    self.ent_user.grid(row=1, column=3, padx=(0, 10))
    self._label(row_conn, "lbl_password").grid(row=0, column=4, sticky="w")
    self.ent_password = ttk.Entry(row_conn, textvariable=self.var_password, show="*", width=14)
    self.ent_password.grid(row=1, column=4, padx=(0, 10))
    self._label(row_conn, "lbl_tls").grid(row=0, column=5, sticky="w")
    self.cb_tls = ttk.Combobox(row_conn, textvariable=self.var_tls, state="readonly", width=9,
                               values=("none", "explicit", "implicit"))
    self.cb_tls.grid(row=1, column=5, sticky="w", padx=(0, 10))
    self.btn_connect = self._button(row_conn, "btn_connect", style="Primary.TButton",
                                    command=self.connect_clicked)
    self.btn_connect.grid(row=1, column=6)
    self.btn_disconnect = self._button(row_conn, "btn_disconnect",
                                       command=self.disconnect_clicked)
    self.btn_disconnect.grid(row=1, column=7, padx=(6, 0))

    self._separator(outer).grid(row=2, column=0, sticky="ew", pady=10)

    # the panes and, below them, the sync section, progress and log; the sash between
    # the two parts is draggable and the panes take the extra height of a taller window
    self.main_split = ttk.Panedwindow(outer, orient="vertical")
    self.main_split.grid(row=3, column=0, sticky="nsew")
    self.top_pane = ttk.Frame(self.main_split)
    self.bottom_pane = ttk.Frame(self.main_split)
    self.main_split.add(self.top_pane, weight=1)
    self.main_split.add(self.bottom_pane, weight=0)
    self._build_panes(self.top_pane)
    self._build_bottom(self.bottom_pane)
    self.top_pane.bind("<Configure>", self._on_panes_configure)

    self._job_buttons = [self.btn_save, self.btn_delete, self.btn_choose, self.btn_dir,
                         self.btn_preview, self.btn_run]
    self._job_inputs = [self.cb_profile, self.ent_host, self.ent_port, self.ent_user,
                        self.ent_password, self.cb_tls, self.ent_local, self.ent_remote,
                        self.chk_age, self.ent_age, self.chk_size, self.chk_newer,
                        self.chk_overwrite]

  def _build_panes(self, parent) -> None:
    parent.columnconfigure(0, weight=1, uniform="pane")
    parent.columnconfigure(2, weight=1, uniform="pane")
    parent.rowconfigure(0, weight=1)
    local = self._build_pane(parent, "local")
    local["frame"].grid(row=0, column=0, sticky="nsew")
    self.ent_local_path, self.local_tree = local["entry"], local["tree"]
    self.btn_mkdir_local = local["mkdir"]

    center = ttk.Frame(parent)
    center.grid(row=0, column=1, sticky="ns", padx=10)
    center.rowconfigure(0, weight=1)
    center.rowconfigure(3, weight=1)
    self.btn_upload = self._button(center, "btn_upload", style="Primary.TButton", width=12,
                                   command=self.upload_selected)
    self.btn_upload.grid(row=1, column=0, pady=(0, 6))
    self.btn_download = self._button(center, "btn_download", style="Primary.TButton",
                                     width=12, command=self.download_selected)
    self.btn_download.grid(row=2, column=0)

    remote = self._build_pane(parent, "remote")
    remote["frame"].grid(row=0, column=2, sticky="nsew")
    self.ent_remote_path, self.remote_tree = remote["entry"], remote["tree"]
    self.btn_mkdir_remote = remote["mkdir"]
    status = ttk.Label(remote["head"], textvariable=self.status_var, style="Cap.TLabel")
    status.grid(row=0, column=1, sticky="e")

  def _build_pane(self, parent, side: str) -> dict:
    """One pane: caption, editable path bar, list and the New folder button."""
    path_var = self.local_path_var if side == "local" else self.remote_path_var
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)
    head = ttk.Frame(frame)
    head.grid(row=0, column=0, sticky="ew")
    head.columnconfigure(1, weight=1)
    self._label(head, "lbl_%s_pane" % side).grid(row=0, column=0, sticky="w")
    entry = ttk.Entry(frame, textvariable=path_var)
    entry.grid(row=1, column=0, sticky="ew", pady=(2, 4))
    entry.bind("<Return>", lambda _e, s=side: self.go_to_path(s))
    entry.bind("<FocusIn>", lambda _e, s=side: self._set_active(s))

    tree_frame = ttk.Frame(frame)
    tree_frame.grid(row=2, column=0, sticky="nsew")
    tree_frame.columnconfigure(0, weight=1)
    tree_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(tree_frame, columns=("name", "size", "date"), show="headings",
                        height=PANE_ROWS, selectmode="extended")
    for col, key, width, anchor, stretch in (
        ("name", "col_name", 120, "w", True), ("size", "col_size", 80, "e", False),
        ("date", "col_date", 140, "w", False)):
      self._headings.append((tree, col, key))
      tree.heading(col, text=tr(key), anchor=anchor)
      tree.column(col, width=width, anchor=anchor, stretch=stretch)
    tree.tag_configure("alt", background=GHIACCIO_SCURO)
    sb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    tree.bind("<Double-1>", lambda e, s=side: self._on_tree_double(s, e))
    tree.bind("<Return>", lambda _e, s=side: self._on_tree_return(s))
    tree.bind("<FocusIn>", lambda _e, s=side: self._set_active(s))

    mkdir = self._button(frame, "btn_mkdir", command=lambda s=side: self.new_folder(s))
    mkdir.grid(row=3, column=0, sticky="w", pady=(6, 0))
    return {"frame": frame, "head": head, "entry": entry, "tree": tree, "mkdir": mkdir}

  def _build_route(self, parent) -> None:
    """Local folder, direction button with its line and remote folder of the sync."""
    route = ttk.Frame(parent)
    route.grid(row=0, column=0, sticky="ew")
    route.columnconfigure(1, weight=1)
    self._label(route, "lbl_local_dir").grid(row=0, column=0, sticky="w", pady=(0, 2))
    self.ent_local = ttk.Entry(route, textvariable=self.var_local)
    self.ent_local.grid(row=1, column=0, columnspan=2, sticky="ew")
    self.btn_choose = self._button(route, "btn_choose", command=self.choose_local)
    self.btn_choose.grid(row=1, column=2, padx=(8, 0))

    rail = ttk.Frame(route)
    rail.grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)
    rail.columnconfigure(0, weight=1)
    rail.columnconfigure(2, weight=1)
    left_color, right_color = rail_colors(self.direction)
    self.rail_left = tk.Frame(rail, height=3, bg=left_color, highlightthickness=0)
    self.rail_left.grid(row=0, column=0, sticky="ew")
    self.btn_dir = ttk.Button(rail, style="Dir.TButton", command=self.toggle_direction)
    self.btn_dir.grid(row=0, column=1, padx=12)
    self.rail_right = tk.Frame(rail, height=3, bg=right_color, highlightthickness=0)
    self.rail_right.grid(row=0, column=2, sticky="ew")

    self._label(route, "lbl_remote_dir").grid(row=3, column=0, sticky="w", pady=(0, 2))
    self.ent_remote = ttk.Entry(route, textvariable=self.var_remote)
    self.ent_remote.grid(row=4, column=0, columnspan=3, sticky="ew")

  def _build_sync(self, parent) -> ttk.Frame:
    """Content of the collapsible sync section: route, criteria, buttons and plan."""
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)
    self._build_route(frame)
    self._separator(frame).grid(row=1, column=0, sticky="ew", pady=8)
    self._build_criteria_and_plan(frame)
    return frame

  def _build_criteria_and_plan(self, outer) -> None:
    box = ttk.Frame(outer)
    box.grid(row=2, column=0, sticky="nsew")
    box.columnconfigure(0, weight=1)
    box.rowconfigure(3, weight=1)

    crit = ttk.Frame(box)
    crit.grid(row=0, column=0, sticky="ew")
    self._label(crit, "lbl_criteria").grid(row=0, column=0, sticky="w", padx=(0, 10))
    self.chk_age = self._check(crit, "chk_age", variable=self.var_use_age)
    self.chk_age.grid(row=0, column=1, sticky="w")
    self.ent_age = ttk.Entry(crit, textvariable=self.var_age, width=5)
    self.ent_age.grid(row=0, column=2, padx=4)
    self._reg(ttk.Label(crit), "lbl_days").grid(row=0, column=3, sticky="w", padx=(0, 14))
    self.chk_size = self._check(crit, "chk_size", variable=self.var_size)
    self.chk_size.grid(row=0, column=4, sticky="w")
    self.chk_newer = self._check(crit, "chk_newer", variable=self.var_newer)
    self.chk_newer.grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))
    self.chk_overwrite = self._check(crit, "chk_overwrite", variable=self.var_overwrite)
    self.chk_overwrite.grid(row=1, column=4, sticky="w", pady=(4, 0))

    buttons = ttk.Frame(box)
    buttons.grid(row=1, column=0, sticky="w", pady=10)
    self.btn_preview = self._button(buttons, "btn_preview", command=self.start_preview)
    self.btn_preview.grid(row=0, column=0)
    self.btn_run = self._button(buttons, "btn_run", style="Primary.TButton",
                                command=self.start_run)
    self.btn_run.grid(row=0, column=1, padx=8)

    head = ttk.Frame(box)
    head.grid(row=2, column=0, sticky="ew")
    head.columnconfigure(0, weight=1)
    self._label(head, "lbl_plan").grid(row=0, column=0, sticky="w")
    self._label(head, "lbl_filter").grid(row=0, column=1, padx=(0, 6))
    self.cb_filter = ttk.Combobox(head, textvariable=self.var_filter, state="readonly",
                                  values=[tr(k) for k in FILTER_KEYS], width=20)
    self.cb_filter.current(0)
    self.cb_filter.grid(row=0, column=2)
    self.cb_filter.bind("<<ComboboxSelected>>", lambda _e: self._refill_plan())

    tree_frame = ttk.Frame(box)
    tree_frame.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
    tree_frame.columnconfigure(0, weight=1)
    tree_frame.rowconfigure(0, weight=1)
    self.tree = ttk.Treeview(tree_frame, columns=("word", "path", "note", "size"),
                             show="headings", height=5, selectmode="browse")
    for col, key, width, anchor, stretch in (
        ("word", "col_action", 70, "w", False), ("path", "col_path", 300, "w", True),
        ("note", "col_note", 190, "w", False), ("size", "col_size", 90, "e", False)):
      self._headings.append((self.tree, col, key))
      self.tree.heading(col, text=tr(key), anchor=anchor)
      self.tree.column(col, width=width, anchor=anchor, stretch=stretch)
    self.tree.tag_configure("transfer", foreground=OTTANIO)
    self.tree.tag_configure("dir", foreground=INCHIOSTRO)
    self.tree.tag_configure("skip", foreground=GRAFITE)
    self.tree.tag_configure("conflict", foreground=AMBRA, background=AMBRA_CHIARO)
    self.tree.tag_configure("err", foreground=MATTONE, background=MATTONE_CHIARO)
    # alternate rows: only background, and never on conflict or error rows
    self.tree.tag_configure("alt", background=GHIACCIO_SCURO)
    sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
    self.tree.configure(yscrollcommand=sb.set)
    self.tree.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")

  def _build_bottom(self, parent) -> None:
    """Sync section toggle (closed at start), progress bar and log."""
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)
    self.sync_toggle = ttk.Button(parent, style="Toggle.TButton", command=self.toggle_sync)
    self.sync_toggle.grid(row=0, column=0, sticky="w", pady=(8, 0))
    self.sync_frame = self._build_sync(parent)
    self.sync_frame.grid(row=1, column=0, sticky="nsew")
    self.sync_frame.grid_remove()
    prog = ttk.Frame(parent)
    prog.grid(row=2, column=0, sticky="ew", pady=(6, 0))
    prog.columnconfigure(0, weight=1)
    self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
    self.progress.grid(row=0, column=0, sticky="ew")
    ttk.Label(prog, textvariable=self.var_progress_text, width=12, anchor="e").grid(
      row=0, column=1, padx=(8, 0))
    # visible whatever the sync section state: it stops pane transfers too
    self.btn_cancel = self._button(prog, "btn_cancel", command=self.cancel, state="disabled")
    self.btn_cancel.grid(row=0, column=2, padx=(8, 0))
    log_frame = ttk.Frame(parent)
    log_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(0, weight=1)
    self.log = tk.Text(log_frame, height=5, wrap="word", state="disabled", bg=GHIACCIO,
                       fg=INCHIOSTRO, font=self.font_mono, relief="flat", borderwidth=0,
                       highlightthickness=1, highlightbackground=FERRO,
                       highlightcolor=OTTANIO, padx=6, pady=4)
    self.log.tag_configure("ok", foreground=INCHIOSTRO)
    self.log.tag_configure("ok_accent", foreground=OTTANIO)
    self.log.tag_configure("warn", foreground=AMBRA)
    self.log.tag_configure("err", foreground=MATTONE)
    sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
    self.log.configure(yscrollcommand=sb.set)
    self.log.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")

  # -- sync section and sash ---------------------------------------------------

  def _update_sync_toggle(self) -> None:
    self.sync_toggle.configure(
      text=("v " if self.sync_open else "> ") + tr("lbl_sync_section"))

  def toggle_sync(self) -> None:
    """Opens or closes the sync section; the panes keep their height, the window follows."""
    root = self.root
    ready = self.main_split.winfo_height() > 1
    top_before = self.main_split.sashpos(0) if ready else 0
    # window height without the split, and the sash thickness, to size the window afterwards
    chrome = root.winfo_height() - self.main_split.winfo_height()
    sash = self.main_split.winfo_height() - top_before - self.bottom_pane.winfo_height()
    self.sync_open = not self.sync_open
    if self.sync_open:
      self.sync_frame.grid()
    else:
      self.sync_frame.grid_remove()
    self.bottom_pane.rowconfigure(1, weight=1 if self.sync_open else 0)
    self._update_sync_toggle()
    root.update_idletasks()
    if not ready:
      return
    top = max(top_before, self._min_panes_height())
    if root.state() == "normal":
      wanted = chrome + top + sash + self.bottom_pane.winfo_reqheight()
      room = root.winfo_screenheight() - root.winfo_y() - 60
      height = max(APP_MIN_SIZE[1], min(wanted, room))
      if height != root.winfo_height():
        root.geometry("%dx%d" % (root.winfo_width(), height))
        root.update()
    self.main_split.sashpos(0, top)

  def _min_panes_height(self) -> int:
    """Smallest height of the panes area: PANE_MIN_ROWS list rows plus the fixed parts."""
    return self.top_pane.winfo_reqheight() - (PANE_ROWS - PANE_MIN_ROWS) * ROW_HEIGHT

  def _on_panes_configure(self, _event=None) -> None:
    if not self._sash_guard:
      self.root.after_idle(self._enforce_pane_min)

  def _enforce_pane_min(self) -> None:
    """Moves the sash back when a drag or a small window left fewer than PANE_MIN_ROWS rows."""
    self._sash_guard = True
    try:
      if self.main_split.winfo_height() > 1:
        low = self._min_panes_height()
        if self.main_split.sashpos(0) < low:
          self.main_split.sashpos(0, low)
    except tk.TclError:
      pass
    finally:
      self._sash_guard = False

  # -- panes ---------------------------------------------------------------------

  def _set_active(self, side: str) -> None:
    self._active_side = side

  def _tree(self, side: str) -> ttk.Treeview:
    return self.local_tree if side == "local" else self.remote_tree

  def _fill_tree(self, side: str, entries: list) -> None:
    self._pane_entries[side] = list(entries)
    self._redraw(side)

  def _refill_panes(self) -> None:
    for side in ("local", "remote"):
      self._redraw(side)

  def _redraw(self, side: str) -> None:
    """Shows the entries of a pane: a ".." row first, folders before files."""
    tree = self._tree(side)
    tree.delete(*tree.get_children())
    items: dict = {}
    cwd = self._local_cwd if side == "local" else self._remote_cwd
    if cwd is not None:
      rows = [RemoteEntry("..", True, 0, None)] + sort_entries(self._pane_entries[side])
      for index, entry in enumerate(rows):
        tags = ("alt",) if index % 2 else ()
        iid = tree.insert("", "end", values=pane_row_values(entry), tags=tags)
        items[iid] = entry
    self._pane_items[side] = items

  def selected_entries(self, side: str) -> "List[RemoteEntry]":
    """Entries selected in a pane, without the ".." row."""
    items = self._pane_items[side]
    entries = [items[iid] for iid in self._tree(side).selection() if iid in items]
    return [e for e in entries if e.name != ".."]

  def _navigate_local(self, path: str, quiet: bool = False) -> None:
    path = os.path.abspath(os.path.expanduser(path.strip() or "~"))
    try:
      entries = list_local(path)
    except OSError as exc:
      if not quiet:
        self._log(tr("log_pane_error", path=path, error=exc), "err")
      self.local_path_var.set(self._local_cwd or "")
      return
    self._local_cwd = path
    self.local_path_var.set(path)
    self._fill_tree("local", entries)

  def _navigate_remote(self, path: str) -> None:
    if self._connected and not self.running:
      self._submit("list_remote", path=path.strip() or "/")

  def go_to_path(self, side: str) -> None:
    """Goes to the folder written in the path bar of a pane."""
    if self.running:
      return
    if side == "local":
      self._navigate_local(self.local_path_var.get())
    else:
      self._navigate_remote(self.remote_path_var.get())

  def reload(self, side: str) -> None:
    """Lists the current folder of a pane again."""
    if self.running:
      return
    if side == "local":
      self._navigate_local(self._local_cwd or self.local_path_var.get())
    else:
      self._navigate_remote(self._remote_cwd or self.remote_path_var.get())

  def open_selected(self, side: str) -> None:
    """Enters the selected folder (".." goes up); a file does nothing."""
    if self.running:
      return
    chosen = self._tree(side).selection()
    entry = self._pane_items[side].get(chosen[0]) if chosen else None
    if entry is None or not entry.is_dir:
      return
    if side == "local":
      cwd = self._local_cwd or ""
      target = parent_local(cwd) if entry.name == ".." else os.path.join(cwd, entry.name)
      self._navigate_local(target)
    else:
      cwd = self._remote_cwd
      if cwd is None:
        return
      target = parent_remote(cwd) if entry.name == ".." else join_remote(cwd, entry.name)
      self._navigate_remote(target)

  def _on_tree_double(self, side: str, event) -> None:
    if event.widget.identify_region(event.x, event.y) == "cell":
      self.open_selected(side)

  def _on_tree_return(self, side: str) -> str:
    self.open_selected(side)
    return "break"

  def _on_f5(self, _event=None) -> str:
    self.reload(self._active_side)
    return "break"

  def new_folder(self, side: str, name: "Optional[str]" = None) -> None:
    """Creates a folder in a pane; without `name` the user is asked for it."""
    if self.running or (side == "remote" and not self._connected):
      return
    if name is None:
      name = simpledialog.askstring(tr("dlg_new_folder_title"), tr("dlg_new_folder_prompt"),
                                    parent=self.root)
      if name is None:
        return
    try:
      name = validate_folder_name(name)
    except ValueError as exc:
      self._log(str(exc), "err")
      return
    if side == "local":
      try:
        os.mkdir(os.path.join(self._local_cwd or "", name))
      except OSError as exc:
        self._log(tr("log_folder_failed", error=exc), "err")
        return
      self._log(tr("log_folder_created", name=name), "ok")
      self.reload("local")
    elif self._remote_cwd is not None:
      if self._submit("mkdir_remote", path=join_remote(self._remote_cwd, name)):
        self._pending_reload.add("remote")

  def _transfer_items(self, side: str) -> list:
    """(local path, remote path, is_dir) for the selection of a pane."""
    if self._local_cwd is None or self._remote_cwd is None:
      return []
    return [(os.path.join(self._local_cwd, e.name), join_remote(self._remote_cwd, e.name),
             e.is_dir) for e in self.selected_entries(side)]

  def _start_transfer(self, direction: str) -> None:
    source = "local" if direction == "up" else "remote"
    items = self._transfer_items(source)
    if self.running or not self._connected or not items:
      return
    # nothing is replaced without a yes: files first (from the pane listings), then folders
    try:
      rows = find_conflicts(self.selected_entries(source),
                            self._pane_entries["remote" if direction == "up" else "local"],
                            source)
    except ValueError as exc:
      self._log(str(exc), "err")
      return
    overwrite: set = set()
    if rows:
      total = len(rows)
      resolver = ConflictResolver(
        lambda row, remaining: self._ask_file_conflict(row, total - remaining + 1, total))
      overwrite, _skipped, cancelled = resolver.resolve(rows)
      if self.closing:
        return
      if cancelled:
        self._log(tr("log_transfer_declined"), "warn")
        return
    if any(is_dir for _l, _r, is_dir in items):
      # the comparison runs on the connection thread; the transfer goes on when it answers
      self._pending_transfer = (direction, items, overwrite)
      if not self._submit("plan_dirs", direction=direction, items=items):
        self._pending_transfer = None
      return
    self._begin_transfer(direction, items, overwrite, False)

  def _begin_transfer(self, direction: str, items: list, overwrite: set,
                      overwrite_dirs: bool) -> None:
    self.progress.configure(value=0)
    self.var_progress_text.set("")
    if self._submit("transfer", direction=direction, items=items, overwrite=overwrite,
                    overwrite_dirs=overwrite_dirs):
      self._log(tr("log_transfer_running"), "ok")
      self._pending_reload.update(("local", "remote"))

  def _ask_file_conflict(self, row: ConflictRow, index: int, total: int) -> tuple:
    """Opens the window for one file conflict and returns (decision, apply to all)."""
    dialog = ConflictDialog(self.root, row, index, total)
    self._dialog = dialog
    try:
      return dialog.show()
    finally:
      self._dialog = None

  def _ask_dir_conflict(self, actions: list, rows: dict) -> str:
    """Opens the window for the differences inside folders: "overwrite", "skip" or "cancel"."""
    dialog = DirConflictDialog(self.root, actions, self._local_cwd or "", self._remote_cwd or "",
                               rows)
    self._dialog = dialog
    try:
      return dialog.show()
    finally:
      self._dialog = None

  def _on_dir_conflicts(self, actions: list, rows: dict) -> None:
    pending, self._pending_transfer = self._pending_transfer, None
    if pending is None:
      return
    direction, items, overwrite = pending
    decision = self._ask_dir_conflict(actions, rows) if actions else "skip"
    if self.closing:
      return
    if decision == "cancel":
      self._log(tr("log_transfer_declined"), "warn")
      return
    # the worker is still finishing plan_dirs: the transfer goes out when it is idle
    self._deferred = lambda: self._begin_transfer(direction, items, overwrite,
                                                  decision == "overwrite")

  def upload_selected(self) -> None:
    """Sends the files and folders selected in the local pane to the remote folder."""
    self._start_transfer("up")

  def download_selected(self) -> None:
    """Gets the files and folders selected in the remote pane into the local folder."""
    self._start_transfer("down")

  # -- connection and worker messages ----------------------------------------------

  def _ensure_worker(self) -> None:
    self.worker.start()

  def _submit(self, cmd: str, **kw) -> bool:
    """Sends a command to the connection thread; False if one is already running."""
    if self.running or self.closing:
      return False
    self._ensure_worker()
    self._set_running(True)
    self.worker.submit(cmd, **kw)
    return True

  def connect_clicked(self) -> None:
    """Opens the persistent connection with the fields of the connection bar."""
    if self.running or self.closing:
      return
    try:
      host, port = validate_connection(self.var_host.get(), self.var_port.get())
    except ValueError as exc:
      self._log(str(exc), "err")
      return
    cfg = FtpConfig(host=host, port=port, user=self.var_user.get().strip() or "anonymous",
                    password=self.var_password.get() or None, tls=self.var_tls.get())
    self._set_status("status_connecting")
    self._submit("connect", cfg=cfg)

  def disconnect_clicked(self) -> None:
    """Closes the persistent connection."""
    if self._connected:
      self._submit("disconnect")

  def _set_status(self, key: str, **kw) -> None:
    self._status = (key, kw)
    self.status_var.set(tr(key, **kw))

  def _update_controls(self) -> None:
    """Enables the connection and transfer controls according to connection and busy state."""
    idle = not self.running
    online = idle and self._connected

    def state(flag: bool) -> str:
      return "normal" if flag else "disabled"

    self.btn_connect.configure(state=state(idle and not self._connected))
    self.btn_disconnect.configure(state=state(online))
    self.btn_upload.configure(state=state(online))
    self.btn_download.configure(state=state(online))
    self.btn_mkdir_local.configure(state=state(idle))
    self.btn_mkdir_remote.configure(state=state(online))
    self.ent_local_path.configure(state=state(idle))
    self.ent_remote_path.configure(state=state(online))

  def _run_pending_reloads(self) -> None:
    pending, self._pending_reload = self._pending_reload, set()
    if "local" in pending:
      self.reload("local")
    if "remote" in pending:
      self.reload("remote")

  def _handle_worker(self, msg: tuple) -> None:
    """Messages of the connection thread (they arrive through the same queue)."""
    kind = msg[0]
    if kind == "connected":
      self._connected = True
      host = self.var_host.get().strip()
      self._set_status("status_connected", host=host)
      self._log(tr("log_connected", host=host), "ok_accent")
      self.remote_path_var.set(self.var_remote.get().strip() or "/")
      # the connect command is still finishing: the listing goes out when it is idle
      self._pending_reload.add("remote")
      self._update_controls()
    elif kind == "disconnected":
      self._connected = False
      self._remote_cwd = None
      self._pending_transfer = None
      self._deferred = None
      self._pending_reload.discard("remote")
      self._fill_tree("remote", [])
      self.remote_path_var.set("")
      self._set_status("status_not_connected")
      self._log(tr("log_disconnected", reason=msg[1]), "warn")
      self._update_controls()
    elif kind == "remote_listing":
      self._remote_cwd = msg[1]
      self.remote_path_var.set(msg[1])
      self._fill_tree("remote", msg[2])
    elif kind == "busy":
      if msg[1]:
        self._set_running(True)
      else:
        self._set_running(False)
        if self._deferred is not None:
          action, self._deferred = self._deferred, None
          action()
        else:
          self._run_pending_reloads()
    elif kind == "dir_conflicts":
      self._on_dir_conflicts(msg[1], msg[2] if len(msg) > 2 else {})
    elif kind == "progress":
      _, _name, done, total = msg
      percent = min(100.0, done / total * 100.0) if total > 0 else 0.0
      self.progress.configure(value=percent)
      self.var_progress_text.set("%d%%" % percent)
    elif kind == "transfer_done":
      self._report_transfer(msg[1])
    elif kind == "sync_done":
      result = msg[1]
      self._plan = list(result.actions)
      self._errors = {rel: text for rel, text in result.errors}
      self._refill_plan()
      if self._sync_mode == "preview":
        self._report_preview(result.actions)
      else:
        self._report_run(result, sum(1 for a in result.actions
                                     if a.kind in ("upload", "download")))
        self._pending_reload.update(("local", "remote"))
    elif kind == "error":
      self._log(tr("error_prefix") + str(msg[1]), "err")
      self._pending_transfer = None
      if self._remote_cwd is not None:
        self.remote_path_var.set(self._remote_cwd)
      if not self._connected:
        self._set_status("status_not_connected")

  def _report_transfer(self, summary: dict) -> None:
    for name, text in summary["errors"]:
      self._log(tr("log_item_error", rel=name, text=text), "err")
    if summary["cancelled"]:
      self._log(tr("log_transfer_cancelled", transferred=summary["transferred"]), "warn")
    else:
      self._log(tr("log_transfer_done", transferred=summary["transferred"],
                   skipped=summary["skipped"], errors=len(summary["errors"])),
                "err" if summary["errors"] else "ok_accent")
    self.progress.configure(value=0)
    self.var_progress_text.set("")
    self._pending_reload.update(("local", "remote"))

  def _report_preview(self, plan: list) -> None:
    moves = sum(1 for a in plan if a.kind in ("upload", "download"))
    conflicts = sum(1 for a in plan if a.reason == "conflict-dest-newer")
    self._log(tr("log_preview_done", moves=moves,
                 skipped=sum(1 for a in plan if a.kind == "skip"), conflicts=conflicts),
              "ok_accent")

  def _report_run(self, result, todo: int) -> None:
    self._errors = {rel: text for rel, text in result.errors}
    self._refill_plan()
    for rel, text in result.errors:
      self._log(tr("log_item_error", rel=rel, text=text), "err")
    if result.cancelled:
      self._log(tr("log_cancelled", count=result.transferred), "warn")
    else:
      self._log(tr("log_done", transferred=result.transferred, skipped=result.skipped,
                   errors=len(result.errors)),
                "err" if result.errors else "ok_accent")
      self.progress.configure(value=100 if todo else 0)
      self.var_progress_text.set("%d / %d" % (result.transferred, todo) if todo else "")

  # -- direction -----------------------------------------------------------

  def toggle_direction(self) -> None:
    """Flips the direction (up = local -> server, down = server -> local)."""
    self.direction = "down" if self.direction == "up" else "up"
    self._apply_direction()

  def _apply_direction(self) -> None:
    self.btn_dir.configure(text=tr("dir_up" if self.direction == "up" else "dir_down"))
    # the half of the line we are heading to lights up, the other stays Ferro
    left_color, right_color = rail_colors(self.direction)
    self.rail_left.configure(bg=left_color)
    self.rail_right.configure(bg=right_color)

  # -- profiles -------------------------------------------------------------

  def _refresh_profile_list(self) -> None:
    self.cb_profile.configure(values=sorted(self.profiles))

  def _collect_profile(self) -> dict:
    """Saveable fields; the password is never included."""
    return {
      "host": self.var_host.get().strip(),
      "port": self.var_port.get().strip(),
      "user": self.var_user.get().strip(),
      "tls": self.var_tls.get(),
      "local_dir": self.var_local.get().strip(),
      "remote_dir": self.var_remote.get().strip(),
      "direction": self.direction,
      "use_age": bool(self.var_use_age.get()),
      "max_age_days": self.var_age.get().strip(),
      "size_differs": bool(self.var_size.get()),
      "newer": bool(self.var_newer.get()),
      "overwrite_newer": bool(self.var_overwrite.get()),
    }

  def fill(self, **values) -> None:
    """Fills the fields from a dict (profile or programmatic use)."""
    setters = {
      "host": self.var_host, "user": self.var_user, "tls": self.var_tls,
      "local_dir": self.var_local, "remote_dir": self.var_remote,
      "max_age_days": self.var_age, "password": self.var_password,
    }
    for key, var in setters.items():
      if key in values and values[key] is not None:
        var.set(str(values[key]))
    if values.get("port") not in (None, ""):
      self.var_port.set(str(values["port"]))
    for key, var in (("use_age", self.var_use_age), ("size_differs", self.var_size),
                     ("newer", self.var_newer), ("overwrite_newer", self.var_overwrite)):
      if key in values:
        var.set(bool(values[key]))
    if values.get("direction") in ("up", "down"):
      self.direction = values["direction"]
      self._apply_direction()
    if values.get("local_dir"):
      self._navigate_local(str(values["local_dir"]), quiet=True)

  def _on_profile_selected(self, _event=None) -> None:
    profile = self.profiles.get(self.var_profile.get())
    if profile:
      self.fill(**profile)
      # the password is not in the profile: it has to be re-entered every time
      self.var_password.set("")

  def save_profile(self) -> None:
    """Saves the current fields in the profile named in the profile box (without password)."""
    name = self.var_profile.get().strip()
    if not name:
      self._log(tr("log_profile_name_required"), "warn")
      return
    self.profiles[name] = self._collect_profile()
    try:
      save_profiles(self.profiles_path, self.profiles)
    except OSError as exc:
      self._log(tr("log_profile_not_saved", error=exc), "err")
      return
    self._refresh_profile_list()
    self._log(tr("log_profile_saved", name=name), "ok")

  def delete_profile(self) -> None:
    """Deletes the selected profile after confirmation."""
    name = self.var_profile.get().strip()
    if name not in self.profiles:
      self._log(tr("log_profile_none"), "warn")
      return
    if not messagebox.askyesno(tr("dlg_delete_profile_title"),
                               tr("dlg_delete_profile_msg", name=name),
                               parent=self.root):
      return
    del self.profiles[name]
    try:
      save_profiles(self.profiles_path, self.profiles)
    except OSError as exc:
      self._log(tr("log_profiles_not_updated", error=exc), "err")
    self.var_profile.set("")
    self._refresh_profile_list()
    self._log(tr("log_profile_deleted", name=name), "ok")

  def choose_local(self) -> None:
    """Opens the local directory chooser."""
    path = filedialog.askdirectory(parent=self.root, title=tr("dlg_choose_local_title"))
    if path:
      self.var_local.set(path)

  # -- log and plan ---------------------------------------------------------

  def _log(self, text: str, tag: str = "ok") -> None:
    self.log.configure(state="normal")
    self.log.insert("end", text + "\n", tag)
    self.log.see("end")
    self.log.configure(state="disabled")

  def _row_values(self, action: Action) -> "tuple[tuple, str]":
    word, rel, note = describe_action(action)
    tag = action_tag(action)
    if rel in self._errors and action.kind in ("upload", "download", "mkdir"):
      word, note, tag = tr("word_err"), self._errors[rel], "err"
    size = format_size(action.size) if action.kind != "mkdir" else ""
    return (word, rel, note, size), tag

  def _passes_filter(self, action: Action) -> bool:
    index = self.cb_filter.current()
    mode = FILTER_IDS[index] if 0 <= index < len(FILTER_IDS) else "all"
    if mode == "transfers":
      return action.kind in ("upload", "download", "mkdir")
    if mode == "conflicts_errors":
      return action.reason == "conflict-dest-newer" or action.rel_path in self._errors
    return True

  def _refill_plan(self) -> None:
    self.tree.delete(*self.tree.get_children())
    shown = 0
    for action in self._plan:
      if self._passes_filter(action):
        values, tag = self._row_values(action)
        tags = (tag, "alt") if shown % 2 and tag not in ("conflict", "err") else (tag,)
        self.tree.insert("", "end", values=values, tags=tags)
        shown += 1

  # -- jobs --------------------------------------------------------------

  def _set_running(self, running: bool) -> None:
    self.running = running
    state = "disabled" if running else "normal"
    for btn in self._job_buttons:
      btn.configure(state=state)
    for widget in self._job_inputs:
      if isinstance(widget, ttk.Combobox) and widget is self.cb_tls:
        widget.configure(state="disabled" if running else "readonly")
      else:
        widget.configure(state=state)
    self.btn_cancel.configure(state="normal" if running else "disabled")
    self._update_controls()

  def _snapshot(self) -> Optional[dict]:
    """Reads the fields in the main thread; None if something is wrong (message in the log)."""
    try:
      host, port, local, remote = validate_fields(
        self.var_host.get(), self.var_port.get(), self.var_local.get(),
        self.var_remote.get())
    except ValueError as exc:
      self._log(str(exc), "err")
      return None
    age_text = self.var_age.get() if self.var_use_age.get() else ""
    try:
      rules = build_rules(age_text, self.var_size.get(), self.var_newer.get(),
                          self.var_overwrite.get())
    except ValueError as exc:
      self._log(str(exc), "err")
      return None
    cfg = FtpConfig(host=host, port=port, user=self.var_user.get().strip() or "anonymous",
                    password=self.var_password.get() or None, tls=self.var_tls.get())
    return {"cfg": cfg, "direction": self.direction, "local": local, "remote": remote,
            "rules": rules}

  def start_preview(self) -> None:
    """Preview: computes and shows the plan (dry run), transferring nothing."""
    self._start_job("preview")

  def start_run(self) -> None:
    """Runs the sync in a thread; the plan is computed first and then again in sync()."""
    self._start_job("run")

  def _start_job(self, mode: str) -> None:
    if self.running or self.closing:
      return
    job = self._snapshot()
    if job is None:
      return
    self.cancel_event.clear()
    self._errors = {}
    self.progress.configure(value=0)
    self.var_progress_text.set("")
    self._log(tr("log_preview_running" if mode == "preview" else "log_run_running"), "ok")
    if self._connected:
      # the open connection does the work: one thread owns the client
      self._sync_mode = mode
      self._submit("run_sync", job={
        "direction": job["direction"], "local": job["local"], "remote": job["remote"],
        "rules": job["rules"], "dry_run": mode == "preview"})
      return
    self._set_running(True)
    thread = threading.Thread(target=self._worker, args=(mode, job), daemon=True)
    thread.start()

  def cancel(self) -> None:
    """Requests a stop: the sync stops when the current file finishes."""
    if self.running:
      self.cancel_event.set()
      self.worker.cancel_event.set()
      self._log(tr("log_cancel_requested"), "warn")

  def _on_run_key(self, _event=None) -> str:
    if not self.running:
      self.start_run()
    return "break"

  def _on_cancel_key(self, _event=None) -> None:
    self.cancel()

  # -- worker thread: never widgets, only the queue -----------------------------

  def _worker(self, mode: str, job: dict) -> None:
    put = self.queue.put
    try:
      with connect(job["cfg"]) as client:
        plan = client.plan_sync(job["direction"], job["local"], job["remote"], job["rules"])
        put(("plan", plan))
        if mode == "preview":
          put(("done_preview", plan))
          return
        todo = sum(1 for a in plan if a.kind in ("upload", "download"))
        state = {"done_files": 0, "counted": set()}

        def on_progress(action, done, total):
          if action.kind not in ("upload", "download"):
            return
          if total and done >= total and action.rel_path not in state["counted"]:
            state["counted"].add(action.rel_path)
            state["done_files"] += 1
          frac = (done / total) if total else 1.0
          put(("progress", state["done_files"], todo, frac if done < total else 0.0))

        result = client.sync(job["direction"], job["local"], job["remote"], job["rules"],
                             on_progress=on_progress, cancel=self.cancel_event)
        put(("done_run", result, todo))
    except FtpError as exc:
      put(("fail", str(exc) or exc.__class__.__name__))
    except (OSError, ValueError) as exc:
      put(("fail", str(exc) or exc.__class__.__name__))
    except Exception as exc:  # last safety net: never a traceback in the interface
      put(("fail_unexpected", exc.__class__.__name__))

  # -- main thread: queue draining -----------------------------------

  def _drain_queue(self) -> None:
    try:
      while True:
        try:
          msg = self.queue.get_nowait()
        except queue.Empty:
          break
        self._safe_handle(msg)
    finally:
      try:
        self.root.after(100, self._drain_queue)
      except tk.TclError:
        pass

  def _safe_handle(self, msg) -> None:
    """Handles a message; an error goes to the log and does not stop the loop."""
    try:
      self._handle(msg)
    except Exception as exc:  # no traceback in the interface
      try:
        self._log(tr("log_internal_error", name=exc.__class__.__name__), "err")
      except tk.TclError:
        pass
      # a failed end-of-job message must not leave the buttons locked
      if isinstance(msg, tuple) and msg and msg[0] in ("done_preview", "done_run", "fail", "fail_unexpected"):
        try:
          self._set_running(False)
        except tk.TclError:
          pass

  # -- window closing ---------------------------------------------------

  def _on_close(self) -> None:
    """Closes at once if there is no job; otherwise cancels and waits for the worker (max ~5 s)."""
    self.translator.unsubscribe(self._relabel)
    self.save_state()
    if self._dialog is not None:
      # an open confirmation counts as "cancel"; the flow that opened it must not go on
      self.closing = True
      self._dialog.cancel()
    if not self.running:
      self._finish_close()
      return
    self.closing = True
    self.cancel_event.set()
    self.worker.cancel_event.set()
    for btn in self._job_buttons:
      btn.configure(state="disabled")
    self.btn_cancel.configure(state="disabled")
    self._wait_close(50)

  def _finish_close(self) -> None:
    """Ends the connection thread (it closes its own client), then the window."""
    self.worker.stop()
    self.root.destroy()

  def _wait_close(self, ticks_left: int) -> None:
    # the running flag is cleared in the main thread when the end message arrives
    self.root.after(100, lambda: self._close_tick(ticks_left))

  def _close_tick(self, ticks_left: int) -> None:
    if not self.running or ticks_left <= 0:
      self._finish_close()
    else:
      self._wait_close(ticks_left - 1)

  def _handle(self, msg: tuple) -> None:
    kind = msg[0]
    if kind == "w":
      self._handle_worker(msg[1:])
    elif kind == "plan":
      self._plan = list(msg[1])
      self._refill_plan()
    elif kind == "progress":
      _, files_done, todo, frac = msg
      if todo:
        self.progress.configure(value=min(100.0, (files_done + frac) / todo * 100.0))
        self.var_progress_text.set("%d / %d" % (files_done, todo))
    elif kind == "done_preview":
      self._report_preview(msg[1])
      self._set_running(False)
    elif kind == "done_run":
      self._report_run(msg[1], msg[2])
      self._set_running(False)
    elif kind == "fail":
      self._log(tr("error_prefix") + str(msg[1]), "err")
      self._set_running(False)
    elif kind == "fail_unexpected":
      self._log(tr("error_prefix") + tr("log_unexpected", name=msg[1]), "err")
      self._set_running(False)


def main() -> None:
  """Starts the window."""
  root = tk.Tk()
  App(root)
  root.mainloop()


if __name__ == "__main__":
  main()
