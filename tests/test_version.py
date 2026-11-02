"""The version is defined once, in the library, and shown by the window."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import minimalftp  # noqa: E402
import minimalftp_gui as gui  # noqa: E402


def test_version_is_1_0():
  assert minimalftp.__version__ == "1.0"
  assert re.fullmatch(r"\d+\.\d+", minimalftp.__version__)


def test_window_title_shows_the_version():
  for lang in ("en", "it"):
    assert gui.STRINGS["window_title"][lang] == "minimalFTP v" + minimalftp.__version__
