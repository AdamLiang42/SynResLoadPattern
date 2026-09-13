#!/usr/bin/env python3
"""Archive GitHub traffic and release counters without cloning the repository.

Requires Python 3.10+ and an authenticated GitHub CLI. In Actions, GH_TOKEN
writes the archive and TRAFFIC_TOKEN has Administration: read on this repo only.
Without --publish, remote state is read and the proposed archive is written locally.
"""

import argparse
import base64
import copy
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import quote


class APIError(RuntimeError):
    pass


def api(path, *, payload=None, traffic=False, missing_ok=False, paginate=False):
    command = ["gh", "api", path, "-H", "X-GitHub-Api-Version: 2026-03-10"]
    if paginate:
        command += ["--paginate", "--slurp"]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    env = os.environ.copy()
    if traffic:
        if env.get("GITHUB_ACTIONS") == "true" and not env.get("TRAFFIC_TOKEN"):
            raise APIError("Add the TRAFFIC_TOKEN Actions secret with Administration: read on this repository.")
        if env.get("TRAFFIC_TOKEN"):
            env["GH_TOKEN"] = env["TRAFFIC_TOKEN"]
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, env=env)
    if result.returncode:
        if missing_ok and "(HTTP 404)" in result.stderr:
            return None
        # Do not echo response bodies, credentials, or request headers.
        status = re.search(r"HTTP (\d{3})", result.stderr)
        raise APIError(f"GitHub API failed for {path}: " + (status.group(0) if status else "request failed"))
    return json.loads(result.stdout)


def all_items(path):
    return [item for page in api(path, paginate=True) for item in page]


def get_previous(repo, branch):
    ref = api(f"repos/{repo}/git/ref/heads/{quote(branch, safe='')}", missing_ok=True)
    if ref is None:
        return None, None, None
    parent = ref["object"]["sha"]
    commit = api(f"repos/{repo}/git/commits/{parent}")
    # Missing/corrupt state on an existing branch is an error, never an empty archive.
    contents = api(f"repos/{repo}/contents/state.json?ref={parent}")
    if contents.get("encoding") == "base64":
        state = json.loads(base64.b64decode(contents["content"]))
    else:
        blob = api(f"repos/{repo}/git/blobs/{contents['sha']}")
        state = json.loads(base64.b64decode(blob["content"]))
    if state.get("schema_version") != 1 or state.get("repository") != repo:
        raise ValueError("Archive schema or repository mismatch; refusing to replace it.")
    return parent, commit["tree"]["sha"], state


def collect(repo, stamp):
    traffic = {metric: api(f"repos/{repo}/traffic/{metric}", traffic=True)
               for metric in ("clones", "views", "popular/paths", "popular/referrers")}
    assets = []
    for release in all_items(f"repos/{repo}/releases?per_page=100"):
        if release["draft"]:
            continue
        for asset in all_items(f"repos/{repo}/releases/{release['id']}/assets?per_page=100"):
            assets.append({"id": asset["id"], "tag": release["tag_name"],
                           "name": asset["name"], "size": asset["size"],
                           "download_count": asset["download_count"],
                           "download_url": asset["browser_download_url"]})
    return {"retrieved_at": stamp, "repository": repo, "traffic": traffic, "assets": assets}


def merge(previous, snapshot):
    repo, stamp = snapshot["repository"], snapshot["retrieved_at"]
    state = copy.deepcopy(previous) if previous is not None else {
        "schema_version": 1, "repository": repo, "started_at": stamp,
        "days": {}, "assets": {}, "release_downloads": {},
    }
    if state["schema_version"] != 1 or state["repository"] != repo:
        raise ValueError("Archive schema or repository mismatch.")
    if previous and stamp < previous["updated_at"]:
        raise ValueError("Refusing to overwrite newer observations with an older snapshot.")
    for metric, fields in (("clones", ("clones", "unique_cloners")),
                           ("views", ("views", "unique_visitors"))):
        for item in snapshot["traffic"][metric][metric]:
            day = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).date().isoformat()
            count, uniques = item["count"], item["uniques"]
            if not all(type(x) is int and x >= 0 for x in (count, uniques)) or uniques > count:
                raise ValueError("Invalid traffic count.")
            row = state["days"].setdefault(day, {})
            row[fields[0]], row[fields[1]] = count, uniques
    for asset in state["assets"].values():
        asset["available"] = False
    for asset in snapshot["assets"]:
        key = str(asset["id"])
        old = state["assets"].get(key, {})
        state["assets"][key] = {
            **asset, "first_seen_at": old.get("first_seen_at", stamp), "last_seen_at": stamp,
            "available": True, "is_dataset": asset["name"] == "SynResLoadPattern.zip",
        }
    # Keep last observed counts for deleted assets; never add repeated snapshots together.
    state["release_downloads"][stamp[:10]] = sum(
        a["download_count"] for a in state["assets"].values() if a["is_dataset"])
    state["updated_at"] = stamp
    return state


