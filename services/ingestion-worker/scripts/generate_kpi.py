#!/usr/bin/env python3
"""
Generate KPI Dashboard - Static HTML with velocity metrics and ingestion health.

Generates a self-contained HTML file with Chart.js visualizations.
Run after each ingestion to keep the dashboard current.

Usage:
    python generate_kpi.py                    # Generate kpi.html
    python generate_kpi.py --output /path/to  # Custom output path
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.couchbase_client import CouchbaseClient
from config import WorkerConfig

config = WorkerConfig()


def query_stats(cb_client: CouchbaseClient) -> dict:
    """Query all stats needed for the dashboard."""
    stats = {}
    bucket = config.couchbase_bucket

    # Total repos
    result = cb_client.cluster.query(f"SELECT COUNT(DISTINCT repo_id) as cnt FROM `{bucket}`")
    stats['total_repos'] = list(result)[0]['cnt']

    # Total documents by type
    result = cb_client.cluster.query(f"""
        SELECT type, COUNT(*) as cnt FROM `{bucket}` GROUP BY type
    """)
    stats['doc_types'] = {r['type']: r['cnt'] for r in result}
    stats['total_docs'] = sum(stats['doc_types'].values())
    stats['total_commits'] = stats['doc_types'].get('commit_index', 0)

    # Commits per month (last 12 months)
    cutoff = (datetime.now() - timedelta(days=365)).strftime('%Y-%m')
    result = cb_client.cluster.query(f"""
        SELECT SUBSTR(commit_date, 0, 7) as month,
               COUNT(*) as commits,
               SUM(metadata.lines_added) as added,
               SUM(metadata.lines_deleted) as deleted
        FROM `{bucket}`
        WHERE type = 'commit_index' AND commit_date >= '{cutoff}'
        GROUP BY SUBSTR(commit_date, 0, 7)
        ORDER BY month
    """)
    stats['monthly'] = list(result)

    # This month vs last month
    now = datetime.now()
    this_month = now.strftime('%Y-%m')
    last_month = (now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')

    this_data = next((m for m in stats['monthly'] if m['month'] == this_month), {})
    last_data = next((m for m in stats['monthly'] if m['month'] == last_month), {})

    stats['commits_this_month'] = this_data.get('commits', 0)
    stats['commits_last_month'] = last_data.get('commits', 0)
    stats['loc_this_month'] = (this_data.get('added') or 0) - (this_data.get('deleted') or 0)

    # Repos created in 2025
    result = cb_client.cluster.query(f"""
        SELECT COUNT(DISTINCT repo_id) as cnt
        FROM `{bucket}`
        WHERE type = 'commit_index'
        GROUP BY repo_id
        HAVING MIN(commit_date) >= '2025-01'
    """)
    stats['repos_this_year'] = len(list(result))

    # Recent ingestion runs
    result = cb_client.cluster.query(f"""
        SELECT * FROM `{bucket}`
        WHERE type = 'ingestion_run'
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    stats['runs'] = list(result)

    # Velocity comparison by quarter (last 4 quarters)
    result = cb_client.cluster.query(f"""
        SELECT
            SUBSTR(commit_date, 0, 4) || '-Q' ||
            TOSTRING(CEIL(TONUMBER(SUBSTR(commit_date, 5, 2)) / 3)) as quarter,
            COUNT(*) as commits
        FROM `{bucket}`
        WHERE type = 'commit_index' AND commit_date >= '2024-01'
        GROUP BY SUBSTR(commit_date, 0, 4) || '-Q' ||
            TOSTRING(CEIL(TONUMBER(SUBSTR(commit_date, 5, 2)) / 3))
        ORDER BY quarter
    """)
    stats['quarters'] = {r['quarter']: r['commits'] for r in result}

    # Cutoff for last 3 months
    three_months_ago = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    # Top repos by commits (last 3 months)
    result = cb_client.cluster.query(f"""
        SELECT repo_id, COUNT(*) as commits
        FROM `{bucket}`
        WHERE type = 'commit_index' AND commit_date >= '{three_months_ago}'
        GROUP BY repo_id
        ORDER BY commits DESC
        LIMIT 10
    """)
    stats['top_repos'] = list(result)

    # Top authors by commits (last 3 months)
    result = cb_client.cluster.query(f"""
        SELECT author, COUNT(*) as commits
        FROM `{bucket}`
        WHERE type = 'commit_index' AND commit_date >= '{three_months_ago}'
        GROUP BY author
        ORDER BY commits DESC
        LIMIT 10
    """)
    stats['top_authors'] = list(result)

    return stats


