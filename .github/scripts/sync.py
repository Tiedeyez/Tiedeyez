#!/usr/bin/env python3
"""Keep the Tiedeyez profile, tiedeyez.dev and the Omarchy marketplace in step.

Runs hourly in GitHub Actions (see .github/workflows/sync.yml) and can be run by hand:

    GITHUB_TOKEN=... python3 -I .github/scripts/sync.py

It reads three public sources:
  - the Omarchy plugin marketplace catalog (which of my plugins are listed, and their state)
  - the GitHub API for each plugin repo (latest release, stars, description)
  - my public GitHub contribution graph

and writes:
  - data/plugins.json   every listed plugin, newest facts first-class (tiedeyez.dev reads this)
  - data/contrib.json   the contribution graph (tiedeyez.dev reads this)
  - README.md           the plugin table between the plugins:start / plugins:end markers

A plugin appears here once the marketplace lists it, so a new listing shows up on the
profile and on tiedeyez.dev within the hour with no other step.
"""
import json, os, re, sys, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

OWNER = "Tiedeyez"
ROOT = Path(__file__).resolve().parents[2]
CATALOG = "https://plugins.omarchy.org/catalog.json"
MARKET = "https://omarchyplugins.com"
SITE = "https://tiedeyez.dev"
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def fetch(url, limit, accept=None, auth=False):
    headers = {"User-Agent": "tiedeyez-sync"}
    if accept:
        headers["Accept"] = accept
    if auth and TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"{url} is larger than {limit} bytes")
    return data


def gh(path):
    try:
        return json.loads(fetch("https://api.github.com" + path, 2_000_000, "application/vnd.github+json", auth=True))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def mine(entry):
    repo = str(entry.get("repo", ""))
    return (str(entry.get("author", "")).lower() == OWNER.lower()
            or str(entry.get("id", "")).startswith(OWNER.lower() + ".")
            or repo.lower().startswith(f"https://github.com/{OWNER.lower()}/"))


def plugins():
    catalog = json.loads(fetch(CATALOG, 64_000_000))
    entries = catalog if isinstance(catalog, list) else catalog.get("plugins", [])
    out = []
    for e in filter(mine, entries):
        m = re.match(r"https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?$", str(e.get("repo", "")))
        if not m:
            continue
        owner, key = m.group(1), m.group(2)
        repo = gh(f"/repos/{owner}/{key}") or {}
        rel = gh(f"/repos/{owner}/{key}/releases/latest")
        thumb = e.get("previewThumbnail") or e.get("previewImage")
        name = e.get("name") or key
        desc = repo.get("description") or e.get("description", "")
        if desc.lower().startswith(name.lower() + ":"):  # "Tidal Falls: a 16-bit..." reads twice next to the name
            desc = desc[len(name) + 1:].strip()
            desc = desc[:1].upper() + desc[1:]
        out.append({
            "id": e["id"],
            "key": key,
            "name": name,
            "repo": f"https://github.com/{owner}/{key}",
            "description": desc,
            "category": e.get("category", ""),
            "tags": e.get("tags", [])[:3],
            "install": e.get("installCommand") or f"omarchy plugin add https://github.com/{owner}/{key}.git --enable",
            "marketplace": f"{MARKET}/plugin.html?id={e['id']}",
            "listed": (e.get("listedAt") or e.get("addedAt") or "")[:10],
            # the reviewed snapshot is the newest commit, or a newer one is waiting for review
            "verified": bool(e.get("verificationCommit")) and e.get("verificationCommit") == e.get("upstreamObservedCommit"),
            "preview": f"https://plugins.omarchy.org/{thumb}" if thumb and re.match(r"^assets/img/plugins/[\w.-]+\.webp$", thumb) else None,
            "version": rel["tag_name"] if rel else (("v" + e["version"]) if e.get("version") else None),
            "released": rel["published_at"][:10] if rel else None,
            "releaseUrl": rel["html_url"] if rel else None,
            "stars": repo.get("stargazers_count", e.get("stars", 0)),
            "pushed": (repo.get("pushed_at") or "")[:10],
        })
    out.sort(key=lambda p: (p["listed"] or "9999", p["id"]))
    return out


