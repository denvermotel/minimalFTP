"""Tests for the non-graphical part of the GUI (no display required)."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minimalftp import Action  # noqa: E402
import minimalftp_gui as gui  # noqa: E402


@pytest.fixture(autouse=True)
def italian_language():
  """The Italian assertions below need the Italian texts; restores the language afterwards."""
  before = gui.translator.lang
  gui.translator.set_language("it")
  yield
  gui.translator.set_language(before)


def test_profiles_roundtrip_has_no_password(tmp_path):
  path = tmp_path / "sub" / "profiles.json"
  profiles = {"sito": {"host": "h", "port": 21, "user": "u", "password": "segreta"}}
  gui.save_profiles(path, profiles)
  assert "segreta" not in path.read_text(encoding="utf-8")
  loaded = gui.load_profiles(path)
  assert loaded["sito"]["host"] == "h"
  assert "password" not in loaded["sito"]
  # the in-memory original is not altered
  assert profiles["sito"]["password"] == "segreta"


def test_load_missing_file_returns_empty(tmp_path):
  assert gui.load_profiles(tmp_path / "non-esiste.json") == {}


def test_load_corrupt_file_returns_empty(tmp_path):
  path = tmp_path / "p.json"
  path.write_text("{non json", encoding="utf-8")
  assert gui.load_profiles(path) == {}
  path.write_text(json.dumps([1, 2]), encoding="utf-8")
  assert gui.load_profiles(path) == {}


def test_build_rules_parses_days():
  rules = gui.build_rules("7", True, False)
  assert rules.max_age_days == 7
  assert rules.size_differs is True
  assert rules.newer_than_dest is False
  assert gui.build_rules("2.5", True, True).max_age_days == 2.5
  assert gui.build_rules("1", True, True, overwrite_newer=True).overwrite_newer is True


def test_build_rules_blank_days_means_none():
  assert gui.build_rules("", True, True).max_age_days is None
  assert gui.build_rules("   ", True, True).max_age_days is None


def test_build_rules_invalid_days_raises_valueerror():
  with pytest.raises(ValueError, match="giorni"):
    gui.build_rules("abc", True, True)


def test_build_rules_negative_days_raises_valueerror():
  with pytest.raises(ValueError, match="giorni"):
    gui.build_rules("-3", True, True)


def test_describe_action_words():
  assert gui.describe_action(Action("upload", "a.txt", "missing", 10))[:2] == ("SU", "a.txt")
  assert gui.describe_action(Action("download", "b.txt", "newer", 10))[0] == "GIU"
  assert gui.describe_action(Action("skip", "c.txt", "unchanged", 10))[0] == "salta"
  assert gui.describe_action(Action("mkdir", ".", "missing", 0))[0] == "dir"
  word, rel, note = gui.describe_action(
    Action("skip", "d.txt", "conflict-dest-newer", 5))
  assert (word, rel) == ("CONFL", "d.txt")
  assert note
  assert gui.action_tag(Action("skip", "d.txt", "conflict-dest-newer", 5)) == "conflict"
  assert gui.action_tag(Action("skip", "c.txt", "unchanged", 1)) == "skip"
  assert gui.action_tag(Action("upload", "a", "missing", 1)) == "transfer"
  assert gui.action_tag(Action("mkdir", ".", "missing", 0)) == "dir"


def test_describe_action_has_no_long_dashes():
  for kind in ("upload", "download", "skip", "mkdir"):
    for reason in ("missing", "newer", "size-differs", "unchanged", "too-old",
                   "excluded", "not-included", "age-unknown", "conflict-dest-newer",
                   "name-case-conflict", "unreadable"):
      text = " ".join(gui.describe_action(Action(kind, "x", reason, 1)))
      assert "\u2014" not in text and "\u2013" not in text


def test_save_profiles_is_atomic_no_temp_left(tmp_path):
  path = tmp_path / "profiles.json"
  gui.save_profiles(path, {"a": {"host": "h"}})
  gui.save_profiles(path, {"b": {"host": "k"}})
  assert sorted(os.listdir(tmp_path)) == ["profiles.json"]
  assert gui.load_profiles(path) == {"b": {"host": "k"}}


def test_default_profiles_path_is_in_home():
  assert gui.default_profiles_path().name == "profiles.json"
  assert gui.default_profiles_path().parent.name == ".minimalftp"


def test_app_smoke_creates_and_destroys_window(tmp_path, tk_window):
  root = tk_window
  try:
    app = gui.App(root, profiles_path=tmp_path / "profiles.json", language="it")
    root.update()
  finally:
    root.destroy()


def test_rail_colors_follow_direction():
  assert gui.rail_colors("up") == (gui.OTTANIO, gui.FERRO)
  assert gui.rail_colors("down") == (gui.FERRO, gui.OTTANIO)


def _make_app(tmp_path, root):
  return root, gui.App(root, profiles_path=tmp_path / "profiles.json", language="it")


def test_app_rail_matches_direction(tmp_path, tk_window):
  root, app = _make_app(tmp_path, tk_window)
  try:
    assert (str(app.rail_left.cget("bg")), str(app.rail_right.cget("bg"))) == (
      gui.OTTANIO, gui.FERRO)
    app.toggle_direction()
    assert (str(app.rail_left.cget("bg")), str(app.rail_right.cget("bg"))) == (
      gui.FERRO, gui.OTTANIO)
  finally:
    root.destroy()


def test_malformed_queue_message_does_not_block_later_ones(tmp_path, tk_window):
  root, app = _make_app(tmp_path, tk_window)
  try:
    app._set_running(True)
    app.queue.put(("progress", 1))        # malformed
    app.queue.put(())                      # empty
    app.queue.put(("done_run", None, 0))  # broken end of job: must still unlock
    app.queue.put(("fail", "prova"))
    app._drain_queue()
    assert app.running is False
    text = app.log.get("1.0", "end")
    assert "Errore interno" in text and "Errore: prova" in text
    assert "Traceback" not in text
  finally:
    root.destroy()


def test_close_without_job_destroys_immediately(tmp_path, tk_window):
  root, app = _make_app(tmp_path, tk_window)
  app._on_close()
  # The window is a Toplevel of the shared root, so a destroyed one reports 0 instead of raising
  assert not root.winfo_exists()


def test_close_with_job_cancels_and_waits(tmp_path, tk_window):
  tk = pytest.importorskip("tkinter")
  root, app = _make_app(tmp_path, tk_window)
  app._set_running(True)
  app._on_close()
  assert app.cancel_event.is_set() and app.closing
  assert root.winfo_exists()
  # end of job: at the next check the window is destroyed
  app.running = False
  deadline = time.time() + 2
  destroyed = False
  while time.time() < deadline and not destroyed:
    try:
      root.update()
    except tk.TclError:
      pass
    destroyed = not root.winfo_exists()
    time.sleep(0.05)
  assert destroyed


def test_validate_fields_ok_and_default_port():
  assert gui.validate_fields(" h ", "", " /loc ", " / ") == ("h", 21, "/loc", "/")
  assert gui.validate_fields("h", "990", "l", "/www") == ("h", 990, "l", "/www")


def test_validate_fields_requires_remote_folder():
  with pytest.raises(ValueError, match="cartella remota"):
    gui.validate_fields("h", "21", "l", "   ")


def test_validate_fields_port_range():
  for bad in ("0", "65536", "99999999999", "-1", "abc", "21.5"):
    with pytest.raises(ValueError, match="1 e 65535"):
      gui.validate_fields("h", bad, "l", "/")
  assert gui.validate_fields("h", "65535", "l", "/")[1] == 65535


def test_validate_fields_requires_host_and_local():
  with pytest.raises(ValueError, match="host"):
    gui.validate_fields("", "21", "l", "/")
  with pytest.raises(ValueError, match="locale"):
    gui.validate_fields("h", "21", "", "/")


def test_empty_remote_does_not_start_job(tmp_path, tk_window):
  root, app = _make_app(tmp_path, tk_window)
  try:
    app.fill(host="127.0.0.1", local_dir=str(tmp_path), remote_dir="")
    app.start_preview()
    assert app.running is False
    log = app.log.get("1.0", "end")
    assert "cartella remota" in log
    app.var_remote.set("/")
    app.var_port.set("70000")
    app.start_preview()
    assert app.running is False
    assert "65535" in app.log.get("1.0", "end")
  finally:
    root.destroy()


def test_english_messages_and_words():
  gui.translator.set_language("en")
  with pytest.raises(ValueError, match="days"):
    gui.build_rules("abc", True, True)
  with pytest.raises(ValueError, match="between 1 and 65535"):
    gui.validate_fields("h", "0", "l", "/")
  with pytest.raises(ValueError, match="remote folder"):
    gui.validate_fields("h", "21", "l", "")
  assert gui.describe_action(Action("upload", "a", "missing", 1))[0] == "UP"
  assert gui.describe_action(Action("download", "a", "missing", 1))[0] == "DOWN"
  assert gui.describe_action(Action("skip", "a", "unchanged", 1)) == ("skip", "a", "unchanged")
  assert gui.describe_action(Action("skip", "a", "conflict-dest-newer", 1))[0] == "CONFLICT"


# -- palette -------------------------------------------------------------------

WHITE = "#FFFFFF"


def test_contrast_ratio_known_values():
  assert gui.contrast_ratio("#000000", WHITE) == pytest.approx(21.0)
  assert gui.contrast_ratio(WHITE, "#000000") == pytest.approx(21.0)
  assert gui.contrast_ratio("#336699", "#336699") == pytest.approx(1.0)


def test_text_tokens_meet_aa():
  pairs = [
    (gui.INCHIOSTRO, gui.NEBBIA), (gui.INCHIOSTRO, gui.GHIACCIO),
    (gui.INCHIOSTRO_2, gui.NEBBIA),
    (gui.GRAFITE, gui.GHIACCIO), (gui.GRAFITE, gui.GHIACCIO_SCURO),
    (gui.OTTANIO, gui.GHIACCIO), (gui.OTTANIO, gui.GHIACCIO_SCURO),
    (WHITE, gui.OTTANIO), (WHITE, gui.OTTANIO_SCURO),
    (gui.GHIACCIO, gui.OTTANIO), (gui.GHIACCIO, gui.OTTANIO_SCURO),
    (gui.INCHIOSTRO, gui.SELEZIONE),
    (gui.AMBRA, gui.AMBRA_CHIARO), (gui.AMBRA, gui.GHIACCIO),
    (gui.MATTONE, gui.MATTONE_CHIARO), (gui.MATTONE, gui.GHIACCIO),
  ]
  for fg, bg in pairs:
    assert gui.contrast_ratio(fg, bg) >= 4.5, (fg, bg)


def test_control_border_meets_3_to_1():
  assert gui.contrast_ratio(gui.FERRO, gui.NEBBIA) >= 3.0


def test_no_stray_hex_colors():
  import re
  source = (Path(__file__).resolve().parent.parent / "minimalftp_gui.py").read_text(
    encoding="utf-8")
  token_line = re.compile(r'^[A-Z][A-Z0-9_]* = "#[0-9A-Fa-f]{6}"')
  stray = [line for line in source.splitlines()
           if re.search(r"#[0-9A-Fa-f]{6}\b", line) and not token_line.match(line)]
  assert stray == []


def test_old_tokens_are_gone():
  assert not hasattr(gui, "FERRO_TESTO") and not hasattr(gui, "INCHIOSTRO_70")


def test_action_tag_colors_use_tokens(tmp_path, tk_window):
  app = gui.App(tk_window, profiles_path=tmp_path / "profiles.json", language="en")
  tree = app.tree
  assert str(tree.tag_configure("transfer", "foreground")) == gui.OTTANIO
  assert str(tree.tag_configure("dir", "foreground")) == gui.INCHIOSTRO
  assert str(tree.tag_configure("skip", "foreground")) == gui.GRAFITE
  assert str(tree.tag_configure("conflict", "foreground")) == gui.AMBRA
  assert str(tree.tag_configure("conflict", "background")) == gui.AMBRA_CHIARO
  assert str(tree.tag_configure("err", "foreground")) == gui.MATTONE
  assert str(tree.tag_configure("err", "background")) == gui.MATTONE_CHIARO
  assert str(tree.tag_configure("alt", "background")) == gui.GHIACCIO_SCURO


def test_plan_rows_alternate_and_keep_status_background(tmp_path, tk_window):
  app = gui.App(tk_window, profiles_path=tmp_path / "profiles.json", language="en")
  app._plan = [Action("upload", "a", "missing", 1), Action("skip", "b", "conflict-dest-newer", 1),
               Action("upload", "c", "missing", 1), Action("upload", "d", "missing", 1)]
  app._refill_plan()
  tags = [app.tree.item(i, "tags") for i in app.tree.get_children()]
  # odd rows are tinted, except the conflict row, which keeps its own background
  assert ["alt" in t for t in tags] == [False, False, False, True]
  assert "conflict" in tags[1]


def test_style_uses_selection_token(tmp_path, tk_window):
  from tkinter import ttk
  gui.App(tk_window, profiles_path=tmp_path / "profiles.json", language="en")
  style = ttk.Style(tk_window)
  selected = dict(style.map("Treeview", "background"))
  assert selected["selected"] == gui.SELEZIONE
  assert dict(style.map("Primary.TButton", "background"))["active"] == gui.OTTANIO_SCURO
