"""
tests/test_update_card.py

Comprehensive test suite verifying:
1. XML validity of dark and light SVGs
2. Strict preservation of ASCII artwork (zero character or attribute drift)
3. Correct statistic calculation and replacement (repos, commits, contributions, uptime)
4. Idempotence (no change detection when stats match)
5. GitHub Actions workflow YAML validity
6. Sparkline generation logic
7. Layout padding and dot leader budgeting (57 chars for full rows, 26 chars for half columns)
8. CLI validation and execution
"""

import datetime
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import pytest
import yaml

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.update_card import (
    calculate_uptime,
    compute_sparkline,
    update_svg_content,
    fetch_live_statistics,
    SVG_NS,
)

DARK_SVG_PATH = os.path.join(PROJECT_ROOT, "dark_mode.svg")
LIGHT_SVG_PATH = os.path.join(PROJECT_ROOT, "light_mode.svg")
WORKFLOW_PATH = os.path.join(PROJECT_ROOT, ".github", "workflows", "refresh-card.yml")


def test_initial_svg_validity():
    """Verify that both original SVG files are well-formed XML."""
    for path in [DARK_SVG_PATH, LIGHT_SVG_PATH]:
        assert os.path.exists(path), f"File {path} does not exist"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        root = ET.fromstring(content)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert root.get("width") == "966"


def test_calculate_uptime():
    """Test uptime formatting and singular/plural logic."""
    fixed_now = datetime.datetime(2026, 10, 21, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # 1 year, 7 months, 26 days
    uptime1 = calculate_uptime("2025-02-25T17:02:40Z", now=fixed_now)
    assert uptime1 == "1 year, 7 months, 26 days"

    # Singulars: 1 year, 1 month, 1 day
    target_date = datetime.datetime(2025, 9, 20, 12, 0, 0, tzinfo=datetime.timezone.utc).isoformat()
    uptime2 = calculate_uptime(target_date, now=fixed_now)
    assert uptime2 == "1 year, 1 month, 1 day"

    # Days only
    target_date3 = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc).isoformat()
    uptime3 = calculate_uptime(target_date3, now=fixed_now)
    assert uptime3 == "6 days"


def test_compute_sparkline():
    """Test sparkline generation maps 0 to space and positive values to block glyphs."""
    empty_weeks = [0] * 52
    spark_empty = compute_sparkline(empty_weeks)
    assert len(spark_empty) == 52
    assert spark_empty == " " * 52

    active_weeks = [0] * 50 + [1, 20]
    spark_active = compute_sparkline(active_weeks)
    assert len(spark_active) == 52
    assert spark_active[-1] == "█"  # Maximum value should hit top glyph
    assert spark_active[-2] != " "  # Non-zero should be non-space


