# authentik benchmarks

Ansible project that provisions three hosts and runs a k6 login benchmark against
authentik, with metrics and profiles collected on a separate host.

## Layout

| Path | Purpose |
| --- | --- |
| `inventory/hosts.yml` | The three hosts: `metrics`, `authentik`, `tests` |
| `group_vars/all.yml` | Cross-host settings: addresses, published ports, fixture size |
| `site.yml` | Generates fixtures, provisions all hosts, deploys each stack |
| `roles/common` | Docker, htop and nano on every host |
| `roles/metrics` | Prometheus, Pyroscope and Grafana |
| `roles/authentik` | authentik server, worker and PostgreSQL |
| `roles/runner` | k6 and the test scripts |
| `gen-blueprint.py` | Generates the test-data blueprint and matching credentials |

Lint the playbook and roles with `uv run ansible-lint site.yml roles inventory`.

## Usage

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run ansible-galaxy collection install -r requirements.yml -p collections
$EDITOR inventory/hosts.yml                  # point the three groups at your hosts
uv run ansible-playbook site.yml
```

That will:

1. Generate `build/test-data.yaml` (the blueprint) and `build/users.json` (the
   matching k6 credentials) on the control node, from the same seed.
2. Install Docker, htop and nano on all three hosts.
3. Deploy Prometheus + Pyroscope + Grafana on the `metrics` host, authentik on the
   `authentik` host, and k6 on the `tests` host.

## Test profiles

`runner_profile` picks how k6 runs. Both profiles drive the same scenarios, one
per authentication flow, and tag their metrics with `profile` so the two are
distinguishable in Grafana.

### quick (default)

Each scenario runs for `runner_quick_duration` seconds (150 by default), one
after the other, then k6 exits. Takes about five and a half minutes:

```bash
uv run ansible-playbook site.yml -e runner_run_tests=true
```

The playbook blocks until the run finishes and prints k6's summary. Results
stream to Prometheus via remote-write and are also written to
`/opt/benchmarks/runner/outputs/output.json` on the test host.

### burn

All scenarios run at once in a background container, with `restart:
unless-stopped`, until you stop it. Use it to keep authentik under continuous
load while watching Grafana or Pyroscope:

```bash
uv run ansible-playbook site.yml -e runner_profile=burn
```

Stop it again with:

```bash
uv run ansible-playbook site.yml -e runner_profile=burn -e runner_burn_state=absent
```

The burn profile only remote-writes to Prometheus - it skips the JSON output,
which would otherwise grow without bound. Follow it with
`docker compose logs -f` in `/opt/benchmarks/runner` on the test host.

## Notes

- Fixtures are only generated once. Delete `build/` to regenerate them, e.g. after
  changing `fixture_users`.
- The authentik database password and secret key are generated on first run and
  cached in `credentials/` on the control node so re-runs do not rotate them.
- Prometheus, Pyroscope and Grafana bind to `0.0.0.0` because the other hosts
  connect to them. Override `metrics_bind_address` or firewall the host.
- authentik sends profiles to Pyroscope via `AUTHENTIK_PYROSCOPE_HOST`. Its Python
  components pick this up on their own; the Go components (server, outposts) only
  profile when `AUTHENTIK_DEBUG=true` is also set, which skews benchmark results.
- The `with-mfa` scenario drives the flow slug in `runner_flow_with_mfa`
  (`default-authentication-mfa-flow` by default). That flow needs an authenticator
  validation stage, and the generated users need static tokens matching the
  `staticToken` code in `roles/runner/files/tests/login.js` - neither is created by
  `gen-blueprint.py` yet.
