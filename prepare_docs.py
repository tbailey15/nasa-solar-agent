"""Download the nine NASA Science fact pages and save the article text.

Each page becomes one UTF-8 Markdown file in docs/ with the page title,
source URL and retrieval date at the top. Menus, link lists and the
"Discover More Topics" footer are left out. Image captions are kept because
they hold NASA's photo credits.

Run once:  python prepare_docs.py
"""

import urllib.request
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

FOLDER = Path(__file__).parent
DOCS = FOLDER / "docs"

PAGES = {
    "solar-system": "https://science.nasa.gov/solar-system/solar-system-facts/",
    "mercury": "https://science.nasa.gov/mercury/facts/",
    "venus": "https://science.nasa.gov/venus/facts/",
    "earth": "https://science.nasa.gov/earth/facts/",
    "mars": "https://science.nasa.gov/mars/facts/",
    "jupiter": "https://science.nasa.gov/jupiter/facts/",
    "saturn": "https://science.nasa.gov/saturn/facts/",
    "uranus": "https://science.nasa.gov/uranus/facts/",
    "neptune": "https://science.nasa.gov/neptune/facts/",
}

STOP_HEADINGS = ("discover more topics", "keep exploring")


def download(url):
    """Return the HTML of one page."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def article_text(html):
    """Return (title, markdown body) with only the article content."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True).replace(" - NASA Science", "")
    main = soup.select_one("main") or soup
    blocks = main.select("h2, h3, p, figcaption")

    # the article starts at the first real paragraph; everything before it is menus
    start = next(i for i, b in enumerate(blocks)
                 if b.name == "p" and len(b.get_text(strip=True)) > 80)

    lines = []
    for block in blocks[start:]:
        text = " ".join(block.get_text(" ", strip=True).split())
        if not text:
            continue
        if block.name in ("h2", "h3") and text.lower().startswith(STOP_HEADINGS):
            break
        if block.name == "h2":
            lines.append(f"## {text}")
        elif block.name == "h3":
            lines.append(f"### {text}")
        elif block.name == "figcaption":
            lines.append(f"*Image caption: {text}*")
        else:
            lines.append(text)
    return title, "\n\n".join(lines)


def main():
    DOCS.mkdir(exist_ok=True)
    today = date.today().isoformat()
    for name, url in PAGES.items():
        title, body = article_text(download(url))
        header = (f"# {title}\n\n"
                  f"Source: {url}\n"
                  f"Publisher: NASA Science (science.nasa.gov)\n"
                  f"Retrieved: {today}\n\n---\n\n")
        (DOCS / f"{name}.md").write_text(header + body + "\n", encoding="utf-8")
        print(f"{name:13} {len(body):6} characters")


if __name__ == "__main__":
    main()
