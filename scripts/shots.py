"""Screenshot helpers shared by the demo scripts (copied into each repo as scripts/shots.py)."""
import io
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.terminal_theme import MONOKAI
from rich.text import Text


def terminal_html(title: str, body_ansi: str, width: int = 130) -> str:
    con = Console(record=True, width=width, height=80, force_terminal=True, color_system="truecolor", file=io.StringIO())
    con.print(Panel(Text.from_ansi(body_ansi), title=f"[bold]{title}[/]", border_style="grey50", padding=(0, 1)))
    return con.export_html(theme=MONOKAI, inline_styles=True)


def html_to_png(html: str, out: Path, width: int = 1300, selector: str | None = None):
    from playwright.sync_api import sync_playwright
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    html = html.replace("<body>", '<body style="margin:0;padding:18px;background:#272822">', 1) if "<body>" in html else html
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": width, "height": 400}, device_scale_factor=2)
        pg.goto(Path(f.name).as_uri())
        pg.wait_for_timeout(300)
        (pg.locator(selector).first if selector else pg).screenshot(path=str(out), **({} if selector else {"full_page": True}))
        b.close()
    return out


def run_capture(cmd, cwd=None, env=None, columns=122) -> str:
    """Run a shell command with colors forced and return combined output (for terminal screenshots)."""
    import os
    import re
    e = dict(os.environ, FORCE_COLOR="1", PY_COLORS="1", TERM="xterm-256color", COLUMNS=str(columns), LINES="60")
    e.update(env or {})
    r = subprocess.run(cmd, shell=True, cwd=cwd, env=e, capture_output=True, text=True)
    shown = re.sub(r"/\S+/(python3?|node)\b", r"\1", cmd)  # hide absolute interpreter paths
    return f"$ {shown}\n" + r.stdout + r.stderr


def terminal_png(title: str, body_ansi: str, out: Path, width: int = 130):
    return html_to_png(terminal_html(title, body_ansi, width), out, width=int(width * 9.2) + 60)