def contrib():
    html = fetch(f"https://github.com/users/{OWNER}/contributions", 2_000_000).decode("utf-8", "replace")
    cells = re.findall(r'data-date="(\d{4}-\d\d-\d\d)" id="contribution-day-component-(\d)-(\d+)" data-level="([0-4])"', html)
    tips = {(w, d): t for d, w, t in re.findall(r'for="contribution-day-component-(\d)-(\d+)"[^>]*>([^<]*)</tool-tip>', html)}
    if len(cells) < 300:
        raise RuntimeError(f"only {len(cells)} contribution days found; GitHub's markup may have changed")
    days = []
    for day, wd, wk, level in cells:
        n = re.match(r"(\d[\d,]*) contribution", tips.get((wk, wd), ""))
        days.append({"d": day, "l": int(level), "c": int(n.group(1).replace(",", "")) if n else 0})
    days.sort(key=lambda x: x["d"])
    best = max(days, key=lambda x: x["c"])
    return {"user": OWNER, "total": sum(x["c"] for x in days), "activeDays": sum(1 for x in days if x["c"]), "best": best, "days": days}


WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def readme(ps):
    n = len(ps)
    count = WORDS[n] if n < len(WORDS) else str(n)
    rows = ["| | Plugin | What it is | Latest |", "| --- | --- | --- | --- |"]
    for i, p in enumerate(ps, 1):
        latest = f"[{p['version']}]({p['releaseUrl']})" if p.get("releaseUrl") else (p.get("version") or "")
        desc = p["description"].replace("|", "\\|")
        rows.append(f"| {i:02d} | **[{p['name']}]({p['repo']})** | {desc} | {latest} |")
    first = ps[0]["install"] if ps else ""
    block = "\n".join([
        f"Plugins, themes, and small quiet things for [Omarchy](https://omarchy.org). "
        f"{'All ' + count + ' are' if n > 1 else 'It is'} listed on the [Omarchy plugin marketplace]({MARKET}/), "
        "and every repo ships a `DISCLOSURE.md` saying what it reads, writes and runs.",
        "",
        *rows,
        "",
        "Install any of them with one command, for example:",
        "",
        "```bash",
        first,
        "```",
        "",
        f"More, with a live theme switcher, at **[tiedeyez.dev]({SITE})**.",
    ])
    path = ROOT / "README.md"
    text = path.read_text()
    new = re.sub(r"(<!-- plugins:start -->\n).*?(\n<!-- plugins:end -->)", lambda m: m.group(1) + block + m.group(2), text, flags=re.S)
    if new == text and "<!-- plugins:start -->" not in text:
        raise RuntimeError("README.md has no plugins:start / plugins:end markers")
    path.write_text(new)


def write_json(name, obj):
    path = ROOT / "data" / name
    path.parent.mkdir(exist_ok=True)
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    old = path.read_text() if path.exists() else ""
    # only rewrite when something real changed, so the hourly job does not commit noise
    if json.loads(old or "null") != json.loads(body):
        path.write_text(body)
        return True
    return False


def main():
    ps = plugins()
    if not ps:
        print("no plugins found in the catalog; leaving everything as it was", file=sys.stderr)
        return 1
    changed = write_json("plugins.json", {"plugins": ps})
    try:
        changed |= write_json("contrib.json", contrib())
    except Exception as e:  # the graph is decoration; never block plugin updates on it
        print(f"contribution graph skipped: {e}", file=sys.stderr)
    readme(ps)
    print(f"{len(ps)} plugins: " + ", ".join(f"{p['name']} {p['version'] or ''}".strip() for p in ps) + ("" if changed else " (data unchanged)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
