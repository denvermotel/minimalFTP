"""Tests for the remembered window state (size, position, sash, language)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minimalftp_gui as gui  # noqa: E402

SCREEN = (0, 0, 1920, 1080)


@pytest.fixture(autouse=True)
def restore_language():
  before = gui.translator.lang
  yield
  gui.translator.set_language(before)


def _visible_width(geo, screen):
  sx, _, sw, _ = screen
  return min(geo["x"] + geo["width"], sx + sw) - max(geo["x"], sx)


def _visible_top(geo, screen):
  _, sy, _, sh = screen
  return min(geo["y"] + geo["height"], sy + sh) - max(geo["y"], sy)


def test_parse_geometry_handles_negative_origin():
  assert gui.parse_geometry("960x720+10+20") == (960, 720, 10, 20)
  assert gui.parse_geometry("960x720+-1800+100") == (960, 720, -1800, 100)
  assert gui.parse_geometry("960x720-5-7") == (960, 720, -5, -7)
  assert gui.parse_geometry("garbage") is None


def test_ui_state_path_is_under_home(monkeypatch):
  monkeypatch.undo()  # the conftest redirects it for the other tests
  path = gui.ui_state_path()
  assert path.name == "ui.json" and path.parent.name == ".minimalftp"


def test_load_missing_or_corrupt_returns_empty(tmp_path):
  assert gui.load_ui_state(tmp_path / "no.json") == {}
  bad = tmp_path / "bad.json"
  bad.write_text("{not json", encoding="utf-8")
  assert gui.load_ui_state(bad) == {}
  bad.write_text("[1, 2]", encoding="utf-8")
  assert gui.load_ui_state(bad) == {}


def test_load_keeps_unknown_keys(tmp_path):
  path = tmp_path / "ui.json"
  path.write_text(json.dumps({"width": 900, "future": [1]}), encoding="utf-8")
  assert gui.load_ui_state(path) == {"width": 900, "future": [1]}


def test_save_load_roundtrip_and_atomic(tmp_path):
  path = tmp_path / "sub" / "ui.json"
  state = {"width": 1000, "height": 700, "x": -50, "y": 20, "sash": 0.4, "language": "it"}
  gui.save_ui_state(path, state)
  assert gui.load_ui_state(path) == state
  gui.save_ui_state(path, {"width": 800})
  assert gui.load_ui_state(path) == {"width": 800}
  assert [p.name for p in path.parent.iterdir()] == ["ui.json"]


def test_clamp_default_size_when_missing():
  geo = gui.clamp_geometry({}, SCREEN)
  assert (geo["width"], geo["height"]) == (960, 720)
  assert geo["x"] is None and geo["y"] is None


def test_clamp_ignores_invalid_values():
  geo = gui.clamp_geometry(
    {"width": "wide", "height": True, "x": float("nan"), "y": None}, SCREEN)
  assert (geo["width"], geo["height"]) == (960, 720)
  assert geo["x"] is None and geo["y"] is None


def test_clamp_needs_both_coordinates():
  geo = gui.clamp_geometry({"x": 100}, SCREEN)
  assert geo["x"] is None and geo["y"] is None


def test_clamp_shrinks_oversized_window():
  geo = gui.clamp_geometry({"width": 5000, "height": 4000, "x": 0, "y": 0}, (0, 0, 1280, 800))
  assert (geo["width"], geo["height"]) == (1280, 800)
  assert (geo["x"], geo["y"]) == (0, 0)


def test_clamp_moves_offscreen_window_back():
  screen = (0, 0, 1280, 800)
  geo = gui.clamp_geometry({"width": 960, "height": 720, "x": 5000, "y": 3000}, screen)
  assert _visible_width(geo, screen) >= 120 and _visible_top(geo, screen) >= 120
  geo = gui.clamp_geometry({"width": 960, "height": 720, "x": -5000, "y": -400}, screen)
  assert _visible_width(geo, screen) >= 120 and _visible_top(geo, screen) >= 120
  assert geo["y"] >= 0


def test_clamp_keeps_a_window_that_is_already_visible():
  geo = gui.clamp_geometry({"width": 1000, "height": 700, "x": 300, "y": 40}, SCREEN)
  assert (geo["width"], geo["height"], geo["x"], geo["y"]) == (1000, 700, 300, 40)


def test_clamp_accepts_negative_origin_secondary_monitor():
  screen = (-1920, 0, 3840, 1080)
  geo = gui.clamp_geometry({"width": 960, "height": 720, "x": -1800, "y": 100}, screen)
  assert (geo["x"], geo["y"]) == (-1800, 100)
  geo = gui.clamp_geometry({"width": 960, "height": 720, "x": -9000, "y": 100}, screen)
  assert _visible_width(geo, screen) >= 120


def test_clamp_respects_min_size():
  geo = gui.clamp_geometry({"width": 100, "height": 100}, SCREEN)
  assert (geo["width"], geo["height"]) == (760, 560)
  geo = gui.clamp_geometry({"width": 100, "height": 100}, SCREEN, min_size=(800, 600))
  assert (geo["width"], geo["height"]) == (800, 600)


def test_sash_fraction_is_clamped_0_1():
  assert gui.clamp_sash(0.5) == 0.5
  assert gui.clamp_sash(0.0) == 0.15
  assert gui.clamp_sash(7) == 0.9
  assert gui.clamp_sash(-1) == 0.15
  assert gui.clamp_sash(0.05, lo=0.0, hi=1.0) == 0.05
  assert 0.15 <= gui.clamp_sash("x") <= 0.9
  assert 0.15 <= gui.clamp_sash(float("nan")) <= 0.9


def test_restore_applies_geometry_and_language(tmp_path, tk_window):
  state_path = tmp_path / "ui.json"
  gui.save_ui_state(state_path, {"width": 900, "height": 700, "x": 40, "y": 30, "language": "it"})
  gui.translator.set_language("en")
  app = gui.App(tk_window, profiles_path=tmp_path / "p.json", state_path=state_path)
  tk_window.update()
  assert gui.translator.lang == "it"
  assert tk_window.title() == gui.STRINGS["window_title"]["it"]
  assert tk_window.winfo_width() >= 760
  assert app.state_path == state_path


def test_explicit_language_wins_over_saved_one(tmp_path, tk_window):
  state_path = tmp_path / "ui.json"
  gui.save_ui_state(state_path, {"language": "it"})
  gui.App(tk_window, profiles_path=tmp_path / "p.json", language="en", state_path=state_path)
  assert gui.translator.lang == "en"


def test_close_saves_state(tmp_path, tk_window):
  state_path = tmp_path / "ui.json"
  app = gui.App(tk_window, profiles_path=tmp_path / "p.json", language="it",
                state_path=state_path)
  tk_window.update()
  app._on_close()
  saved = gui.load_ui_state(state_path)
  assert saved["language"] == "it"
  assert saved["width"] >= 760 and saved["height"] >= 560
  assert "x" in saved and "y" in saved


def test_close_survives_unwritable_state(tmp_path, tk_window):
  blocker = tmp_path / "file"
  blocker.write_text("x", encoding="utf-8")
  app = gui.App(tk_window, profiles_path=tmp_path / "p.json", language="it",
                state_path=blocker / "ui.json")
  app._on_close()
  assert not tk_window.winfo_exists()


def test_saved_state_keeps_unknown_keys_and_sash(tmp_path, tk_window):
  state_path = tmp_path / "ui.json"
  gui.save_ui_state(state_path, {"sash": 0.4, "future": 1})
  app = gui.App(tk_window, profiles_path=tmp_path / "p.json", language="it",
                state_path=state_path)
  app._on_close()
  saved = gui.load_ui_state(state_path)
  assert saved["sash"] == 0.4 and saved["future"] == 1
