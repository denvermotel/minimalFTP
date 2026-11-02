"""Tests for the two-pane window: layout, local pane, buttons, sync section, sash."""
import os
import queue
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minimalftp_gui as gui  # noqa: E402
from minimalftp import RemoteEntry  # noqa: E402


@pytest.fixture(autouse=True)
def english():
  before = gui.translator.lang
  yield
  gui.translator.set_language(before)


@pytest.fixture
def app(tmp_path, tk_window):
  """An App whose local pane starts in a temporary folder with two files and a folder."""
  local = tmp_path / "local"
  (local / "sub").mkdir(parents=True)
  (local / "b.txt").write_bytes(b"x" * 10)
  (local / "A.txt").write_bytes(b"y" * 2048)
  tk_window.geometry("960x720+0+0")
  a = gui.App(tk_window, profiles_path=tmp_path / "p.json", language="en",
              state_path=tmp_path / "ui.json")
  a.local_path_var.set(str(local))
  a.go_to_path("local")
  tk_window.update()
  a.test_local = local
  return a


def _names(app, side="local"):
  tree = app.local_tree if side == "local" else app.remote_tree
  return [tree.item(i, "values")[0] for i in tree.get_children()]


def _select(app, name, side="local"):
  tree = app.local_tree if side == "local" else app.remote_tree
  for iid in tree.get_children():
    if tree.item(iid, "values")[0].rstrip("/") == name:
      tree.selection_set(iid)
      return iid
  raise AssertionError(name)


def _connected(app, entries=(), path="/"):
  """Simulates the worker saying it is connected and listed `path`."""
  app._handle(("w", "connected"))
  app._pending_reload.clear()   # these tests have no real worker to list the folder
  app._handle(("w", "remote_listing", path, list(entries)))
  app._handle(("w", "busy", False))


def test_panes_exist_and_start_empty_remote_disabled(app):
  assert str(app.local_tree.winfo_class()) == "Treeview"
  assert app.remote_tree.get_children() == ()
  assert str(app.btn_upload.cget("state")) == "disabled"
  assert str(app.btn_download.cget("state")) == "disabled"
  assert str(app.btn_mkdir_remote.cget("state")) == "disabled"
  assert str(app.btn_disconnect.cget("state")) == "disabled"
  assert str(app.btn_connect.cget("state")) == "normal"
  assert str(app.btn_mkdir_local.cget("state")) == "normal"
  assert "not connected" in app.status_var.get().lower()


def test_local_pane_lists_directory(app):
  assert _names(app) == ["..", "sub/", "A.txt", "b.txt"]
  rows = {app.local_tree.item(i, "values")[0]: app.local_tree.item(i, "values")
          for i in app.local_tree.get_children()}
  assert rows["b.txt"][1] == "10 B" and rows["A.txt"][1] == "2.0 KB"
  assert rows["b.txt"][2] and rows["sub/"][2]
  assert app.local_path_var.get() == str(app.test_local)


def test_double_click_enters_directory_and_dotdot_goes_up(app):
  _select(app, "sub")
  app.open_selected("local")
  assert app.local_path_var.get() == str(app.test_local / "sub")
  assert _names(app) == [".."]
  _select(app, "..")
  app.open_selected("local")
  assert app.local_path_var.get() == str(app.test_local)
  assert "b.txt" in _names(app)


def test_open_selected_on_file_does_nothing(app):
  _select(app, "b.txt")
  app.open_selected("local")
  assert app.local_path_var.get() == str(app.test_local)


def test_go_to_path_ignores_missing_folder(app):
  app.local_path_var.set(str(app.test_local / "nope"))
  app.go_to_path("local")
  assert app.local_path_var.get() == str(app.test_local)
  assert "nope" in app.log.get("1.0", "end")


def test_selected_entries_excludes_dotdot(app):
  tree = app.local_tree
  tree.selection_set(tree.get_children())
  names = [e.name for e in app.selected_entries("local")]
  assert names == ["sub", "A.txt", "b.txt"]


def test_buttons_follow_connection_and_busy_state(app):
  _connected(app)
  assert str(app.btn_upload.cget("state")) == "normal"
  assert str(app.btn_download.cget("state")) == "normal"
  assert str(app.btn_mkdir_remote.cget("state")) == "normal"
  assert str(app.btn_connect.cget("state")) == "disabled"
  assert str(app.btn_disconnect.cget("state")) == "normal"
  app._set_running(True)
  for button in (app.btn_upload, app.btn_download, app.btn_mkdir_local,
                 app.btn_mkdir_remote, app.btn_disconnect, app.btn_connect):
    assert str(button.cget("state")) == "disabled"
  app._set_running(False)
  assert str(app.btn_upload.cget("state")) == "normal"
  app._handle(("w", "disconnected", "bye"))
  assert str(app.btn_upload.cget("state")) == "disabled"
  assert app.remote_tree.get_children() == ()
  assert "not connected" in app.status_var.get().lower()


