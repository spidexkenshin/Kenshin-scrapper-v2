"""
scraper.py  –  Dual-site scraper
Sites: desidubanime.me  |  animehindidubbed.in
Both are WordPress — same REST API + HTML fallback logic.
Quality detection: 360p / 480p / 720p / 1080p / 4K
"""

import re
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

SITES = {
    "desi":  "https://desidubanime.me",
    "hindi": "https://animehindidubbed.in",
}

SITE_LABELS = {
    "desi":  "🟠 DesiDub",
    "hindi": "🔵 HindiDub",
}

QUALITY_PATTERNS = [
    ("4K / 2160p", re.compile(r"2160p|4k|uhd", re.I)),
    ("1080p FHD",  re.compile(r"1080p|fhd|full.?hd", re.I)),
    ("720p HD",    re.compile(r"720p|hd(?!r)", re.I)),
    ("480p",       re.compile(r"480p|sd", re.I)),
    ("360p",       re.compile(r"360p|low", re.I)),
]

DOWNLOAD_DOMAINS = [
    "mega.nz", "mega.io",
    "drive.google.com",
    "mediafire.com",
    "gdtot", "gofile.io",
    "pixeldrain.com",
    "1drv.ms", "onedrive",
    "buzzheavier.com",
    "krakenfiles.com",
    "streamtape.com",
    "filelions.com",
    "dood.watch",
    "streamhg",
    "mixdrop",
    "upstream",
    "embedsito",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# connector shared across requests for max speed
def make_connector():
    return aiohttp.TCPConnector(
        limit=50,              # max concurrent connections
        limit_per_host=10,
        ttl_dns_cache=300,
        force_close=False,
        enable_cleanup_closed=True,
    )


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DownloadLink:
    label: str       # e.g. "720p HD – Mega"
    url: str
    quality: str     # "720p HD", "360p", "Unknown"
    host: str        # "mega.nz", "drive.google", …

@dataclass
class Episode:
    number: str
    title: str
    url: str
    site_key: str             # "desi" or "hindi"
    download_links: list = field(default_factory=list)   # List[DownloadLink]

@dataclass
class AnimeResult:
    title: str
    url: str
    site_key: str
    thumbnail: str = ""
    excerpt: str   = ""

@dataclass
class AnimeDetail:
    title: str
    url: str
    site_key: str
    thumbnail: str
    description: str
    genres: list
    episodes: list     # List[Episode]  – ALL episodes flat


# ── Helpers ───────────────────────────────────────────────────────────────────

def detect_quality(text: str) -> str:
    for label, pat in QUALITY_PATTERNS:
        if pat.search(text):
            return label
    return "Unknown"

def detect_host(url: str) -> str:
    for d in DOWNLOAD_DOMAINS:
        if d in url:
            return d
    return url.split("/")[2] if "//" in url else "link"

def clean_text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(strip=True)


# ── Search ────────────────────────────────────────────────────────────────────

async def search_anime(
    query: str,
    session: aiohttp.ClientSession,
    site_key: str = "both",
) -> list[AnimeResult]:
    """
    Search one or both sites concurrently.
    site_key: "desi" | "hindi" | "both"
    """
    keys = list(SITES.keys()) if site_key == "both" else [site_key]
    tasks = [_search_site(query, session, k) for k in keys]
    results_nested = await asyncio.gather(*tasks)

    combined: list[AnimeResult] = []
    for results in results_nested:
        combined.extend(results)

    # Deduplicate by title (fuzzy: lowercase stripped)
    seen_titles = set()
    deduped = []
    for r in combined:
        key = re.sub(r"\W", "", r.title.lower())
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(r)

    return deduped[:15]


async def _search_site(
    query: str,
    session: aiohttp.ClientSession,
    site_key: str,
) -> list[AnimeResult]:
    base = SITES[site_key]
    results = await _wp_api_search(query, session, base, site_key)
    if not results:
        results = await _html_search(query, session, base, site_key)
    return results


async def _wp_api_search(
    query: str,
    session: aiohttp.ClientSession,
    base: str,
    site_key: str,
) -> list[AnimeResult]:
    try:
        url = f"{base}/wp-json/wp/v2/posts"
        params = {
            "search":   query,
            "per_page": 10,
            "_fields":  "id,title,link,excerpt",
            "_embed":   "wp:featuredmedia",
        }
        timeout = aiohttp.ClientTimeout(total=12, connect=5)
        async with session.get(url, params=params, headers=HEADERS, timeout=timeout) as r:
            if r.status != 200:
                return []
            data = await r.json(content_type=None)

        out = []
        for post in data:
            thumb = ""
            try:
                emb = post.get("_embedded", {})
                media = emb.get("wp:featuredmedia", [{}])
                thumb = media[0].get("source_url", "")
            except Exception:
                pass
            out.append(AnimeResult(
                title    = clean_text(post["title"]["rendered"]),
                url      = post["link"],
                site_key = site_key,
                thumbnail= thumb,
                excerpt  = clean_text(post.get("excerpt", {}).get("rendered", ""))[:180],
            ))
        return out
    except Exception as e:
        print(f"[WP API {site_key}] {e}")
        return []


async def _html_search(
    query: str,
    session: aiohttp.ClientSession,
    base: str,
    site_key: str,
) -> list[AnimeResult]:
    try:
        url = f"{base}/?s={query}"
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        async with session.get(url, headers=HEADERS, timeout=timeout) as r:
            if r.status != 200:
                return []
            html = await r.text()

        soup = BeautifulSoup(html, "lxml")
        out  = []
        for art in soup.select("article, .post, .entry, .search-entry")[:12]:
            a = art.find("a", href=True)
            if not a:
                continue
            t = art.find(["h1","h2","h3"])
            title = t.get_text(strip=True) if t else a.get_text(strip=True)
            img   = art.find("img")
            thumb = img.get("src", img.get("data-src","")) if img else ""
            if title and base in a["href"]:
                out.append(AnimeResult(
                    title=title, url=a["href"],
                    site_key=site_key, thumbnail=thumb
                ))
        return out
    except Exception as e:
        print(f"[HTML search {site_key}] {e}")
        return []


# ── Anime detail + ALL episodes ───────────────────────────────────────────────

async def get_anime_detail(
    url: str,
    session: aiohttp.ClientSession,
    site_key: str,
) -> Optional[AnimeDetail]:
    try:
        timeout = aiohttp.ClientTimeout(total=20, connect=6)
        async with session.get(url, headers=HEADERS, timeout=timeout) as r:
            if r.status != 200:
                return None
            html = await r.text()
    except Exception as e:
        print(f"[Detail fetch] {e}")
        return None

    soup = BeautifulSoup(html, "lxml")
    base = SITES[site_key]

    # Title
    title_el = (soup.find("h1") or soup.find("h2"))
    title = title_el.get_text(strip=True) if title_el else "Unknown"

    # Thumbnail
    thumb = ""
    for sel in [
        "img.wp-post-image", ".post-thumbnail img",
        ".featured-image img", ".attachment-post-thumbnail",
        "article img", ".entry-content img",
    ]:
        el = soup.select_one(sel)
        if el:
            thumb = el.get("src") or el.get("data-src","")
            if thumb:
                break

    # Description
    desc = ""
    for sel in [".entry-content > p", ".post-content > p", "article > p"]:
        paras = soup.select(sel)
        if paras:
            desc = " ".join(p.get_text(strip=True) for p in paras[:3])[:500]
            break

    # Genres
    genres = [a.get_text(strip=True)
              for a in soup.select(".cat-links a, .tags a, .genre a, [rel='category tag']")]

    # ── Episode detection ──────────────────────────────────────────────────
    episodes: list[Episode] = []
    seen_urls: set[str] = set()

    content = soup.select_one(".entry-content, .post-content, article")
    if content:
        # All internal <a> tags inside content
        for a in content.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True)

            # Must be same site
            if base not in href:
                continue
            # Skip self
            if href.rstrip("/") == url.rstrip("/"):
                continue
            if href in seen_urls:
                continue

            # Episode number heuristics
            num_match = re.search(
                r"(?:episode|ep|e)[-_\s]?(\d{1,4})|(\d{1,4})\s*(?:episode|ep)",
                (href + " " + text), re.I
            )
            num = num_match.group(1) or num_match.group(2) if num_match else str(len(episodes)+1)

            # Season prefix from heading above (look for nearest h2/h3/h4 sibling)
            season_prefix = ""
            # walk up to find containing element, check prior headings
            parent = a.find_parent(["li","p","div"])
            if parent:
                prev = parent.find_previous_sibling(["h2","h3","h4","strong"])
                if prev:
                    htext = prev.get_text(strip=True)
                    if re.search(r"season|part|series", htext, re.I):
                        season_prefix = htext + " – "

            ep_title = season_prefix + (text or f"Episode {num}")
            seen_urls.add(href)
            episodes.append(Episode(
                number   = num,
                title    = ep_title[:80],
                url      = href,
                site_key = site_key,
            ))

    # Fallback: if no episodes found, the page itself might BE an episode page
    if not episodes:
        episodes.append(Episode(
            number="1", title=title, url=url, site_key=site_key
        ))

    return AnimeDetail(
        title       = title,
        url         = url,
        site_key    = site_key,
        thumbnail   = thumb,
        description = desc,
        genres      = genres,
        episodes    = episodes,
    )