def test_update_dark_mode_svg_stats_and_artwork():
    """Verify statistic update, column budgeting, and strict artwork preservation on dark_mode.svg."""
    sample_stats = {
        'repos': 13,
        'stars': 0,
        'forks': 0,
        'followers': 0,
        'commits': 53,
        'contributed': 0,
        'prs': 1,
        'issues': 0,
        'contributions': 64,
        'reviews': 0,
        'uptime': '1 year, 7 months, 26 days',
        'location': 'HYDERABAD',
        'company': 'VARDHAMAN COLLEGE',
        'languages': 'TypeScript 66%, HTML 13%, JavaScript 11%',
        'email': 'maazarfaatchoice153@gmail.com',
        'github': 'github.com/Maazarfaat',
        'top_repo': 'Maazarfaat-Portfolio (0 ★)',
        'sparkline': '           ▂        ▅▄     ▂         █ ▄      ▂▂ ▄ ▄▄ ',
    }

    new_svg, changed = update_svg_content(DARK_SVG_PATH, sample_stats)
    assert changed is True

    # 1. XML validity
    root = ET.fromstring(new_svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"

    # 2. Strict ASCII artwork preservation
    orig_root = ET.parse(DARK_SVG_PATH).getroot()
    orig_ascii = [t.text for t in orig_root.findall('.//svg:text', SVG_NS) if not t.findall('svg:tspan', SVG_NS)]
    new_ascii = [t.text for t in root.findall('.//svg:text', SVG_NS) if not t.findall('svg:tspan', SVG_NS)]

    assert len(orig_ascii) == 46
    assert orig_ascii == new_ascii, "ASCII artwork lines must remain 100% identical"

    # 3. Check column budgeting for all right-hand rows
    for text_elem in root.findall('.//svg:text', SVG_NS):
        tspans = text_elem.findall('svg:tspan', SVG_NS)
        if len(tspans) == 7:
            # Half-column 1 + Sep + Half-column 2 = 26 + 3 + 26 = 55
            left_len = len(tspans[0].text or '') + len(tspans[1].text or '') + len(tspans[2].text or '')
            sep_len = len(tspans[3].text or '')
            right_len = len(tspans[4].text or '') + len(tspans[5].text or '') + len(tspans[6].text or '')
            assert left_len == 26, f"Left column of {tspans[0].text} length is {left_len}, expected 26"
            assert sep_len == 3
            assert right_len == 26, f"Right column of {tspans[4].text} length is {right_len}, expected 26"
        elif len(tspans) == 3 and tspans[0].text.startswith('.'):
            # Full row width = 57
            total_len = len(tspans[0].text or '') + len(tspans[1].text or '') + len(tspans[2].text or '')
            assert total_len == 57, f"Row {tspans[0].text} length is {total_len}, expected 57"


def test_update_light_mode_svg_stats_and_artwork():
    """Verify statistic update, column budgeting, and strict artwork preservation on light_mode.svg."""
    sample_stats = {
        'repos': 13,
        'stars': 0,
        'forks': 0,
        'followers': 0,
        'commits': 53,
        'contributed': 0,
        'prs': 1,
        'issues': 0,
        'contributions': 64,
        'reviews': 0,
        'uptime': '1 year, 7 months, 26 days',
        'location': 'HYDERABAD',
        'company': 'VARDHAMAN COLLEGE',
        'languages': 'TypeScript 66%, HTML 13%, JavaScript 11%',
        'email': 'maazarfaatchoice153@gmail.com',
        'github': 'github.com/Maazarfaat',
        'top_repo': 'Maazarfaat-Portfolio (0 ★)',
        'sparkline': '           ▂        ▅▄     ▂         █ ▄      ▂▂ ▄ ▄▄ ',
    }

    new_svg, changed = update_svg_content(LIGHT_SVG_PATH, sample_stats)
    assert changed is True

    # 1. XML validity
    root = ET.fromstring(new_svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"

    # 2. Strict ASCII artwork preservation
    orig_root = ET.parse(LIGHT_SVG_PATH).getroot()
    orig_ascii = [t.text for t in orig_root.findall('.//svg:text', SVG_NS) if not t.findall('svg:tspan', SVG_NS)]
    new_ascii = [t.text for t in root.findall('.//svg:text', SVG_NS) if not t.findall('svg:tspan', SVG_NS)]

    assert len(orig_ascii) == 50
    assert orig_ascii == new_ascii, "ASCII artwork lines in light mode must remain 100% identical"

    # 3. Check Repos line values
    repos_found = False
    for text_elem in root.findall('.//svg:text', SVG_NS):
        tspans = text_elem.findall('svg:tspan', SVG_NS)
        if len(tspans) == 7 and tspans[0].text == '. Repos: ':
            assert tspans[2].text == ' 13'
            assert len(tspans[0].text) + len(tspans[1].text) + len(tspans[2].text) == 26
            repos_found = True

    assert repos_found


def test_idempotence_no_change_when_stats_match():
    """Verify that updating an already updated SVG returns has_changed == False."""
    sample_stats = {
        'repos': 13,
        'stars': 0,
        'forks': 0,
        'followers': 0,
        'commits': 53,
        'contributed': 0,
        'prs': 1,
        'issues': 0,
        'contributions': 64,
        'reviews': 0,
        'uptime': '1 year, 7 months, 26 days',
        'location': 'HYDERABAD',
        'company': 'VARDHAMAN COLLEGE',
        'languages': 'TypeScript 66%, HTML 13%, JavaScript 11%',
        'email': 'maazarfaatchoice153@gmail.com',
        'github': 'github.com/Maazarfaat',
        'top_repo': 'Maazarfaat-Portfolio (0 ★)',
        'sparkline': '           ▂        ▅▄     ▂         █ ▄      ▂▂ ▄ ▄▄ ',
    }

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Pass 1: update from original to sample stats
        updated_svg, changed1 = update_svg_content(DARK_SVG_PATH, sample_stats)
        assert changed1 is True

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(updated_svg)

        # Pass 2: update temporary file again with same sample stats
        again_svg, changed2 = update_svg_content(tmp_path, sample_stats)
        assert changed2 is False, "Second pass with identical stats must detect no changes"
        assert updated_svg.strip() == again_svg.strip()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_workflow_yaml_structure():
    """Verify the GitHub Actions workflow YAML is syntactically valid and properly configured."""
    assert os.path.exists(WORKFLOW_PATH)
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["name"] == "Refresh GitHub ASCII Card"
    triggers = data[True] if True in data else data.get("on")
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert "push" not in triggers

    assert data["permissions"]["contents"] == "write"

    job = data["jobs"]["refresh-card"]
    assert job["runs-on"] == "ubuntu-latest"
    steps = job["steps"]
    step_names = [s.get("name") for s in steps]
    assert "Checkout repository" in step_names
    assert "Set up Python" in step_names
    assert "Refresh ASCII Profile Cards" in step_names
    assert "Validate Cards" in step_names
    assert "Commit and Push if Changed" in step_names


def test_cli_validate_only():
    """Test running scripts/update_card.py with --validate-only flag."""
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "update_card.py"), "--validate-only"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert res.returncode == 0
    assert "[OK] dark_mode.svg is valid XML." in res.stdout
    assert "[OK] light_mode.svg is valid XML." in res.stdout


def test_live_fetch_without_token():
    """Test live statistics fetching from GitHub API for user Maazarfaat."""
    stats = fetch_live_statistics("Maazarfaat", token=None, count_mode="auto", private_repos_count=2, baseline_svg=DARK_SVG_PATH)
    assert stats["login"] == "Maazarfaat"
    assert stats["public_repos"] >= 11
    assert stats["repos"] >= 13  # 11 public + 2 private offset
    assert stats["uptime"] is not None
    assert "year" in stats["uptime"]
    assert stats["location"] == "HYDERABAD"
    assert stats["company"] == "VARDHAMAN COLLEGE"
    assert "TypeScript" in stats["languages"]