def test_remote_listing_fills_remote_pane(app):
  entries = [RemoteEntry("docs", True, 0, None), RemoteEntry("r.bin", False, 5, None)]
  _connected(app, entries, "/www")
  assert _names(app, "remote") == ["..", "docs/", "r.bin"]
  assert app.remote_path_var.get() == "/www"


def test_connected_message_asks_for_the_remote_listing_after_idle(app):
  sent = []
  app._ensure_worker = lambda: None
  app.worker = type("W", (), {"submit": lambda self, cmd, **kw: sent.append((cmd, kw)),
                              "cancel_event": threading.Event()})()
  app.var_remote.set("/start")
  app._handle(("w", "connected"))
  assert sent == []          # the worker is still finishing the connect command
  app._handle(("w", "busy", False))
  assert sent == [("list_remote", {"path": "/start"})]


def test_upload_and_download_submit_transfer_items(app):
  sent = []
  app._ensure_worker = lambda: None
  app.worker = type("W", (), {"submit": lambda self, cmd, **kw: sent.append((cmd, kw)),
                              "cancel_event": threading.Event()})()
  _connected(app, [RemoteEntry("r.bin", False, 5, None), RemoteEntry("d", True, 0, None)], "/srv")
  _select(app, "b.txt")
  app.upload_selected()
  cmd, kw = sent[-1]
  assert cmd == "transfer" and kw["direction"] == "up"
  assert kw["items"] == [(str(app.test_local / "b.txt"), "/srv/b.txt", False)]
  app._pending_reload.clear()
  app._handle(("w", "busy", False))
  _select(app, "r.bin", "remote")
  app.download_selected()
  cmd, kw = sent[-1]
  assert cmd == "transfer" and kw["direction"] == "down"
  assert kw["items"] == [(str(app.test_local / "r.bin"), "/srv/r.bin", False)]


def test_new_folder_local_creates_and_reloads(app):
  app.new_folder("local", name="fresh")
  assert (app.test_local / "fresh").is_dir()
  assert "fresh/" in _names(app)


@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "a/b", "a\\b", "a\0b"])
def test_new_folder_rejects_unsafe_names(app, bad):
  app.new_folder("local", name=bad)
  assert sorted(p.name for p in app.test_local.iterdir()) == ["A.txt", "b.txt", "sub"]
  assert app.log.get("1.0", "end").strip()


def test_validate_folder_name():
  assert gui.validate_folder_name("  ok name ") == "ok name"
  for bad in ("", " ", ".", "..", "a/b", "a\\b", "a\0b"):
    with pytest.raises(ValueError):
      gui.validate_folder_name(bad)


def test_sync_section_toggle(app):
  assert not app.sync_open and not app.sync_frame.winfo_ismapped()
  app.toggle_sync()
  app.root.update()
  assert app.sync_open and app.sync_frame.winfo_ismapped()
  assert str(app.sync_toggle.cget("text")).startswith("v")
  app.toggle_sync()
  app.root.update()
  assert not app.sync_frame.winfo_ismapped()
  assert str(app.sync_toggle.cget("text")).startswith(">")


def test_f5_reloads_active_pane(app):
  (app.test_local / "new.txt").write_bytes(b"1")
  assert "new.txt" not in _names(app)
  app.local_tree.focus_force()
  app.root.update()
  assert app.root.bind_all("<F5>")
  app._on_f5()
  assert "new.txt" in _names(app)


def test_panes_have_at_least_12_visible_rows_at_default_size(app):
  app.root.update()
  rows = (app.local_tree.winfo_height() - 1) // gui.ROW_HEIGHT
  assert rows >= 12, (rows, app.local_tree.winfo_height(), app.root.winfo_height())


def test_main_split_is_vertical_paned_window(app):
  from tkinter import ttk
  assert isinstance(app.main_split, ttk.Panedwindow)
  assert str(app.main_split.cget("orient")) == "vertical"


def _heights(app):
  app.root.update()
  return app.top_pane.winfo_height(), app.bottom_pane.winfo_height()


