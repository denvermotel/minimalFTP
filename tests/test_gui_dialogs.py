"""Tests for the overwrite confirmation windows and the transfers that use them."""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minimalftp_gui as gui  # noqa: E402
from minimalftp import Action, RemoteEntry  # noqa: E402

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def english():
  before = gui.translator.lang
  gui.translator.set_language("en")
  yield
  gui.translator.set_language(before)


def _file(size, mtime=T0):
  return RemoteEntry("f.txt", False, size, mtime)


def _row(local=None, remote=None):
  return gui.build_conflict("f.txt", local or _file(84211, T0 + timedelta(hours=1)),
                            remote or _file(2048, T0))


# -- ConflictDialog -------------------------------------------------------------


def test_dialog_default_is_skip_and_esc_skips(tk_window):
  dialog = gui.ConflictDialog(tk_window, _row(), 1, 1)
  assert dialog.result == ("skip", False)
  dialog._on_escape()
  assert dialog.result == ("skip", False)
  assert not dialog.winfo_exists()


def test_dialog_closing_with_the_x_cancels(tk_window):
  dialog = gui.ConflictDialog(tk_window, _row(), 1, 1)
  dialog.cancel()
  assert dialog.result == ("cancel", False)
  assert not dialog.winfo_exists()


def test_dialog_buttons_choose_overwrite_and_skip(tk_window):
  dialog = gui.ConflictDialog(tk_window, _row(), 1, 1)
  dialog.btn_overwrite.invoke()
  assert dialog.result == ("overwrite", False)
  dialog = gui.ConflictDialog(tk_window, _row(), 1, 1)
  dialog.btn_skip.invoke()
  assert dialog.result == ("skip", False)


def test_dialog_keys_return_skips_and_letter_overwrites(tk_window):
  dialog = gui.ConflictDialog(tk_window, _row(), 1, 1)
  assert dialog.bind("<Return>") and dialog.bind("<Escape>") and dialog.bind("<Key-o>")
  dialog._on_overwrite_key()
  assert dialog.result == ("overwrite", False)


def test_dialog_shows_both_sizes_and_dates(tk_window):
  row = _row()
  dialog = gui.ConflictDialog(tk_window, row, 1, 1)
  texts = gui.conflict_texts(row)
  assert texts["local_size"] in str(dialog.cells["local_size"].cget("text"))
  assert texts["remote_size"] in str(dialog.cells["remote_size"].cget("text"))
  assert texts["local_when"] in str(dialog.cells["local_when"].cget("text"))
  assert texts["remote_when"] in str(dialog.cells["remote_when"].cget("text"))
  assert str(dialog.cells["verdict"].cget("text")) == texts["verdict"]
  assert str(dialog.cells["name"].cget("text")) == "f.txt"
  dialog.cancel()


def test_dialog_marks_newer_side(tk_window):
  dialog = gui.ConflictDialog(tk_window, _row(), 1, 1)   # local is one hour newer
  local, remote = dialog.cells["local_when"], dialog.cells["remote_when"]
  assert str(local.cget("style")) == "Newer.TLabel" and "newer" in str(local.cget("text"))
  assert str(remote.cget("style")) != "Newer.TLabel" and "newer" not in str(remote.cget("text"))
  dialog.cancel()
  dialog = gui.ConflictDialog(
    tk_window, _row(local=_file(5, T0), remote=_file(5, T0 + timedelta(hours=2))), 1, 1)
  assert str(dialog.cells["remote_when"].cget("style")) == "Newer.TLabel"
  assert str(dialog.cells["local_when"].cget("style")) != "Newer.TLabel"
  dialog.cancel()
  dialog = gui.ConflictDialog(tk_window, _row(local=_file(5, T0), remote=_file(5, T0)), 1, 1)
  for key in ("local_when", "remote_when"):
    assert str(dialog.cells[key].cget("style")) != "Newer.TLabel"
  dialog.cancel()


def test_apply_to_all_checkbox_only_when_multiple(tk_window):
  single = gui.ConflictDialog(tk_window, _row(), 1, 1)
  assert single.chk_all is None
  single.cancel()
  many = gui.ConflictDialog(tk_window, _row(), 1, 3)
  assert many.chk_all is not None
  many.var_all.set(True)
  many.btn_overwrite.invoke()
  assert many.result == ("overwrite", True)


def test_dialog_texts_follow_language(tk_window):
  gui.translator.set_language("it")
  dialog = gui.ConflictDialog(tk_window, _row(), 1, 2)
  assert str(dialog.btn_overwrite.cget("text")) == "Sovrascrivi"
  assert str(dialog.btn_skip.cget("text")) == "Salta"
  assert str(dialog.btn_cancel.cget("text")) == "Annulla tutto"
  assert "Applica a tutti" in str(dialog.chk_all.cget("text"))
  dialog.cancel()


# -- DirConflictDialog ----------------------------------------------------------