def compute_verdict(stats: dict) -> tuple:
    """Compute acceleration verdict based on rolling 3-month windows."""
    monthly = stats.get('monthly', [])

    if len(monthly) < 4:
        return ("INSUFFICIENT DATA", "steady", "❓", "Need 4+ months of data")

    # Build month -> commits lookup
    month_commits = {m['month']: m['commits'] for m in monthly}

    # Get last 6 months in order
    now = datetime.now()
    months = []
    for i in range(6):
        d = now.replace(day=1) - timedelta(days=i * 30)
        months.append(d.strftime('%Y-%m'))
    months.reverse()  # oldest first

    # Current window: last 3 complete months (exclude current partial month)
    # Previous window: 3 months before that
    current_month = now.strftime('%Y-%m')

    # Find complete months (exclude current)
    complete_months = [m for m in months if m != current_month and m in month_commits]

    if len(complete_months) < 6:
        # Fall back to whatever we have
        complete_months = [m['month'] for m in monthly if m['month'] != current_month][-6:]

    if len(complete_months) < 4:
        return ("INSUFFICIENT DATA", "steady", "❓", "Need more monthly data")

    # Last 3 complete months vs previous 3
    recent_3 = complete_months[-3:]
    prev_3 = complete_months[-6:-3] if len(complete_months) >= 6 else complete_months[:-3]

    recent_commits = sum(month_commits.get(m, 0) for m in recent_3)
    prev_commits = sum(month_commits.get(m, 0) for m in prev_3)

    if prev_commits > 0:
        growth = (recent_commits - prev_commits) / prev_commits
    else:
        growth = 0

    # Format month ranges for display
    recent_label = f"{recent_3[0][-2:]}-{recent_3[-1][-2:]}"
    prev_label = f"{prev_3[0][-2:]}-{prev_3[-1][-2:]}"
    detail = f"L3M: {recent_commits:,} vs P3M: {prev_commits:,}"

    if growth > 0.1:
        return ("ACCELERATING", "accelerating", "📈", f"+{growth*100:.0f}% • {detail}")
    elif growth < -0.1:
        return ("DECELERATING", "decelerating", "📉", f"{growth*100:.0f}% • {detail}")
    else:
        return ("STEADY", "steady", "➡️", f"Stable ({growth*100:+.0f}%) • {detail}")