def json_text(value):
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def csv_text(fields, rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def render(state, snapshot):
    stamp = state["updated_at"]
    days = sorted(state["days"])
    totals = {metric: sum(row.get(metric, 0) for row in state["days"].values())
              for metric in ("views", "clones")}
    downloads = state["release_downloads"][stamp[:10]]
    missing = []
    if days:
        from datetime import date, timedelta
        day, end = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
        while day <= end:
            if not all(key in state["days"].get(day.isoformat(), {}) for key in ("clones", "views")):
                missing.append(day.isoformat())
            day += timedelta(days=1)
    readme = f"""# SynResLoadPattern usage archive

Last collected: **{stamp}**. Archiving started: **{state['started_at']}**.

| Metric | Count |
| --- | ---: |
| Dataset release ZIP downloads (observed cumulative asset counters) | {downloads} |
| Repository clones across archived dates | {totals['clones']} |
| Repository views across archived dates | {totals['views']} |

Traffic date range: {days[0] if days else 'none'} through {days[-1] if days else 'none'} (UTC).
Dates missing one or both traffic metrics inside that range: {', '.join(missing) or 'none'}.

[Daily traffic CSV](traffic.csv) · [Release download history](release_downloads.csv) ·
[Canonical state and per-asset counters](state.json) · [Raw daily snapshots](snapshots)

- The collector refreshes the available 14-day window and replaces overlapping dates.
  It preserves older rows. Missing observations remain unknown; today's counts are partial.
- Unique visitors/cloners are unique **within each day**; do not sum them as all-time unique people.
- ZIP downloads, clones, and views are separate metrics and may include bots/repeated access.
  GitHub does not expose a comprehensive count of raw-file or repository source-ZIP downloads.
- Release totals include only uploaded assets named `SynResLoadPattern.zip`, once per asset ID.
  Deleted assets retain their last observed count; downloads after their last observation may be lost.
- Traffic before the first available date cannot be reconstructed. More than 14 days without
  successful collection can leave permanent gaps. Raw snapshots preserve GitHub's rolling-window
  uniques, referrers, popular paths, and later revisions without treating them as additive totals.
- Data on this branch is public. It contains aggregate GitHub metrics, no visitor identities.
  API access avoids creating a daily clone simply to collect statistics.

See [operation and credential setup](https://github.com/{state['repository']}/blob/main/docs/usage_tracking.md).
"""
    return {
        "state.json": json_text(state),
        "traffic.csv": csv_text(["date", "views", "unique_visitors", "clones", "unique_cloners"],
                                ({"date": day, **state["days"][day]} for day in days)),
        "release_downloads.csv": csv_text(["date", "observed_cumulative_dataset_downloads"],
            ({"date": day, "observed_cumulative_dataset_downloads": count}
             for day, count in sorted(state["release_downloads"].items()))),
        "README.md": readme,
        f"snapshots/{stamp[:10]}.json": json_text(snapshot),
    }


def publish(repo, branch, parent, base_tree, files, stamp):
    payload = {"tree": [{"path": path, "mode": "100644", "type": "blob", "content": content}
                        for path, content in files.items()]}
    if base_tree:
        payload["base_tree"] = base_tree
    tree = api(f"repos/{repo}/git/trees", payload=payload)
    commit = api(f"repos/{repo}/git/commits", payload={
        "message": f"Archive usage statistics for {stamp}", "tree": tree["sha"],
        "parents": [parent] if parent else [],
    })
    if parent:
        # PATCH with force=false rejects a concurrent writer instead of losing its history.
        command = ["gh", "api", f"repos/{repo}/git/refs/heads/{quote(branch, safe='')}",
                   "--method", "PATCH", "--input", "-"]
        result = subprocess.run(command, input=json.dumps({"sha": commit["sha"], "force": False}),
                                capture_output=True, text=True)
        if result.returncode:
            raise APIError("Archive branch update failed; rerun to merge the latest state. No force push was attempted.")
    else:
        api(f"repos/{repo}/git/refs", payload={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    return commit["sha"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "AdamLiang42/SynResLoadPattern"))
    parser.add_argument("--branch", default="traffic-data")
    parser.add_argument("--output-dir", type=Path, default=Path("traffic-output"))
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.branch in ("main", "master"):
        parser.error("Use a dedicated archive branch, never main/master.")
    parent, base_tree, previous = get_previous(args.repo, args.branch)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = collect(args.repo, stamp)
    state = merge(previous, snapshot)
    files = render(state, snapshot)
    for path, content in files.items():
        target = args.output_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if args.publish:
        sha = publish(args.repo, args.branch, parent, base_tree, files, stamp)
        print(f"Published archive commit {sha}")
    else:
        print(f"Preview saved in {args.output_dir}; no remote writes.")
    summary = f"Archived {len(state['days'])} UTC dates; dataset ZIP downloads: {state['release_downloads'][stamp[:10]]}."
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary + f"\n\n[Open usage archive](https://github.com/{args.repo}/tree/{args.branch})\n")


if __name__ == "__main__":
    try:
        main()
    except (APIError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