def _dir_actions():
  return [Action("upload", "proj/a.txt", "conflict-dest-newer", 10),
          Action("upload", "proj/sub/b.txt", "size-differs", 20)]


def test_dir_dialog_lists_every_difference(tk_window):
  rows = {"proj/a.txt": gui.build_conflict("proj/a.txt", _file(10, T0 + timedelta(hours=1)),
                                           _file(99, T0))}
  dialog = gui.DirConflictDialog(tk_window, _dir_actions(), "/loc", "/srv", rows)
  values = [dialog.table.item(i, "values") for i in dialog.table.get_children()]
  assert [v[0] for v in values] == ["proj/a.txt", "proj/sub/b.txt"]
  assert "10" in values[0][1] and "99" in values[0][3]
  assert values[1][1] == "" and values[1][3] == ""     # no detail for the second one
  assert dialog.result == "skip"
  dialog.cancel()


def test_dir_dialog_results(tk_window):
  for button, expected in (("btn_overwrite", "overwrite"), ("btn_skip", "skip"),
                           ("btn_cancel", "cancel")):
    dialog = gui.DirConflictDialog(tk_window, _dir_actions(), "/loc", "/srv")
    getattr(dialog, button).invoke()
    assert dialog.result == expected
  dialog = gui.DirConflictDialog(tk_window, _dir_actions(), "/loc", "/srv")
  dialog._on_escape()
  assert dialog.result == "skip"
  dialog = gui.DirConflictDialog(tk_window, _dir_actions(), "/loc", "/srv")
  dialog.cancel()
  assert dialog.result == "cancel"


# -- transfers with confirmation, against a real server --------------------------


@pytest.fixture
def app(tmp_path, tk_window, ftp_server):
  local = tmp_path / "local"
  local.mkdir()
  tk_window.geometry("960x720+0+0")
  a = gui.App(tk_window, profiles_path=tmp_path / "p.json", language="en",
              state_path=tmp_path / "ui.json")
  a.test_local = local
  a.fill(host=ftp_server.host, port=ftp_server.port, user=ftp_server.user,
         password=ftp_server.password, local_dir=str(local), remote_dir="/")
  a.connect_clicked()
  _pump(a, lambda: a._connected and not a.running and a._remote_cwd == "/")
  a.asked = []   # what the scripted answers were asked
  yield a
  a.worker.stop()


def _pump(app, condition, timeout=10.0):
  end = time.time() + timeout
  while time.time() < end:
    app.root.update()
    if condition():
      return
    time.sleep(0.02)
  raise AssertionError("timeout; log: " + app.log.get("1.0", "end"))


def _idle(app):
  _pump(app, lambda: not app.running and not app._pending_reload
        and app._deferred is None and app._pending_transfer is None)


def _select(app, name, side="local"):
  tree = app.local_tree if side == "local" else app.remote_tree
  for iid in tree.get_children():
    if tree.item(iid, "values")[0].rstrip("/") == name:
      tree.selection_set(iid)
      return
  raise AssertionError(name)


def _refresh(app, side):
  app.reload(side)
  _idle(app)


def _answer_files(app, *decisions):
  """Scripts the answers of the file conflict window."""
  queue = list(decisions)

  def ask(row, index, total):
    app.asked.append((row.name, index, total))
    return queue.pop(0)
  app._ask_file_conflict = ask


def _answer_dirs(app, decision):
  def ask(actions, rows):
    app.asked.append(("dirs", [a.rel_path for a in actions]))
    return decision
  app._ask_dir_conflict = ask


def _no_dialogs(app):
  def fail(*args):
    raise AssertionError("no window expected")
  app._ask_file_conflict = fail
  app._ask_dir_conflict = fail


def _upload(app, name):
  _refresh(app, "local")
  _select(app, name)
  app.upload_selected()
  _idle(app)


def test_upload_with_conflict_skip_leaves_remote_untouched(app, ftp_server):
  (ftp_server.root / "c.txt").write_bytes(b"remote")
  (app.test_local / "c.txt").write_bytes(b"local data")
  _refresh(app, "remote")
  _answer_files(app, ("skip", False))
  _upload(app, "c.txt")
  assert (ftp_server.root / "c.txt").read_bytes() == b"remote"
  assert app.asked == [("c.txt", 1, 1)]
  assert "1 skipped" in app.log.get("1.0", "end")


def test_upload_with_conflict_overwrite_replaces(app, ftp_server):
  (ftp_server.root / "c.txt").write_bytes(b"remote")
  (app.test_local / "c.txt").write_bytes(b"local data")
  _refresh(app, "remote")
  _answer_files(app, ("overwrite", False))
  _upload(app, "c.txt")
  assert (ftp_server.root / "c.txt").read_bytes() == b"local data"