# ── Episode download links ────────────────────────────────────────────────────

async def get_episode_links(
    ep_url: str,
    session: aiohttp.ClientSession,
) -> list[DownloadLink]:
    """
    Scrape an episode page and return structured download links
    with quality detection.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        async with session.get(ep_url, headers=HEADERS, timeout=timeout) as r:
            if r.status != 200:
                return []
            html = await r.text()
    except Exception as e:
        print(f"[Episode links] {e}")
        return []

    soup = BeautifulSoup(html, "lxml")
    links: list[DownloadLink] = []
    seen: set[str] = set()

    def add_link(label: str, href: str):
        if href in seen or not href.startswith("http"):
            return
        seen.add(href)
        quality = detect_quality(label + " " + href)
        host    = detect_host(href)
        # Clean label
        lbl = re.sub(r"\s+", " ", label).strip()[:50] or host
        links.append(DownloadLink(label=lbl, url=href, quality=quality, host=host))

    # 1) Direct links to known download hosts
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if any(d in href for d in DOWNLOAD_DOMAINS):
            add_link(text or href, href)

    # 2) Quality-labelled buttons / divs even if host not in list
    for el in soup.select(
        "a[href], [class*='download'] a, [class*='btn'] a, "
        "[class*='quality'] a, [class*='link'] a"
    ):
        href = el.get("href","").strip()
        text = el.get_text(strip=True)
        if href and re.search(r"\d{3,4}p|download|hd|fhd|quality", text + href, re.I):
            add_link(text, href)

    # 3) <iframe> embed sources
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"].strip()
        if src.startswith("http"):
            add_link("🎬 Stream Embed", src)

    # 4) data-src lazy iframes
    for iframe in soup.find_all("iframe"):
        src = iframe.get("data-src","").strip()
        if src.startswith("http"):
            add_link("🎬 Lazy Embed", src)

    # Sort by quality preference
    quality_order = {"4K / 2160p":0,"1080p FHD":1,"720p HD":2,"480p":3,"360p":4,"Unknown":5}
    links.sort(key=lambda x: quality_order.get(x.quality, 5))

    return links[:12]


# ── Batch episode fetcher (for "All Episodes" feature) ────────────────────────

async def get_all_episodes_links(
    episodes: list[Episode],
    session: aiohttp.ClientSession,
    progress_cb=None,   # async callback(done, total)
) -> list[Episode]:
    """
    Fetch download links for ALL episodes concurrently with rate limiting.
    Returns same episode list but with .download_links filled.
    """
    semaphore = asyncio.Semaphore(8)   # max 8 concurrent requests

    async def fetch_one(ep: Episode, idx: int) -> Episode:
        async with semaphore:
            ep.download_links = await get_episode_links(ep.url, session)
            if progress_cb:
                await progress_cb(idx + 1, len(episodes))
            return ep

    tasks = [fetch_one(ep, i) for i, ep in enumerate(episodes)]
    done  = await asyncio.gather(*tasks)
    return list(done)
