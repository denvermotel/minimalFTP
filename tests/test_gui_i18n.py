"""Tests for the GUI internationalization (English and Italian)."""
import ast
import string
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minimalftp_gui as gui  # noqa: E402


@pytest.fixture
def lang_restore():
  """Restores the global translator language after the test."""
  before = gui.translator.lang
  yield gui.translator
  gui.translator.set_language(before)


def _placeholders(text: str) -> set:
  return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def test_every_key_has_en_and_it():
  assert gui.STRINGS
  for key, texts in gui.STRINGS.items():
    assert set(texts) == {"en", "it"}, key


def test_placeholders_match_between_languages():
  for key, texts in gui.STRINGS.items():
    assert _placeholders(texts["en"]) == _placeholders(texts["it"]), key


def test_no_empty_texts_and_no_long_dashes():
  for key, texts in gui.STRINGS.items():
    for lang, text in texts.items():
      assert text.strip(), (key, lang)
      assert "\u2014" not in text and "\u2013" not in text, (key, lang)


def test_detect_language_cases():
  assert gui.detect_language({"LANG": "it_IT.UTF-8"}) == "it"
  assert gui.detect_language({"LANG": "en_US.UTF-8"}) == "en"
  assert gui.detect_language({"LC_ALL": "it_IT.UTF-8", "LANG": "en_US"}) == "it"
  assert gui.detect_language({"LC_ALL": "en_US", "LANG": "it_IT"}) == "en"
  assert gui.detect_language({"LC_MESSAGES": "it_IT", "LANG": "en_US"}) == "it"
  assert gui.detect_language({}) == "en"
  assert gui.detect_language({"LANG": ""}) == "en"
  assert gui.detect_language({"LANG": "C"}) == "en"


def test_unknown_key_returns_key():
  assert gui.translator.tr("no_such_key_xyz") == "no_such_key_xyz"
  assert gui.translator.tr("no_such_key_xyz", a=1) == "no_such_key_xyz"


def test_tr_formats_placeholders(lang_restore):
  lang_restore.set_language("en")
  assert "3" in lang_restore.tr("log_profile_saved", name="3")
  # a missing placeholder never raises
  assert isinstance(lang_restore.tr("log_profile_saved"), str)


def test_set_language_validates(lang_restore):
  lang_restore.set_language("it")
  assert lang_restore.lang == "it"
  lang_restore.set_language("en")
  assert lang_restore.lang == "en"
  with pytest.raises(ValueError):
    lang_restore.set_language("fr")
  assert lang_restore.lang == "en"


def test_subscribers_notified_on_change(lang_restore):
  seen = []
  callback = lambda: seen.append(lang_restore.lang)  # noqa: E731
  lang_restore.subscribe(callback)
  try:
    lang_restore.set_language("it")
    lang_restore.set_language("en")
    assert seen[-2:] == ["it", "en"]
  finally:
    lang_restore.unsubscribe(callback)


_WIDGET_KWARGS = {"text", "title", "message"}
_ALLOWED_LITERALS = {"", "English", "Italiano"}


def test_no_hardcoded_ui_literals():
  source = (Path(__file__).resolve().parent.parent / "minimalftp_gui.py").read_text(encoding="utf-8")
  tree = ast.parse(source)
  offenders = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
      continue
    for kw in node.keywords:
      if kw.arg in _WIDGET_KWARGS and isinstance(kw.value, ast.Constant) \
          and isinstance(kw.value.value, str) and kw.value.value not in _ALLOWED_LITERALS:
        offenders.append((node.lineno, kw.arg, kw.value.value))
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    # messagebox / filedialog positional title and message
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
        and func.value.id in ("messagebox", "filedialog"):
      for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
          offenders.append((node.lineno, name, arg.value))
  assert not offenders, offenders


def _make_app(tmp_path, language, root):
  return root, gui.App(root, profiles_path=tmp_path / "profiles.json", language=language)


def test_switching_language_relabels_widgets(tmp_path, lang_restore, tk_window):
  root, app = _make_app(tmp_path, "en", tk_window)
  try:
    assert str(app.btn_run.cget("text")) == "Run"
    assert str(app.btn_dir.cget("text")) == "UP >>>"
    assert tuple(app.language_box.cget("values")) == ("English", "Italiano")
    app.set_language("it")
    assert str(app.btn_run.cget("text")) == "Esegui"
    assert str(app.btn_dir.cget("text")) == "SU >>>"
    assert app.translator.lang == "it"
    app.set_language("en")
    assert str(app.btn_run.cget("text")) == "Run"
  finally:
    root.destroy()


def test_language_selector_switches_live(tmp_path, lang_restore, tk_window):
  root, app = _make_app(tmp_path, "en", tk_window)
  try:
    app.language_box.current(1)
    app.language_box.event_generate("<<ComboboxSelected>>")
    root.update()
    assert gui.translator.lang == "it"
    assert str(app.btn_preview.cget("text")) == "Anteprima"
    assert root.title() == gui.STRINGS["window_title"]["it"]
  finally:
    root.destroy()


def test_plan_rows_and_filter_follow_language(tmp_path, lang_restore, tk_window):
  from minimalftp import Action
  root, app = _make_app(tmp_path, "it", tk_window)
  try:
    app._plan = [Action("upload", "a.txt", "missing", 10),
                 Action("skip", "b.txt", "conflict-dest-newer", 5)]
    app.cb_filter.current(2)
    app._refill_plan()
    words = [app.tree.item(i, "values")[0] for i in app.tree.get_children()]
    assert words == ["CONFL"]
    app.set_language("en")
    words = [app.tree.item(i, "values")[0] for i in app.tree.get_children()]
    assert words == ["CONFLICT"]
    # the selected filter index is kept while the labels change
    assert app.cb_filter.current() == 2
    assert tuple(app.cb_filter.cget("values"))[2] == gui.STRINGS["filter_conflicts_errors"]["en"]
  finally:
    root.destroy()


def test_initial_language_is_used(tmp_path, lang_restore, tk_window):
  root, app = _make_app(tmp_path, "it", tk_window)
  try:
    assert gui.translator.lang == "it"
    assert app.language_box.get() == "Italiano"
    assert str(app.btn_run.cget("text")) == "Esegui"
  finally:
    root.destroy()
