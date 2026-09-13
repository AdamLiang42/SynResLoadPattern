# Usage tracking

The [daily workflow](../.github/workflows/archive-traffic.yml) runs at **04:23 UTC**.
It archives repository views, clones, daily uniques, top referring sites, popular
paths, and release-asset download counters on the dedicated
[`traffic-data` branch](https://github.com/AdamLiang42/SynResLoadPattern/tree/traffic-data).
Use the Actions tab's **Run workflow** button to collect immediately.

The archive is public and contains aggregate statistics, not visitor identities.
The workflow uses the GitHub API without cloning this dataset repository.

## Reading the numbers

- `traffic.csv` has one row per observed UTC date. Overlapping API windows update
  the existing row; they are never appended as duplicate counts. Missing data is
  left blank rather than reported as zero. The current UTC day is incomplete.
- `release_downloads.csv` records daily observations of the cumulative counters
  for assets named `SynResLoadPattern.zip`. A download is not a unique person.
  Counters are keyed by asset ID so successive observations are not added together.
- `state.json` is the canonical merged archive, including individual release assets.
  Deleted assets retain their last observed count; unobserved downloads cannot be recovered.
- `snapshots/YYYY-MM-DD.json` contains that day's latest raw traffic response and
  normalized release-asset counters. Earlier same-day captures remain in Git history.
- Daily unique visitors/cloners cannot be summed into all-time unique people.
  Views, clones, and release downloads should be reported separately.
- GitHub does not expose comprehensive raw-file or repository source-ZIP download
  counts. The release counter covers the explicitly uploaded dataset ZIP.

## Credential

GitHub's built-in `GITHUB_TOKEN` writes the archive branch but cannot read traffic.
Create a fine-grained personal access token with:

1. Resource owner: `AdamLiang42`.
2. Repository access: **Only select repositories → SynResLoadPattern**.
3. Repository permission: **Administration: Read-only** (Metadata read is automatic).
4. A deliberate expiration date; replace the secret before expiration.

Store it as the repository Actions secret **`TRAFFIC_TOKEN`**. It needs no Contents
write, account permissions, or access to other repositories. Never put the token
in source files, archive data, logs, or a workflow input.

## Reliability and recovery

GitHub traffic endpoints return only the latest 14 days. Each run refreshes that
whole window, allowing missed daily runs to catch up. An outage longer than the
available window can leave permanent gaps. Errors stop publication instead of
replacing valid history with empty data. Archive updates reject concurrent writes
instead of force-pushing. Protect the `traffic-data` branch from deletion.

Check failed Actions runs, especially for an expired/revoked `TRAFFIC_TOKEN`.
GitHub may delay or drop scheduled runs under load and disables schedules on public
repositories after 60 days with no repository activity. The archive normally
creates daily commits; if the schedule is disabled, re-enable it in Actions and
run it manually. Collection requires no local computer to remain online.

For a local recovery run with an authorized GitHub CLI account:

```sh
python3 scripts/archive_traffic.py --output-dir /tmp/synres-traffic-preview
python3 scripts/archive_traffic.py --output-dir /tmp/synres-traffic-output --publish
```

The first command previews without remote writes; the second updates only the
archive branch. Run tests with `python3 -m unittest discover -s tests -v`.

References: [Traffic API](https://docs.github.com/en/rest/metrics/traffic),
[release assets](https://docs.github.com/en/rest/releases/assets),
[scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