def generate_html(stats: dict) -> str:
    """Generate the HTML dashboard."""
    # Verdict
    verdict, verdict_class, verdict_emoji, verdict_detail = compute_verdict(stats)

    # Trend for this month vs last
    if stats['commits_last_month'] > 0:
        trend = (stats['commits_this_month'] - stats['commits_last_month']) / stats['commits_last_month']
        trend_pct = f"{trend*100:+.0f}%"
        trend_class = "trend-up" if trend > 0 else "trend-down" if trend < 0 else "trend-neutral"
        trend_icon = "↑" if trend > 0 else "↓" if trend < 0 else "→"
    else:
        trend_pct = "N/A"
        trend_class = "trend-neutral"
        trend_icon = "→"

    # Chart data
    monthly = stats['monthly'][-12:]  # Last 12 months
    commits_labels = json.dumps([m['month'][-5:] for m in monthly])
    commits_data = json.dumps([m['commits'] for m in monthly])
    loc_data = json.dumps([(m.get('added') or 0) - (m.get('deleted') or 0) for m in monthly])

    # Runs table
    runs_rows = ""
    for run in stats['runs']:
        r = run.get('code_kosha', run)
        ts = r.get('timestamp', r.get('started_at', 'N/A'))[:16].replace('T', ' ')
        status = r.get('status', 'unknown')
        status_class = f"status-{status}"
        s = r.get('stats', {})
        repos = s.get('processed', r.get('repos_processed', 0))
        updated = s.get('updated', r.get('repos_updated', 0)) + s.get('full_reingest', r.get('repos_full_reingest', 0))
        skipped = s.get('skipped', r.get('repos_skipped', 0))
        errors = s.get('error', r.get('repos_error', 0))
        duration = r.get('duration_seconds', 0)
        runs_rows += f"""
                    <tr>
                        <td>{ts}</td>
                        <td class="{status_class}">{status}</td>
                        <td>{repos}</td>
                        <td>{updated}</td>
                        <td>{skipped}</td>
                        <td>{errors}</td>
                        <td>{duration:.0f}s</td>
                    </tr>"""

    # Doc type cards
    doc_type_cards = ""
    for doc_type, count in sorted(stats['doc_types'].items(), key=lambda x: -x[1]):
        doc_type_cards += f"""
                        <div class="doc-card">
                            <div class="doc-type">{doc_type.replace('_', ' ')}</div>
                            <div class="doc-count">{count:,}</div>
                        </div>"""

    # Top repos rows
    top_repos_rows = ""
    for i, repo in enumerate(stats.get('top_repos', []), 1):
        repo_name = repo['repo_id'].split('/')[-1] if '/' in repo['repo_id'] else repo['repo_id']
        top_repos_rows += f"""
                    <tr>
                        <td>{i}</td>
                        <td>{repo_name}</td>
                        <td>{repo['commits']:,}</td>
                    </tr>"""

    # Top authors rows
    top_authors_rows = ""
    for i, author in enumerate(stats.get('top_authors', []), 1):
        top_authors_rows += f"""
                    <tr>
                        <td>{i}</td>
                        <td>{author['author']}</td>
                        <td>{author['commits']:,}</td>
                    </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Velocity Dashboard | CodeSmriti</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #fafaf9;
            --text: #1a1a1a;
            --text-muted: #666;
            --accent: #2563eb;
            --border: #e5e5e5;
            --code-bg: #f5f5f4;
            --green: #16a34a;
            --red: #dc2626;
            --yellow: #ca8a04;
            --serif: 'Source Serif 4', Georgia, serif;
            --mono: 'JetBrains Mono', monospace;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html {{ font-size: 18px; }}
        body {{
            font-family: var(--serif);
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
            padding: 2rem 1rem;
        }}
        .container {{ max-width: 52rem; margin: 0 auto; }}
        header {{
            border-bottom: 2px solid var(--text);
            padding-bottom: 1.5rem;
            margin-bottom: 2rem;
        }}
        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        .title {{ font-size: 1.75rem; font-weight: 600; letter-spacing: -0.02em; }}
        .timestamp {{ font-size: 0.85rem; color: var(--text-muted); font-family: var(--mono); }}
        .verdict {{
            background: var(--code-bg);
            border-left: 3px solid var(--text);
            padding: 1.25rem 1.5rem;
            margin-bottom: 2rem;
        }}
        .verdict-label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-muted); font-family: var(--mono); }}
        .verdict-value {{ font-size: 1.5rem; font-weight: 600; margin: 0.5rem 0; }}
        .verdict-detail {{ font-size: 0.9rem; color: var(--text-muted); font-family: var(--mono); }}
        .verdict-accelerating {{ color: var(--green); }}
        .verdict-steady {{ color: var(--yellow); }}
        .verdict-decelerating {{ color: var(--red); }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        @media (max-width: 768px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }} }}
        .metric {{
            background: var(--code-bg);
            padding: 1rem;
        }}
        .metric-label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-muted); font-family: var(--mono); }}
        .metric-value {{ font-size: 1.5rem; font-weight: 600; margin: 0.25rem 0; font-family: var(--mono); }}
        .metric-sub {{ font-size: 0.75rem; color: var(--text-muted); font-family: var(--mono); }}
        .trend-up {{ color: var(--green); }}
        .trend-down {{ color: var(--red); }}
        section {{ margin-bottom: 2.5rem; }}
        h2 {{
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 1rem;
            font-family: var(--mono);
        }}
        h2::before {{ content: "§ "; color: var(--text-muted); }}
        .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }}
        @media (max-width: 768px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
        .chart-container {{
            background: var(--code-bg);
            padding: 1rem;
            height: 220px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.8rem;
            font-family: var(--mono);
        }}
        th, td {{
            padding: 0.6rem 0.75rem;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        th {{ color: var(--text-muted); font-weight: 500; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em; }}
        .status-completed {{ color: var(--green); }}
        .status-completed_with_errors {{ color: var(--yellow); }}
        .status-failed {{ color: var(--red); }}
        .doc-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 0.75rem;
        }}
        .doc-card {{
            background: var(--code-bg);
            padding: 0.75rem;
        }}
        .doc-type {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-family: var(--mono); }}
        .doc-count {{ font-size: 1.1rem; font-weight: 600; font-family: var(--mono); }}
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .leaderboard {{ background: var(--code-bg); }}
        .rank {{ color: var(--text-muted); }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-top">
                <h1 class="title">Velocity Dashboard</h1>
                <span class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
            </div>
        </header>

        <div class="verdict">
            <div class="verdict-label">INDUSTRIALIZATION VELOCITY</div>
            <div class="verdict-value verdict-{verdict_class}">{verdict_emoji} {verdict}</div>
            <div class="verdict-detail">{verdict_detail}</div>
        </div>

        <div class="grid">
            <div class="metric">
                <div class="metric-label">Total Repos</div>
                <div class="metric-value">{stats['total_repos']}</div>
                <div class="metric-sub">{stats['repos_this_year']} new in 2025</div>
            </div>
            <div class="metric">
                <div class="metric-label">Total Documents</div>
                <div class="metric-value">{stats['total_docs']:,}</div>
                <div class="metric-sub">{stats['total_commits']:,} commits indexed</div>
            </div>
            <div class="metric">
                <div class="metric-label">Commits This Month</div>
                <div class="metric-value">{stats['commits_this_month']:,}</div>
                <div class="metric-sub {trend_class}">{trend_icon} {trend_pct} vs last month</div>
            </div>
            <div class="metric">
                <div class="metric-label">LOC This Month</div>
                <div class="metric-value">{stats['loc_this_month']:,}</div>
                <div class="metric-sub">net lines added</div>
            </div>
        </div>

        <section class="two-col">
            <div>
                <h2>Commits per Month</h2>
                <div class="chart-container">
                    <canvas id="commitsChart"></canvas>
                </div>
            </div>
            <div>
                <h2>LOC per Month (net)</h2>
                <div class="chart-container">
                    <canvas id="locChart"></canvas>
                </div>
            </div>
        </section>

        <section class="two-col">
            <div>
                <h2>Top Repos (L3M)</h2>
                <table class="leaderboard">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Repository</th>
                            <th>Commits</th>
                        </tr>
                    </thead>
                    <tbody>{top_repos_rows}
                    </tbody>
                </table>
            </div>
            <div>
                <h2>Top Authors (L3M)</h2>
                <table class="leaderboard">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Author</th>
                            <th>Commits</th>
                        </tr>
                    </thead>
                    <tbody>{top_authors_rows}
                    </tbody>
                </table>
            </div>
        </section>

        <section>
            <h2>Recent Ingestion Runs</h2>
            <table>
                <thead>
                    <tr>
                        <th>Timestamp</th>
                        <th>Status</th>
                        <th>Repos</th>
                        <th>Updated</th>
                        <th>Skipped</th>
                        <th>Errors</th>
                        <th>Duration</th>
                    </tr>
                </thead>
                <tbody>{runs_rows}
                </tbody>
            </table>
        </section>

        <section>
            <h2>Document Breakdown</h2>
            <div class="doc-grid">{doc_type_cards}
            </div>
        </section>
    </div>

    <script>
        const chartOptions = {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ grid: {{ color: 'var(--border)' }}, ticks: {{ color: 'var(--text-muted)' }} }},
                y: {{ grid: {{ color: 'var(--border)' }}, ticks: {{ color: 'var(--text-muted)' }} }}
            }}
        }};

        new Chart(document.getElementById('commitsChart'), {{
            type: 'bar',
            data: {{
                labels: {commits_labels},
                datasets: [{{
                    data: {commits_data},
                    backgroundColor: 'var(--green)',
                    borderRadius: 4
                }}]
            }},
            options: chartOptions
        }});

        new Chart(document.getElementById('locChart'), {{
            type: 'line',
            data: {{
                labels: {commits_labels},
                datasets: [{{
                    data: {loc_data},
                    borderColor: 'var(--accent)',
                    backgroundColor: 'rgba(37, 99, 235, 0.1)',
                    fill: true,
                    tension: 0.3
                }}]
            }},
            options: chartOptions
        }});
    </script>
</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="Generate KPI dashboard HTML")
    parser.add_argument("--output", type=str, default=None, help="Output directory")
    args = parser.parse_args()

    # Determine output path - default to landing folder for nginx serving
    if args.output:
        output_dir = Path(args.output)
    else:
        # Default: landing folder (mounted by nginx)
        output_dir = Path(__file__).parent.parent.parent.parent / "landing"

    output_file = output_dir / "kpi.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Query stats
    print("Querying Couchbase stats...")
    cb_client = CouchbaseClient()
    stats = query_stats(cb_client)

    # Generate HTML
    print("Generating dashboard...")
    html = generate_html(stats)

    # Write file
    output_file.write_text(html)
    print(f"Dashboard written to: {output_file}")


if __name__ == "__main__":
    main()
