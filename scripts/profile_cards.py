import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

type JsonObject = dict[str, Any]
type RepoCounts = tuple[tuple[str, int], ...]
type YearCounts = tuple[tuple[int, int], ...]

LOGIN: str = "stonebuzz"
API_URL: str = "https://api.github.com"
OUTPUT_DIR: Path = Path("profile")
YEARS_SHOWN: int = 8
# Search API allows 30 authenticated requests per minute.
SEARCH_PAUSE_SECONDS: float = 2.5
AUTHORED_QUERY: str = f"type:pr is:public author:{LOGIN}"
REVIEWED_QUERY: str = f"type:pr is:public reviewed-by:{LOGIN} -author:{LOGIN}"

CARD_WIDTH: int = 440
CARD_HEIGHT: int = 250
TITLE_BAR_HEIGHT: int = 28
PADDING: int = 20
CHART_WIDTH: int = CARD_WIDTH - 2 * PADDING
BAR_WIDTH: int = 28
BAR_MAX_HEIGHT: int = 55
BAR_BASELINE: int = 222
COMMAND_CHAR_WIDTH: float = 7.2
SMALL_CHAR_WIDTH: float = 6.0
TOP_LINE_MAX_CHARS: int = 64
FONT_STACK: str = "'JetBrains Mono','Fira Code',ui-monospace,SFMono-Regular,Menlo,Consolas,'DejaVu Sans Mono',monospace"

CONTRIBUTIONS_QUERY: str = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      pullRequestContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner isPrivate }
        contributions { totalCount }
      }
      pullRequestReviewContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner isPrivate }
        contributions { totalCount }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    title_bar: str
    border: str
    text: str
    muted: str
    prompt: str
    author_accent: str
    reviewer_accent: str


@dataclass(frozen=True)
class AuthorStats:
    opened: int
    merged: int
    per_year: YearCounts
    top_repos: RepoCounts


@dataclass(frozen=True)
class ReviewerStats:
    reviewed: int
    reviews_last_year: int
    repos_last_year: int
    per_year: YearCounts
    top_repos: RepoCounts


@dataclass(frozen=True)
class CardContent:
    title: str
    command: str
    figures: tuple[tuple[str, str], ...]
    top_repos: RepoCounts
    chart_caption: str
    per_year: YearCounts


THEMES: tuple[Theme, ...] = (
    Theme("dark", "#0d1117", "#161b22", "#30363d", "#c9d1d9", "#8b949e", "#3fb950", "#58a6ff", "#d2a8ff"),
    Theme("light", "#ffffff", "#f6f8fa", "#d0d7de", "#1f2328", "#656d76", "#1a7f37", "#0969da", "#8250df"),
)


def api_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{LOGIN}-profile-cards",
    }


def fetch_json(request: urllib.request.Request) -> JsonObject:
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def search_count(query: str, token: str) -> int:
    params: str = urllib.parse.urlencode({"q": query, "per_page": 1})
    request = urllib.request.Request(f"{API_URL}/search/issues?{params}", headers=api_headers(token))
    time.sleep(SEARCH_PAUSE_SECONDS)
    return int(fetch_json(request)["total_count"])


def graphql(query: str, variables: dict[str, str], token: str) -> JsonObject:
    body: bytes = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(f"{API_URL}/graphql", data=body, headers=api_headers(token), method="POST")
    return fetch_json(request)["data"]


def public_repo_counts(entries: list[JsonObject]) -> RepoCounts:
    counts: list[tuple[str, int]] = [
        (entry["repository"]["nameWithOwner"], int(entry["contributions"]["totalCount"]))
        for entry in entries
        if not entry["repository"]["isPrivate"]
    ]
    return tuple(sorted(counts, key=lambda item: item[1], reverse=True))


def shown_years(today: date) -> range:
    return range(today.year - YEARS_SHOWN + 1, today.year + 1)


def yearly_counts(base_query: str, years: range, token: str) -> YearCounts:
    return tuple((year, search_count(f"{base_query} created:{year}-01-01..{year}-12-31", token)) for year in years)


