"""`stt site`: the dashboard as a static site, for GitHub Pages.

The same page as the claude.ai dashboard (``dashboard/index.html``) inside a shell
with its design tokens and a stand-in for the dashboard runtime
(``dashboard/standalone/``), plus the JSON files ``stt dashboard`` wrote. Output goes
to ``site/`` (gitignored); the daily GitHub Actions run publishes it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from stocktrend.config import ROOT
from stocktrend.report.dashboard import DATASETS, OUT_DIR

PAGE = ROOT / "dashboard" / "index.html"
STANDALONE = ROOT / "dashboard" / "standalone"
SITE_DIR = ROOT / "site"


def build(out: Path = SITE_DIR, data_dir: Path = OUT_DIR) -> Path:
    missing = [d for d in DATASETS if not (data_dir / f"{d}.json").exists()]
    if missing:
        raise FileNotFoundError(f"run `stt dashboard` first; missing: {', '.join(missing)}")
    page = PAGE.read_text(encoding="utf-8").replace('<meta name="dash-one-writer">\n', "", 1)
    shell = (STANDALONE / "shell.html").read_text(encoding="utf-8")
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    (out / "index.html").write_text(shell.replace("<!-- PAGE -->", page), encoding="utf-8")
    shutil.copy(STANDALONE / "dash.js", out / "dash.js")
    for d in DATASETS:
        shutil.copy(data_dir / f"{d}.json", out / "data" / f"{d}.json")
    (out / "data" / "index.json").write_text(json.dumps(list(DATASETS)), encoding="utf-8")
    (out / ".nojekyll").touch()
    return out