def test_panes_area_grows_when_window_grows(app):
  top, bottom = _heights(app)
  app.root.geometry("960x%d" % (app.root.winfo_height() + 200))
  new_top, new_bottom = _heights(app)
  assert new_top - top >= 180
  assert abs(new_bottom - bottom) <= 20


def test_sash_can_be_moved_and_panes_follow(app):
  top, _ = _heights(app)
  app.main_split.sashpos(0, app.main_split.sashpos(0) - 60)
  new_top, _ = _heights(app)
  assert new_top <= top - 50


def test_panes_never_below_5_rows(app):
  app.main_split.sashpos(0, 10)
  app.root.update()
  app.root.update()
  rows = (app.local_tree.winfo_height() - 1) // gui.ROW_HEIGHT
  assert rows >= 5, rows


def test_password_field_is_masked_and_next_to_user(app):
  assert str(app.ent_password.cget("show")) == "*"
  user, password = app.ent_user.grid_info(), app.ent_password.grid_info()
  assert user["row"] == password["row"] and int(password["column"]) == int(user["column"]) + 1


def test_password_is_never_in_profile_or_status(app):
  app.var_host.set("h")
  app.var_password.set("S3cret-pw")
  assert "S3cret-pw" not in str(app._collect_profile())
  assert "S3cret-pw" not in app.status_var.get()


def test_close_stops_the_worker(app):
  calls = []
  app.worker = type("W", (), {"stop": lambda self, timeout=5.0: calls.append("stop"),
                              "cancel_event": threading.Event()})()
  app._on_close()
  assert calls == ["stop"]


def test_language_switch_relabels_panes(app):
  app.set_language("it")
  assert str(app.btn_upload.cget("text")) == "Carica >"
  assert str(app.btn_download.cget("text")) == "< Scarica"
  assert app.local_tree.heading("name", "text") == "nome"
  assert "non connesso" in app.status_var.get().lower()


def _pump(app, condition, timeout=10.0):
  import time
  end = time.time() + timeout
  while time.time() < end:
    app.root.update()
    if condition():
      return
    time.sleep(0.02)
  raise AssertionError("timeout; log: " + app.log.get("1.0", "end"))


def test_full_flow_with_a_real_server(app, ftp_server):
  try:
    app.fill(host=ftp_server.host, port=ftp_server.port, user=ftp_server.user,
             password=ftp_server.password, local_dir=str(app.test_local), remote_dir="/")
    app.connect_clicked()
    _pump(app, lambda: app._connected and not app.running and app._remote_cwd == "/")
    assert "connected" in app.status_var.get().lower()
    # upload through the panes; a second upload of the same name is skipped, not replaced
    _select(app, "b.txt")
    app.upload_selected()
    _pump(app, lambda: not app.running and "b.txt" in _names(app, "remote"))
    assert (ftp_server.root / "b.txt").read_bytes() == b"x" * 10
    (app.test_local / "b.txt").write_bytes(b"changed")
    _select(app, "b.txt")
    asked = []
    app._ask_file_conflict = lambda row, index, total: asked.append(row.name) or ("skip", False)
    app.upload_selected()
    assert asked == ["b.txt"]
    _pump(app, lambda: not app.running and "1 skipped" in app.log.get("1.0", "end"))
    assert (ftp_server.root / "b.txt").read_bytes() == b"x" * 10
    # remote folder, navigation and download
    app.new_folder("remote", name="made")
    _pump(app, lambda: not app.running and "made/" in _names(app, "remote"))
    (ftp_server.root / "made" / "inner.txt").write_bytes(b"inner")
    _select(app, "made", "remote")
    app.open_selected("remote")
    _pump(app, lambda: not app.running and app._remote_cwd == "/made")
    assert _names(app, "remote") == ["..", "inner.txt"]
    _select(app, "inner.txt", "remote")
    app.download_selected()
    _pump(app, lambda: not app.running and (app.test_local / "inner.txt").exists())
    assert (app.test_local / "inner.txt").read_bytes() == b"inner"
    # sync over the open connection: preview fills the plan and transfers nothing
    app.var_remote.set("/")
    app.start_preview()
    _pump(app, lambda: not app.running and "Preview:" in app.log.get("1.0", "end"))
    assert app.tree.get_children()
    app.disconnect_clicked()
    _pump(app, lambda: not app._connected and not app.running)
    assert app.remote_tree.get_children() == ()
    assert ftp_server.password not in app.log.get("1.0", "end")
  finally:
    app.worker.stop()