def merge_rate(stats: AuthorStats) -> str:
    return f"{round(100 * stats.merged / stats.opened)}%" if stats.opened else "n/a"


def author_card(stats: AuthorStats) -> CardContent:
    return CardContent(
        title=f"{LOGIN}@github: ~/pull-requests/authored",
        command=f"gh pr list --author {LOGIN} --state all",
        figures=((f"{stats.opened:,}", "PRs opened"), (f"{stats.merged:,}", "PRs merged"), (merge_rate(stats), "merge rate")),
        top_repos=stats.top_repos,
        chart_caption="# PRs opened per year",
        per_year=stats.per_year,
    )


def reviewer_card(stats: ReviewerStats) -> CardContent:
    return CardContent(
        title=f"{LOGIN}@github: ~/pull-requests/reviewed",
        command=f'gh pr list --search "reviewed-by:{LOGIN}"',
        figures=(
            (f"{stats.reviewed:,}", "PRs reviewed"),
            (f"{stats.reviews_last_year:,}", "reviews, 12 mo"),
            (f"{stats.repos_last_year:,}", "repos, 12 mo"),
        ),
        top_repos=stats.top_repos,
        chart_caption="# PRs reviewed per year",
        per_year=stats.per_year,
    )


def fitting_repos(repos: RepoCounts, max_chars: int) -> str:
    line: str = ""
    for name, count in repos:
        entry: str = f"{name.split('/')[-1]} {count}"
        candidate: str = f"{line} | {entry}" if line else entry
        if len(candidate) > max_chars:
            break
        line = candidate
    return line


def frame_svg(content: CardContent, accent: str, theme: Theme) -> str:
    command_x: float = PADDING + 2 * COMMAND_CHAR_WIDTH
    cursor_x: float = command_x + (len(content.command) + 1) * COMMAND_CHAR_WIDTH
    right: int = CARD_WIDTH - 8
    return (
        f'<rect x="0.5" y="0.5" width="{CARD_WIDTH - 1}" height="{CARD_HEIGHT - 1}" rx="8" '
        f'fill="{theme.background}" stroke="{theme.border}"/>'
        f'<path d="M0.5,{TITLE_BAR_HEIGHT} V8.5 A8,8 0 0 1 8.5,0.5 H{right - 0.5} A8,8 0 0 1 {CARD_WIDTH - 0.5},8.5 '
        f'V{TITLE_BAR_HEIGHT} Z" fill="{theme.title_bar}" stroke="{theme.border}"/>'
        f'<circle cx="16" cy="14" r="5" fill="#ff5f56"/><circle cx="32" cy="14" r="5" fill="#ffbd2e"/>'
        f'<circle cx="48" cy="14" r="5" fill="#27c93f"/>'
        f'<text x="{CARD_WIDTH / 2}" y="18" text-anchor="middle" class="title">{escape(content.title)}</text>'
        f'<text x="{PADDING}" y="54" class="cmd" fill="{theme.prompt}">$</text>'
        f'<text x="{command_x:.1f}" y="54" class="cmd" fill="{theme.text}">{escape(content.command)}</text>'
        f'<rect x="{cursor_x:.1f}" y="44" width="7" height="13" fill="{accent}">'
        f'<animate attributeName="opacity" values="1;0;1" dur="1.2s" repeatCount="indefinite"/></rect>'
    )


def figures_svg(figures: tuple[tuple[str, str], ...], accent: str) -> str:
    column_width: float = CHART_WIDTH / len(figures)
    return "".join(
        f'<text x="{PADDING + index * column_width:.1f}" y="88" class="value" fill="{accent}">{escape(value)}</text>'
        f'<text x="{PADDING + index * column_width:.1f}" y="104" class="label">{escape(label)}</text>'
        for index, (value, label) in enumerate(figures)
    )