def test_download_with_conflict_overwrite_replaces_local(app, ftp_server):
  (ftp_server.root / "d.txt").write_bytes(b"from server")
  (app.test_local / "d.txt").write_bytes(b"old local")
  _refresh(app, "remote")
  _refresh(app, "local")
  _answer_files(app, ("overwrite", False))
  _select(app, "d.txt", "remote")
  app.download_selected()
  _idle(app)
  assert (app.test_local / "d.txt").read_bytes() == b"from server"


def test_cancel_all_transfers_nothing(app, ftp_server):
  for name in ("a.txt", "b.txt"):
    (ftp_server.root / name).write_bytes(b"remote")
    (app.test_local / name).write_bytes(b"local")
  (app.test_local / "new.txt").write_bytes(b"new")
  _refresh(app, "remote")
  _refresh(app, "local")
  _answer_files(app, ("cancel", False))
  app.local_tree.selection_set(app.local_tree.get_children())
  app.upload_selected()
  _idle(app)
  assert not (ftp_server.root / "new.txt").exists()
  assert (ftp_server.root / "a.txt").read_bytes() == b"remote"
  assert len(app.asked) == 1


def test_apply_to_all_asks_once(app, ftp_server):
  for name in ("a.txt", "b.txt", "c.txt"):
    (ftp_server.root / name).write_bytes(b"remote")
    (app.test_local / name).write_bytes(b"local")
  _refresh(app, "remote")
  _refresh(app, "local")
  _answer_files(app, ("overwrite", True))
  app.local_tree.selection_set(app.local_tree.get_children())
  app.upload_selected()
  _idle(app)
  assert app.asked == [("a.txt", 1, 3)]
  for name in ("a.txt", "b.txt", "c.txt"):
    assert (ftp_server.root / name).read_bytes() == b"local"


def test_no_conflict_means_no_dialog(app, ftp_server):
  (app.test_local / "fresh.txt").write_bytes(b"x")
  _no_dialogs(app)
  _upload(app, "fresh.txt")
  assert (ftp_server.root / "fresh.txt").read_bytes() == b"x"


def test_folder_transfer_without_conflicts_has_no_dialog(app, ftp_server):
  folder = app.test_local / "proj"
  (folder / "sub").mkdir(parents=True)
  (folder / "a.txt").write_bytes(b"a")
  (folder / "sub" / "b.txt").write_bytes(b"b")
  _no_dialogs(app)
  _upload(app, "proj")
  assert (ftp_server.root / "proj" / "sub" / "b.txt").read_bytes() == b"b"


def _folder_with_conflict(app, ftp_server):
  folder = app.test_local / "proj"
  folder.mkdir()
  (folder / "same.txt").write_bytes(b"local version")
  (folder / "extra.txt").write_bytes(b"extra")
  remote = ftp_server.root / "proj"
  remote.mkdir()
  (remote / "same.txt").write_bytes(b"remote")
  _refresh(app, "remote")
  return remote


def test_folder_conflict_overwrite_replaces_files(app, ftp_server):
  remote = _folder_with_conflict(app, ftp_server)
  _answer_dirs(app, "overwrite")
  _upload(app, "proj")
  assert app.asked == [("dirs", ["proj/same.txt"])]
  assert (remote / "same.txt").read_bytes() == b"local version"
  assert (remote / "extra.txt").read_bytes() == b"extra"


def test_folder_conflict_skip_keeps_existing_files(app, ftp_server):
  remote = _folder_with_conflict(app, ftp_server)
  _answer_dirs(app, "skip")
  _upload(app, "proj")
  assert (remote / "same.txt").read_bytes() == b"remote"
  assert (remote / "extra.txt").read_bytes() == b"extra"


def test_folder_conflict_cancel_transfers_nothing(app, ftp_server):
  remote = _folder_with_conflict(app, ftp_server)
  _answer_dirs(app, "cancel")
  _upload(app, "proj")
  assert (remote / "same.txt").read_bytes() == b"remote"
  assert not (remote / "extra.txt").exists()


def test_case_variant_name_triggers_conflict_dialog(app, ftp_server):
  (ftp_server.root / "report.txt").write_bytes(b"remote")
  (app.test_local / "Report.TXT").write_bytes(b"local")
  _refresh(app, "remote")
  _answer_files(app, ("skip", False))
  _upload(app, "Report.TXT")
  assert app.asked and app.asked[0][0] == "Report.TXT"


def test_type_mismatch_is_reported_and_nothing_is_sent(app, ftp_server):
  (ftp_server.root / "clash").mkdir()
  (app.test_local / "clash").write_bytes(b"i am a file")
  _refresh(app, "remote")
  _no_dialogs(app)
  _upload(app, "clash")
  assert "file on one side and a folder" in app.log.get("1.0", "end")
  assert (ftp_server.root / "clash").is_dir()


def test_closing_the_window_cancels_an_open_confirmation(app):
  calls = []
  app._dialog = type("D", (), {"cancel": lambda self: calls.append("cancel")})()
  app._on_close()
  assert calls == ["cancel"] and app.closing
