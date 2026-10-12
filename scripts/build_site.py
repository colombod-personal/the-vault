"""Build the player guide (docs/site) into plain HTML, ready for GitHub Pages (bead vault-30s.3.2).

    python scripts/build_site.py [SOURCE_DIR] [OUT_DIR]      # default: docs/site -> _site

Standard library only. There is no framework: every page in the source folder is an HTML fragment with a few comment
lines at the top, and this script wraps it in the shared header, navigation and footer (``_layout.html``) and copies
the other files (``style.css``, ``assets/``) next to it. Files and folders whose name starts with ``_`` are not copied.

The comment lines at the top of a page:

    <!-- title: Install the Vault in Claude -->          required: the page title
    <!-- summary: One sentence for search results. -->   required
    <!-- nav: Claude -->                                  the label in the navigation (default: the title)
    <!-- order: 2 -->                                     the position in the navigation and the page list (default: 50)
    <!-- status: being-written -->                        required: home | being-written | checked
    <!-- checked: 2026-10-14, Claude desktop 1.2.3 -->    required when the status is "checked": the date and app version

The rule this enforces (the owner asked for *correct* guidance): a page that is **being written** carries a banner and may
not contain a numbered list or any other steps; a page that is **checked** must say on what date and which app version its
steps were run on the real app. ``{{pages}}`` in a page is replaced by the list of the guide's pages with their status.
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "site"
OUT = ROOT / "_site"

STATUSES = ("home", "being-written", "checked")
META_LINE = re.compile(r"^<!--\s*([a-z-]+):\s*(.*?)\s*-->$")
BANNER = ('<div class="status" role="note"><p><strong>This page is being written.</strong> '
          "Only steps that were run on the real app are published, so there are no steps here yet.</p></div>")
STEP_MARKERS = re.compile(r"<ol\b", re.IGNORECASE)  # a numbered list is how steps are written: none while being written


def split_meta(text: str) -> tuple[dict[str, str], str]:
    """The comment lines at the top of a page, and the rest."""
    meta: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line:
            found = META_LINE.match(line)
            if not found:
                break
            meta[found.group(1)] = found.group(2)
        i += 1
    return meta, "\n".join(lines[i:]).strip() + "\n"


class SiteError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def pages_of(source: Path) -> list[Path]:
    return sorted(p for p in source.rglob("*.html") if not any(part.startswith("_") for part in p.relative_to(source).parts))


def load_pages(source: Path) -> list[dict]:
    pages, problems = [], []
    for path in pages_of(source):
        rel = path.relative_to(source).as_posix()
        meta, body = split_meta(path.read_text(encoding="utf-8"))
        for key in ("title", "summary", "status"):
            if not meta.get(key):
                problems.append(f"{rel}: the page needs a `<!-- {key}: ... -->` line at the top")
        status = meta.get("status", "")
        if status and status not in STATUSES:
            problems.append(f"{rel}: status must be one of {', '.join(STATUSES)} (it is {status!r})")
        if status == "being-written" and STEP_MARKERS.search(body):
            problems.append(f"{rel}: a page that is being written may not contain steps (a numbered list): only steps run on the real app are published")
        if status == "checked" and not meta.get("checked"):
            problems.append(f"{rel}: a checked page needs `<!-- checked: <date>, <app and version> -->` (when its steps were run on the real app)")
        if status == "home" and rel != "index.html":
            problems.append(f"{rel}: only index.html can have the status 'home'")
        if "<h1" not in body:
            problems.append(f"{rel}: the page needs one <h1>")
        try:
            order = int(meta.get("order", "50"))
        except ValueError:
            order = 50
            problems.append(f"{rel}: order must be a number")
        pages.append({"rel": rel, "meta": meta, "body": body, "order": order, "status": status})
    if not any(p["rel"] == "index.html" for p in pages):
        problems.append("index.html is missing: the guide needs a start page")
    if problems:
        raise SiteError(problems)
    return sorted(pages, key=lambda p: (p["order"], p["rel"]))


def page_list(pages: list[dict], root: str) -> str:
    items = []
    for p in pages:
        if p["status"] == "home":
            continue
        label = html.escape(p["meta"].get("nav") or p["meta"]["title"])
        tag = "being written" if p["status"] == "being-written" else "checked on the real app"
        items.append(f'  <li><a href="{root}{p["rel"]}">{html.escape(p["meta"]["title"])}</a> <span class="tag">({tag})</span>'
                     f'<br><span class="tag">{html.escape(p["meta"]["summary"])}</span></li>' if False else
                     f'  <li><a href="{root}{p["rel"]}">{html.escape(p["meta"]["title"])}</a> <span class="tag">({tag})</span></li>')
        del label
    return '<ul class="pages">\n' + "\n".join(items) + "\n</ul>"


def nav_html(pages: list[dict], current: str, root: str) -> str:
    links = []
    for p in pages:
        label = html.escape(p["meta"].get("nav") or p["meta"]["title"])
        here = ' aria-current="page"' if p["rel"] == current else ""
        links.append(f'    <a href="{root}{p["rel"]}"{here}>{label}</a>')
    return "\n".join(links)


def render(page: dict, pages: list[dict], layout: str) -> str:
    depth = page["rel"].count("/")
    root = "../" * depth
    meta, status = page["meta"], page["status"]
    content = page["body"].replace("{{pages}}", page_list(pages, root))
    banner = BANNER if status == "being-written" else ""
    if status == "checked":
        banner = f'<p class="checked">Checked on the real app: {html.escape(meta["checked"])}.</p>'
    robots = '<meta name="robots" content="noindex">' if status == "being-written" else ""
    values = {"title": html.escape(meta["title"]), "summary": html.escape(meta["summary"]), "root": root, "robots": robots,
              "nav": nav_html(pages, page["rel"], root), "status_banner": banner, "content": content}
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), layout)


def build(source: Path = SOURCE, out: Path = OUT) -> list[str]:
    """Write the site to ``out`` and return the paths written (relative, sorted). Raises SiteError for a page that breaks a rule."""
    source, out = source.resolve(), out.resolve()
    if out == source or out in source.parents or source in out.parents:
        raise SiteError([f"the output folder {out} must be separate from the source folder {source}"])
    layout_path = source / "_layout.html"
    if not layout_path.is_file():
        raise SiteError([f"{layout_path} is missing"])
    layout = layout_path.read_text(encoding="utf-8")
    pages = load_pages(source)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written = []
    for page in pages:
        target = out / page["rel"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(page, pages, layout), encoding="utf-8")
        written.append(page["rel"])
    page_paths = {p["rel"] for p in pages}
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_dir() or any(part.startswith("_") for part in rel.parts) or rel.as_posix() in page_paths:
            continue
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        written.append(rel.as_posix())
    return sorted(written)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    source = Path(args[0]) if len(args) > 0 else SOURCE
    out = Path(args[1]) if len(args) > 1 else OUT
    try:
        written = build(source, out)
    except SiteError as exc:
        for problem in exc.problems:
            print(f"build_site: {problem}", file=sys.stderr)
        return 1
    print(f"build_site: wrote {len(written)} files to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