def top_repos_svg(repos: RepoCounts) -> str:
    prefix: str = "top 12 mo:"
    names: str = fitting_repos(repos, TOP_LINE_MAX_CHARS - len(prefix) - 1)
    names_x: float = PADDING + (len(prefix) + 1) * SMALL_CHAR_WIDTH
    return (
        f'<text x="{PADDING}" y="128" class="small">{prefix}</text>'
        f'<text x="{names_x:.1f}" y="128" class="repo">{escape(names)}</text>'
    )


def bars_svg(per_year: YearCounts, accent: str, theme: Theme) -> str:
    peak: int = max((count for _, count in per_year), default=0) or 1
    slot: float = CHART_WIDTH / len(per_year)
    parts: list[str] = []
    for index, (year, count) in enumerate(per_year):
        height: int = max(round(BAR_MAX_HEIGHT * count / peak), 1)
        center: float = PADDING + index * slot + slot / 2
        parts.append(
            f'<rect x="{center - BAR_WIDTH / 2:.1f}" y="{BAR_BASELINE - height}" width="{BAR_WIDTH}" height="{height}" '
            f'rx="3" fill="{accent}" fill-opacity="0.85"/>'
            f'<text x="{center:.1f}" y="{BAR_BASELINE - height - 4}" text-anchor="middle" class="num" fill="{theme.text}">{count}</text>'
            f'<text x="{center:.1f}" y="{BAR_BASELINE + 14}" text-anchor="middle" class="small">{year}</text>'
        )
    return "".join(parts)


def style_svg(theme: Theme) -> str:
    return (
        f"<style>text{{font-family:{FONT_STACK}}}"
        f".title{{font-size:11px;fill:{theme.muted}}}.cmd{{font-size:12px}}"
        f".value{{font-size:22px;font-weight:700}}.label{{font-size:11px;fill:{theme.muted}}}"
        f".small{{font-size:10px;fill:{theme.muted}}}.repo{{font-size:10px;fill:{theme.text}}}"
        f".num{{font-size:9px}}</style>"
    )


def render_card(content: CardContent, accent: str, theme: Theme) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" height="{CARD_HEIGHT}" '
        f'viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}" role="img" aria-label="{escape(content.title)}">'
        f"{style_svg(theme)}"
        f"{frame_svg(content, accent, theme)}"
        f"{figures_svg(content.figures, accent)}"
        f"{top_repos_svg(content.top_repos)}"
        f'<text x="{PADDING}" y="152" class="small">{escape(content.chart_caption)}</text>'
        f"{bars_svg(content.per_year, accent, theme)}"
        f"</svg>\n"
    )


def main() -> None:
    token: str = os.environ["GITHUB_TOKEN"]
    years: range = shown_years(date.today())
    contributions: JsonObject = graphql(CONTRIBUTIONS_QUERY, {"login": LOGIN}, token)["user"]["contributionsCollection"]
    authored_repos: RepoCounts = public_repo_counts(contributions["pullRequestContributionsByRepository"])
    reviewed_repos: RepoCounts = public_repo_counts(contributions["pullRequestReviewContributionsByRepository"])
    author = AuthorStats(
        opened=search_count(AUTHORED_QUERY, token),
        merged=search_count(f"{AUTHORED_QUERY} is:merged", token),
        per_year=yearly_counts(AUTHORED_QUERY, years, token),
        top_repos=authored_repos,
    )
    reviewer = ReviewerStats(
        reviewed=search_count(REVIEWED_QUERY, token),
        reviews_last_year=sum(count for _, count in reviewed_repos),
        repos_last_year=len(reviewed_repos),
        per_year=yearly_counts(REVIEWED_QUERY, years, token),
        top_repos=reviewed_repos,
    )
    OUTPUT_DIR.mkdir(exist_ok=True)
    for theme in THEMES:
        (OUTPUT_DIR / f"author-{theme.name}.svg").write_text(render_card(author_card(author), theme.author_accent, theme))
        (OUTPUT_DIR / f"reviewer-{theme.name}.svg").write_text(
            render_card(reviewer_card(reviewer), theme.reviewer_accent, theme)
        )


if __name__ == "__main__":
    main()
