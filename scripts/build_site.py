"""Build the player guide (docs/site) into plain HTML, ready for GitHub Pages (bead vault-30s.3.2).

    python scripts/build_site.py [SOURCE_DIR] [OUT_DIR]      # default: docs/site -> _site

Standard library only. There is no framework: every page in the source folder is an HTML fragment with a few comment
lines at the top, and this script wraps it in the shared header, navigation and footer (``_layout.html``) and copies
the allowed asset files (``style.css``, screenshots in ``assets/``) next to it.

What is published is an allow-list, because everything in the output becomes public:

- pages (``.html``) and these assets only: ``.css``, ``.png``, ``.jpg``, ``.jpeg``, ``.webp``, ``.ico``, ``.txt`` (no SVG: it can
  carry script; no ``.md``, ``.py``, ``.htm`` and so on). Any other file is an error, not a silent skip, so nobody assumes
  it was published;
- a name that starts with ``.`` or contains ``.local.`` (the repository's private-notes convention) is refused;
- a symbolic link anywhere in the source is refused (it could publish a file from outside the tree);
- files and folders whose name starts with ``_`` are never copied (put notes in ``_notes/``).

The comment lines at the top of a page:

    <!-- title: Install the Vault in Claude -->          required: the page title
    <!-- summary: One sentence for search results. -->   required
    <!-- nav: Claude -->                                  the label in the navigation (default: the title)
    <!-- order: 2 -->                                     the position in the navigation and the page list (default: 50)
    <!-- status: being-written -->                        required: home | being-written | checked
    <!-- checked: 2026-10-14, Claude desktop 1.2.3 -->    required when the status is "checked": the date and app version

The rule this enforces (the owner asked for *correct* guidance): a page that is **being written** carries a banner and may not
contain steps; a page that is **checked** must say on what date and which app version its steps were run on the real app.
"No steps" is a heuristic (STEP_PATTERNS: a numbered list, ``<pre>``, "Step 1", a line starting "1."): it stops the usual ways of
writing steps, it cannot prove a page holds no instruction, so a reviewer still reads every page that changes.
``{{pages}}`` in a page is replaced by the list of the guide's pages with their status.

The output folder is only ever replaced when it is new, empty or was made by this script (it holds MARKER): pointing the script
at a folder that holds anything else is refused, never deleted.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "site"
OUT = ROOT / "_site"
MARKER = ".build_site_output"  # a dotfile: GitHub's artifact upload leaves it out

STATUSES = ("home", "being-written", "checked")
ASSET_SUFFIXES = (".css", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".txt")
META_LINE = re.compile(r"^<!--\s*([a-z-]+):\s*(.*?)\s*-->$")
BANNER = ('<div class="status" role="note"><p><strong>This page is being written.</strong> '
          "Only steps that were run on the real app are published, so there are no steps here yet.</p></div>")
# The usual ways of writing steps. A heuristic, said so in the module docstring: a page being written may match none of them.
STEP_PATTERNS = (
    re.compile(r"<ol\b", re.IGNORECASE),                                              # a numbered list
    re.compile(r"<pre\b", re.IGNORECASE),                                             # a block of commands or text to copy
    re.compile(r"<li[^>]{0,200}>\s{0,50}(?:<[^>]{1,200}>\s{0,50}){0,5}(?:step\s{0,5})?\d{1,3}\s{0,5}[.):]", re.IGNORECASE),  # <li>1. ...</li>
    re.compile(r"\bstep\s{0,5}\d{1,3}\b", re.IGNORECASE),                             # "Step 1", "step 2:"
    re.compile(r"^[ \t]{0,50}(?:<[^>]{1,200}>[ \t]{0,50}){0,5}\d{1,3}[ \t]{0,5}[.)]\s", re.MULTILINE),  # a line that starts "1. "
)


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


LOCAL_NAME = re.compile(r"\.local(\.|$)", re.IGNORECASE)  # notes.local.md, draft.local/ (a folder), page.LOCAL.html
CHECKED = re.compile(r"^(\d{4}-\d{2}-\d{2}),\s*(.{3,200})$")


def valid_checked(value: str) -> bool:
    """`2026-10-14, Claude desktop 1.2.3`: a real calendar date, then an app name with a version (a letter and a digit)."""
    found = CHECKED.match(value.strip())
    if not found:
        return False
    try:
        date.fromisoformat(found.group(1))
    except ValueError:
        return False
    app = found.group(2)
    return bool(re.search(r"[A-Za-z]", app) and re.search(r"\d", app))


class SiteError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def scan_source(source: Path) -> tuple[list[Path], list[Path], list[str]]:
    """The page files and the asset files that will be published, and what in the source tree is refused."""
    pages, assets, problems = [], [], []
    for folder, dirs, files in os.walk(source, followlinks=False):
        here = Path(folder)
        for name in sorted(dirs + files):
            path = here / name
            if path.is_symlink():
                problems.append(f"{path.relative_to(source).as_posix()}: a symbolic link is refused "
                                "(it could publish a file from outside docs/site)")
        dirs[:] = sorted(d for d in dirs if not (here / d).is_symlink())
        for name in sorted(files):
            path = here / name
            rel = path.relative_to(source)
            if path.is_symlink() or any(part.startswith("_") for part in rel.parts):
                continue
            if any(part.startswith(".") for part in rel.parts):
                problems.append(f"{rel.as_posix()}: a name that starts with '.' is never published")
            elif any(LOCAL_NAME.search(part) for part in rel.parts):  # every folder of the path, not only the file name
                problems.append(f"{rel.as_posix()}: a '.local.' file or folder is private notes and is never published")
            elif path.suffix != path.suffix.lower():
                problems.append(f"{rel.as_posix()}: file extensions must be lower case (the link check and the server treat "
                                f"{path.suffix!r} differently)")
            elif path.suffix == ".html":
                pages.append(path)
            elif path.suffix in ASSET_SUFFIXES:
                assets.append(path)
            else:
                problems.append(f"{rel.as_posix()}: {path.suffix or name!r} files are not published (allowed: .html and "
                                f"{', '.join(ASSET_SUFFIXES)}); rename a note to start with '_' to keep it out of the site")
    return sorted(pages), sorted(assets), problems


def load_pages(source: Path) -> list[dict]:
    page_files, _assets, problems = scan_source(source)
    pages = []
    for path in page_files:
        rel = path.relative_to(source).as_posix()
        meta, body = split_meta(path.read_text(encoding="utf-8"))
        for key in ("title", "summary", "status"):
            if not meta.get(key):
                problems.append(f"{rel}: the page needs a `<!-- {key}: ... -->` line at the top")
        status = meta.get("status", "")
        if status and status not in STATUSES:
            problems.append(f"{rel}: status must be one of {', '.join(STATUSES)} (it is {status!r})")
        if status != "checked" and any(pattern.search(body) for pattern in STEP_PATTERNS):  # being-written and home alike
            problems.append(f"{rel}: a page that is not `checked` may not contain steps (a numbered list, <pre>, 'Step 1', '1.'): "
                            "only steps run on the real app are published")
        if status == "checked" and not valid_checked(meta.get("checked", "")):
            problems.append(f"{rel}: a checked page needs `<!-- checked: YYYY-MM-DD, <app and version> -->` (the real date its steps were "
                            f"run on the real app, then the app and its version, e.g. 'Claude desktop 1.2.3'); got {meta.get('checked', '')!r}")
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
        tag = "being written" if p["status"] == "being-written" else "checked on the real app"
        href = html.escape(root + p["rel"], quote=True)
        items.append(f'  <li><a href="{href}">{html.escape(p["meta"]["title"])}</a> <span class="tag">({tag})</span></li>')
    return '<ul class="pages">\n' + "\n".join(items) + "\n</ul>"


def nav_html(pages: list[dict], current: str, root: str) -> str:
    links = []
    for p in pages:
        label = html.escape(p["meta"].get("nav") or p["meta"]["title"])
        here = ' aria-current="page"' if p["rel"] == current else ""
        href = html.escape(root + p["rel"], quote=True)
        links.append(f'    <a href="{href}"{here}>{label}</a>')
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
    values = {"title": html.escape(meta["title"]), "summary": html.escape(meta["summary"], quote=True), "root": root, "robots": robots,
              "nav": nav_html(pages, page["rel"], root), "status_banner": banner, "content": content}
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), layout)


def prepare_out(out: Path) -> None:
    """Make ``out`` an empty folder, deleting only what this script itself made earlier."""
    if out.is_symlink():
        raise SiteError([f"the output folder {out} is a symbolic link: refused"])
    if out.exists():
        if not out.is_dir():
            raise SiteError([f"the output path {out} exists and is not a folder"])
        if any(out.iterdir()):
            if not (out / MARKER).is_file():
                raise SiteError([f"the output folder {out} is not empty and was not made by build_site.py: refusing to delete it"])
            shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)


def build(source: Path = SOURCE, out: Path = OUT) -> list[str]:
    """Write the site to ``out`` and return the paths written (relative, sorted). Raises SiteError for a page that breaks a rule."""
    for given, what in ((source, "source"), (out, "output")):  # before resolve(), which would follow the link and hide it
        if given.is_symlink():
            raise SiteError([f"the {what} folder {given} is a symbolic link: refused"])
    source, out = source.resolve(), out.resolve()
    if out == source or out in source.parents or source in out.parents:
        raise SiteError([f"the output folder {out} must be separate from the source folder {source}"])
    layout_path = source / "_layout.html"
    if not layout_path.is_file() or layout_path.is_symlink():
        raise SiteError([f"{layout_path} is missing"])
    layout = layout_path.read_text(encoding="utf-8")
    pages = load_pages(source)
    _page_files, assets, _problems = scan_source(source)
    prepare_out(out)
    written = []
    for page in pages:
        target = out / page["rel"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(page, pages, layout), encoding="utf-8")
        written.append(page["rel"])
    for path in assets:
        rel = path.relative_to(source)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)  # the bytes only: no permissions, no metadata
        written.append(rel.as_posix())
    (out / MARKER).write_text("made by scripts/build_site.py; safe to delete and rebuild\n", encoding="utf-8")
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
