#!/usr/bin/env python3
"""
scripts/update_card.py

Fetches live statistics from the GitHub API and updates the ASCII GitHub
Profile Card SVGs (both dark_mode.svg and light_mode.svg) while preserving:
- Original ASCII artwork lines and character positions
- Theme colors and styling
- Layout, column budgeting, and dot leaders
- XML structure and validity

Supports:
- Unauthenticated public mode with graceful rate-limit fallbacks
- Built-in GITHUB_TOKEN (actions runner)
- Personal Access Token (for including private repo counts)
- Safe validation before overwriting
- Non-destructive updates
"""

import argparse
import datetime
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Ensure stdout and stderr handle UTF-8 characters cleanly across all OS platforms
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Register empty SVG namespace for clean serialization
ET.register_namespace('', 'http://www.w3.org/2000/svg')
SVG_NS = {'svg': 'http://www.w3.org/2000/svg'}

# Sparkline glyphs (standard 8-level blocks)
SPARK_RAMP = " ▁▂▃▄▅▆▇█"

# Column widths based on gh-ascii specifications
HALF_COL_WIDTH = 26
FULL_ROW_WIDTH = 57


def request_json(url, token=None, timeout=15):
    """Fetch JSON from GitHub API with error and rate limit handling."""
    headers = {
        'User-Agent': 'GitHub-Profile-Card-Updater',
        'Accept': 'application/vnd.github.v3+json',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('utf-8')
            return json.loads(data), resp.headers
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        print(f"[Warning] HTTP {e.code} for {url}: {body[:200]}", file=sys.stderr)
        return None, e.headers
    except Exception as e:
        print(f"[Warning] Request failed for {url}: {e}", file=sys.stderr)
        return None, None


def calculate_uptime(created_at_str, now=None):
    """
    Calculate account uptime in 'X years, Y months, Z days'
    matching gh-ascii accountUptime logic.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    created = datetime.datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

    years = now.year - created.year
    months = now.month - created.month
    days = now.day - created.day

    if days < 0:
        months -= 1
        first_of_cur_month = datetime.date(now.year, now.month, 1)
        last_of_prev_month = first_of_cur_month - datetime.timedelta(days=1)
        days += last_of_prev_month.day

    if months < 0:
        years -= 1
        months += 12

    parts = []
    if years > 0:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months > 0:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    parts.append(f"{days} day{'s' if days != 1 else ''}")
    return ", ".join(parts)


def compute_sparkline(weeks):
    """Generate 52-week activity sparkline using log-scale bucket mapping."""
    recent = weeks[-52:] if len(weeks) >= 52 else [0] * (52 - len(weeks)) + weeks
    max_count = max(recent) if max(recent) > 0 else 1
    chars = []
    for count in recent:
        if count <= 0:
            chars.append(SPARK_RAMP[0])
        else:
            level = 1 + int((math.log1p(count) / math.log1p(max_count)) * 7)
            chars.append(SPARK_RAMP[max(1, min(8, level))])
    return "".join(chars)


def scrape_contributions(username, timeout=15):
    """
    Scrape live contribution calendar from https://github.com/users/{username}/contributions
    Returns: (total_contributions, sparkline_str)
    """
    url = f"https://github.com/users/{username}/contributions"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode('utf-8')
    except Exception as e:
        print(f"[Warning] Could not fetch contributions calendar: {e}", file=sys.stderr)
        return None, None

    # Total contributions text
    m_total = re.search(r'(\d+[\d,]*)\s+contributions?\s+in', html)
    total_contrib = int(m_total.group(1).replace(",", "")) if m_total else None

    # Daily counts from table cells
    days = []
    for m in re.finditer(r'<td[^>]*data-date="([^"]+)"[^>]*>.*?</td>', html, re.DOTALL):
        td_html = m.group(0)
        c_match = re.search(r'(\d+)\s+contributions?', td_html)
        count = int(c_match.group(1)) if c_match else 0
        days.append(count)

    sparkline_str = None
    if days:
        weeks = [sum(days[i:i + 7]) for i in range(0, len(days), 7)]
        sparkline_str = compute_sparkline(weeks)

    return total_contrib, sparkline_str


def scrape_public_profile(username, timeout=15):
    """
    Fallback scraper for public profile https://github.com/{username}
    when REST API hits rate limits.
    """
    url = f"https://github.com/{username}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode('utf-8')
    except Exception as e:
        print(f"[Warning] Fallback profile scrape failed: {e}", file=sys.stderr)
        return {}

    data = {}
    m_repos = re.search(r'Repositories\s*<span[^>]*class="Counter[^>]*>(\d+)</span>', html)
    if m_repos:
        data['public_repos'] = int(m_repos.group(1))

    m_comp = re.search(r'aria-label="Organization:\s*([^"]+)"', html)
    if not m_comp:
        m_comp = re.search(r'class="p-org"[^>]*>(?:<div>)?([^<]+)', html)
    if m_comp:
        data['company'] = m_comp.group(1).strip()

    m_loc = re.search(r'aria-label="Home location:\s*([^"]+)"', html)
    if not m_loc:
        m_loc = re.search(r'itemprop="homeLocation"[^>]*>.*?<span>([^<]+)</span>', html, re.DOTALL)
    if m_loc:
        data['location'] = m_loc.group(1).strip()

    m_foll = re.search(r'href="[^"]*tab=followers"[^>]*>.*?<span[^>]*class="text-bold[^>]*>(\d+)</span>', html, re.DOTALL)
    if m_foll:
        data['followers'] = int(m_foll.group(1))

    return data


def extract_existing_stats(svg_path):
    """Extract baseline stats from an existing SVG file."""
    if not os.path.exists(svg_path):
        return {}

    stats = {}
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        for text_elem in root.findall('.//svg:text', SVG_NS):
            tspans = text_elem.findall('svg:tspan', SVG_NS)
            if not tspans:
                continue
            first_text = tspans[0].text or ""

            if len(tspans) == 7:
                if first_text == '. Repos: ':
                    if tspans[2].text and tspans[2].text.strip().isdigit():
                        stats['repos'] = int(tspans[2].text.strip())
                    if tspans[6].text and tspans[6].text.strip().isdigit():
                        stats['stars'] = int(tspans[6].text.strip())
                elif first_text == '. Forks: ':
                    if tspans[2].text and tspans[2].text.strip().isdigit():
                        stats['forks'] = int(tspans[2].text.strip())
                    if tspans[6].text and tspans[6].text.strip().isdigit():
                        stats['followers'] = int(tspans[6].text.strip())
                elif first_text == '. Commits: ':
                    if tspans[2].text and tspans[2].text.strip().isdigit():
                        stats['commits'] = int(tspans[2].text.strip())
                    if tspans[6].text and tspans[6].text.strip().isdigit():
                        stats['contributed'] = int(tspans[6].text.strip())
                elif first_text == '. PRs: ':
                    if tspans[2].text and tspans[2].text.strip().isdigit():
                        stats['prs'] = int(tspans[2].text.strip())
                    if tspans[6].text and tspans[6].text.strip().isdigit():
                        stats['issues'] = int(tspans[6].text.strip())
                elif first_text == '. Contributions: ':
                    if tspans[2].text and tspans[2].text.strip().isdigit():
                        stats['contributions'] = int(tspans[2].text.strip())
                    if tspans[6].text and tspans[6].text.strip().isdigit():
                        stats['reviews'] = int(tspans[6].text.strip())

            elif len(tspans) == 3:
                val = tspans[2].text.strip() if tspans[2].text else None
                if first_text == '. Uptime: ':
                    stats['uptime'] = val
                elif first_text == '. Location: ':
                    stats['location'] = val
                elif first_text == '. Company: ':
                    stats['company'] = val
                elif first_text == '. Languages: ':
                    stats['languages'] = val
                elif first_text == '. Email: ':
                    stats['email'] = val
                elif first_text == '. GitHub: ':
                    stats['github'] = val
                elif first_text == '. Top repo: ':
                    stats['top_repo'] = val

            elif len(tspans) == 1:
                t = tspans[0].text or ""
                if any(ch in t for ch in SPARK_RAMP):
                    stats['sparkline'] = t.strip()
    except Exception as e:
        print(f"[Warning] Could not extract baseline stats from {svg_path}: {e}", file=sys.stderr)

    return stats


def fetch_live_statistics(username, token=None, count_mode="auto", private_repos_count=None, baseline_svg=None):
    """
    Fetch comprehensive live GitHub stats for username.
    Handles public, authenticated, and GraphQL endpoints with resilient fallbacks.
    """
    if baseline_svg is None and os.path.exists("dark_mode.svg"):
        baseline_svg = "dark_mode.svg"

    # Initialize with baseline stats from existing SVG if available
    stats = extract_existing_stats(baseline_svg) if baseline_svg else {}
    stats['login'] = username

    # Known creation date for Maazarfaat (fallback if API is rate-limited)
    default_created_at = "2025-02-25T17:02:40Z"
    created_at = default_created_at

    # 1. Fetch user profile
    user_data, _ = request_json(f"https://api.github.com/users/{username}", token=token)
    if user_data:
        stats['login'] = user_data.get('login', username)
        if user_data.get('created_at'):
            created_at = user_data.get('created_at')
        if user_data.get('location'):
            stats['location'] = user_data['location'].strip()
        if user_data.get('company'):
            stats['company'] = user_data['company'].strip()
        if 'followers' in user_data:
            stats['followers'] = user_data['followers']
        if user_data.get('email'):
            stats['email'] = user_data['email'].strip()
        public_repos = user_data.get('public_repos', stats.get('public_repos', 11))
        stats['public_repos'] = public_repos
    else:
        print(f"[Info] GitHub API rate-limited or unavailable. Running fallback profile scrape for {username}...")
        fallback_data = scrape_public_profile(username)
        public_repos = fallback_data.get('public_repos', stats.get('public_repos', 11))
        stats['public_repos'] = public_repos
        if fallback_data.get('location'):
            stats['location'] = fallback_data['location']
        if fallback_data.get('company'):
            stats['company'] = fallback_data['company']
        if 'followers' in fallback_data:
            stats['followers'] = fallback_data['followers']

    # Update uptime using accurate created_at
    stats['uptime'] = calculate_uptime(created_at)

    # 2. Check for private repository access via /user
    total_private = 0
    if token:
        auth_user, _ = request_json("https://api.github.com/user", token=token)
        if auth_user and auth_user.get('login', '').lower() == username.lower():
            total_private = auth_user.get('total_private_repos', 0)
            print(f"[Info] Authenticated as {username}: found {total_private} private repos")

    # Override or augment private repos count if provided
    if private_repos_count is not None and private_repos_count >= 0:
        total_private = private_repos_count

    if count_mode == "public":
        stats['repos'] = public_repos
    elif count_mode == "all":
        stats['repos'] = public_repos + total_private
    else:  # "auto"
        stats['repos'] = public_repos + total_private

    stats['private_repos'] = total_private

    # 3. Fetch repositories to calculate stars, forks, languages, and top repo
    page = 1
    all_repos = []
    while True:
        repo_url = f"https://api.github.com/users/{username}/repos?per_page=100&page={page}&type=owner"
        page_repos, _ = request_json(repo_url, token=token)
        if not page_repos:
            break
        all_repos.extend(page_repos)
        if len(page_repos) < 100:
            break
        page += 1

    if all_repos:
        total_stars = 0
        total_forks = 0
        lang_bytes = {}

        for r in all_repos:
            stargazers = r.get('stargazers_count', 0)
            forks = r.get('forks_count', 0)
            total_stars += stargazers
            total_forks += forks

            r_name = r.get('name')
            if r_name:
                lang_data, _ = request_json(f"https://api.github.com/repos/{username}/{r_name}/languages", token=token)
                if lang_data and isinstance(lang_data, dict):
                    for lang, b in lang_data.items():
                        lang_bytes[lang] = lang_bytes.get(lang, 0) + b

        sorted_repos = sorted(
            all_repos,
            key=lambda r: (
                r.get('stargazers_count', 0),
                r.get('forks_count', 0),
                r.get('pushed_at', '')
            ),
            reverse=True
        )

        top_repo_node = None
        if sorted_repos:
            if sorted_repos[0].get('stargazers_count', 0) > 0:
                top_repo_node = sorted_repos[0]
            else:
                portfolio_candidates = [r for r in sorted_repos if 'portfolio' in (r.get('name') or '').lower()]
                top_repo_node = portfolio_candidates[0] if portfolio_candidates else sorted_repos[0]

        stats['stars'] = total_stars
        stats['forks'] = total_forks

        total_code_bytes = sum(lang_bytes.values())
        if total_code_bytes > 0:
            sorted_langs = sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)
            top_langs = []
            for name, b in sorted_langs:
                pct = round((b / total_code_bytes) * 100)
                if pct >= 1:
                    top_langs.append(f"{name} {pct}%")
            stats['languages'] = ", ".join(top_langs[:3])

        if top_repo_node:
            r_name = top_repo_node.get('name')
            r_stars = top_repo_node.get('stargazers_count', 0)
            stats['top_repo'] = f"{r_name} ({r_stars} ★)"

    # 4. Search API for PRs and Issues
    pr_data, _ = request_json(f"https://api.github.com/search/issues?q=author:{username}+type:pr", token=token)
    if pr_data and 'total_count' in pr_data:
        stats['prs'] = pr_data['total_count']
    elif 'prs' not in stats:
        stats['prs'] = 1

    issue_data, _ = request_json(f"https://api.github.com/search/issues?q=author:{username}+type:issue", token=token)
    if issue_data and 'total_count' in issue_data:
        stats['issues'] = issue_data['total_count']
    elif 'issues' not in stats:
        stats['issues'] = 0

    # 5. Commits & Contributions
    graphql_success = False
    if token:
        gql_query = """
        query($login: String!) {
          user(login: $login) {
            contributionsCollection {
              totalCommitContributions
              totalPullRequestReviewContributions
              restrictedContributionsCount
              contributionCalendar {
                totalContributions
                weeks {
                  contributionDays {
                    contributionCount
                  }
                }
              }
            }
          }
        }
        """
        gql_url = "https://api.github.com/graphql"
        req = urllib.request.Request(
            gql_url,
            data=json.dumps({"query": gql_query, "variables": {"login": username}}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "GitHub-Profile-Card-Updater",
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                gql_res = json.loads(res.read().decode("utf-8"))
                user_gql = gql_res.get("data", {}).get("user")
                if user_gql:
                    c_col = user_gql.get("contributionsCollection", {})
                    stats['commits'] = c_col.get('totalCommitContributions', stats.get('commits', 53))
                    stats['reviews'] = c_col.get('totalPullRequestReviewContributions', 0)
                    calendar = c_col.get("contributionCalendar", {})
                    stats['contributions'] = calendar.get("totalContributions", stats.get('contributions', 64))
                    weeks_data = calendar.get("weeks", [])
                    if weeks_data:
                        weekly_sums = [
                            sum(d.get("contributionCount", 0) for d in w.get("contributionDays", []))
                            for w in weeks_data
                        ]
                        stats['sparkline'] = compute_sparkline(weekly_sums)
                    graphql_success = True
                    print(f"[Info] GraphQL stats: commits={stats['commits']}, contrib={stats['contributions']}")
        except Exception as e:
            print(f"[Warning] GraphQL query failed: {e}", file=sys.stderr)

    if not graphql_success:
        scraped_contrib, scraped_spark = scrape_contributions(username)
        if scraped_contrib is not None:
            stats['contributions'] = scraped_contrib
        if scraped_spark and any(ch in scraped_spark for ch in "▁▂▃▄▅▆▇█"):
            stats['sparkline'] = scraped_spark

        # Fallback commits: protect existing commits count from regression
        commit_data, _ = request_json(
            f"https://api.github.com/search/commits?q=author:{username}",
            token=token
        )
        if commit_data and 'total_count' in commit_data:
            current_c = stats.get('commits', 0) or 0
            stats['commits'] = max(current_c, commit_data['total_count'])

    if 'reviews' not in stats:
        stats['reviews'] = 0
    if 'contributed' not in stats:
        stats['contributed'] = 0

    return stats


def update_svg_content(svg_path, stats):
    """
    Parses svg_path, replaces right-column stats according to design constraints,
    verifies ASCII artwork preservation and XML validity, and returns
    (updated_svg_string, has_changed).
    """
    with open(svg_path, 'r', encoding='utf-8') as f:
        original_content = f.read()

    tree = ET.parse(svg_path)
    root = tree.getroot()

    half = HALF_COL_WIDTH
    full = FULL_ROW_WIDTH

    for text_elem in root.findall('.//svg:text', SVG_NS):
        tspans = text_elem.findall('svg:tspan', SVG_NS)
        if not tspans:
            continue

        first_text = tspans[0].text or ""

        # 2-column stats lines (7 tspans: label1, dots1, val1, sep, label2, dots2, val2)
        if len(tspans) == 7:
            if first_text == '. Repos: ':
                if stats.get('repos') is not None:
                    val1 = f" {stats['repos']}"
                    tspans[1].text = "." * max(2, half - len(tspans[0].text) - len(val1))
                    tspans[2].text = val1
                if stats.get('stars') is not None:
                    val2 = f" {stats['stars']}"
                    tspans[5].text = "." * max(2, half - len(tspans[4].text) - len(val2))
                    tspans[6].text = val2

            elif first_text == '. Forks: ':
                if stats.get('forks') is not None:
                    val1 = f" {stats['forks']}"
                    tspans[1].text = "." * max(2, half - len(tspans[0].text) - len(val1))
                    tspans[2].text = val1
                if stats.get('followers') is not None:
                    val2 = f" {stats['followers']}"
                    tspans[5].text = "." * max(2, half - len(tspans[4].text) - len(val2))
                    tspans[6].text = val2

            elif first_text == '. Commits: ':
                current_val = int(tspans[2].text.strip()) if tspans[2].text and tspans[2].text.strip().isdigit() else 0
                new_commits = stats.get('commits')
                target_commits = max(current_val, new_commits) if new_commits is not None else current_val
                val1 = f" {target_commits}"
                tspans[1].text = "." * max(2, half - len(tspans[0].text) - len(val1))
                tspans[2].text = val1
                if stats.get('contributed') is not None:
                    val2 = f" {stats['contributed']}"
                    tspans[5].text = "." * max(2, half - len(tspans[4].text) - len(val2))
                    tspans[6].text = val2

            elif first_text == '. PRs: ':
                if stats.get('prs') is not None:
                    val1 = f" {stats['prs']}"
                    tspans[1].text = "." * max(2, half - len(tspans[0].text) - len(val1))
                    tspans[2].text = val1
                if stats.get('issues') is not None:
                    val2 = f" {stats['issues']}"
                    tspans[5].text = "." * max(2, half - len(tspans[4].text) - len(val2))
                    tspans[6].text = val2

            elif first_text == '. Contributions: ':
                if stats.get('contributions') is not None:
                    val1 = f" {stats['contributions']}"
                    tspans[1].text = "." * max(2, half - len(tspans[0].text) - len(val1))
                    tspans[2].text = val1
                if stats.get('reviews') is not None:
                    val2 = f" {stats['reviews']}"
                    tspans[5].text = "." * max(2, half - len(tspans[4].text) - len(val2))
                    tspans[6].text = val2

        # 3-span single item lines (label, dots, value)
        elif len(tspans) == 3:
            key = None
            if first_text == '. Uptime: ':
                key = 'uptime'
            elif first_text == '. Location: ':
                key = 'location'
            elif first_text == '. Company: ':
                key = 'company'
            elif first_text == '. Languages: ':
                key = 'languages'
            elif first_text == '. Email: ':
                key = 'email'
            elif first_text == '. GitHub: ':
                key = 'github'
            elif first_text == '. Top repo: ':
                key = 'top_repo'

            if key and key in stats and stats[key] is not None:
                val = f" {stats[key]}"
                dot_count = max(2, full - len(tspans[0].text) - len(val))
                tspans[1].text = "." * dot_count
                tspans[2].text = val

        # 1-span sparkline
        elif len(tspans) == 1 and stats.get('sparkline'):
            t = tspans[0].text or ""
            if any(ch in t for ch in SPARK_RAMP):
                tspans[0].text = f"  {stats['sparkline']}"

    # Serialize to string
    new_svg = ET.tostring(root, encoding='utf-8').decode('utf-8')

    # XML Validation
    parsed = ET.fromstring(new_svg)
    if parsed.tag != '{http://www.w3.org/2000/svg}svg':
        raise ValueError("Invalid root tag in generated SVG")

    # Verify ASCII lines are identical
    orig_ascii = [t.text for t in ET.fromstring(original_content).findall('.//svg:text', SVG_NS) if not t.findall('svg:tspan', SVG_NS)]
    new_ascii = [t.text for t in parsed.findall('.//svg:text', SVG_NS) if not t.findall('svg:tspan', SVG_NS)]
    if orig_ascii != new_ascii:
        raise ValueError("ASCII artwork was modified during update! Aborting.")

    has_changed = (new_svg.strip() != original_content.strip())
    return new_svg, has_changed


def main():
    parser = argparse.ArgumentParser(description="Update GitHub ASCII Profile Card statistics.")
    parser.add_argument("--username", default=os.environ.get("GITHUB_ACTOR", "Maazarfaat"), help="GitHub username")
    parser.add_argument("--dark-svg", default="dark_mode.svg", help="Path to dark mode SVG")
    parser.add_argument("--light-svg", default="light_mode.svg", help="Path to light mode SVG")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("PROFILE_CARD_PAT"), help="GitHub token")
    parser.add_argument("--count-mode", choices=["auto", "all", "public"], default="auto", help="Repository count mode (auto includes private if accessible)")
    parser.add_argument("--private-repos-count", type=int, default=int(os.environ.get("PRIVATE_REPOS_COUNT")) if os.environ.get("PRIVATE_REPOS_COUNT") else None, help="Manual count of private repos if running without PAT")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without saving files")
    parser.add_argument("--validate-only", action="store_true", help="Validate existing SVGs without fetching or updating")

    args = parser.parse_args()

    if args.validate_only:
        for path in [args.dark_svg, args.light_svg]:
            with open(path, 'r', encoding='utf-8') as f:
                ET.fromstring(f.read())
            print(f"[OK] {path} is valid XML.")
        return 0

    print(f"=== Refreshing ASCII Profile Cards for {args.username} ===")
    print(f"Count mode: {args.count_mode} | Private offset: {args.private_repos_count}")

    # Fetch live stats using baseline from dark SVG
    stats = fetch_live_statistics(
        args.username,
        token=args.token,
        count_mode=args.count_mode,
        private_repos_count=args.private_repos_count,
        baseline_svg=args.dark_svg
    )

    print("\nFetched Live Statistics:")
    for k, v in stats.items():
        if k != 'sparkline':
            print(f"  {k:15}: {v}")
        else:
            print(f"  {k:15}: {repr(v)}")

    total_changed = 0
    for path in [args.dark_svg, args.light_svg]:
        if not os.path.exists(path):
            print(f"[Error] File not found: {path}", file=sys.stderr)
            return 1

        new_svg, changed = update_svg_content(path, stats)
        if changed:
            total_changed += 1
            print(f"[Updated] {path} has changes.")
            if not args.dry_run:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_svg)
                print(f"[Saved] Wrote updated SVG to {path}.")
        else:
            print(f"[No Change] {path} already up-to-date.")

    print(f"\nCompleted. Files modified: {total_changed}/2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
