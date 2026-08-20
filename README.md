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
| `roles/metrics` | Prometheus, Loki, Pyroscope and Grafana |
| `roles/authentik` | authentik server, worker, PostgreSQL and its Prometheus exporter |
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

`runner_profile` picks how k6 runs. Both profiles drive the same single `login`
scenario and tag their metrics with `profile`, so runs are distinguishable in
Grafana.

### quick (default)

The scenario runs for `runner_quick_duration` seconds (150 by default), then k6
exits:

```bash
uv run ansible-playbook site.yml -e runner_run_tests=true
```

The playbook blocks until the run finishes and prints k6's summary. Results
stream to Prometheus via remote-write and are also written to
`/opt/benchmarks/runner/outputs/output.json` on the test host.

### burn

The scenario runs in a background container, with `restart: unless-stopped`,
until you stop it. Use it to keep authentik under continuous
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
- Prometheus, Loki, Pyroscope and Grafana bind to `0.0.0.0` because the other
  hosts connect to them. Override `metrics_bind_address` or firewall the host.
- Container logs from the authentik and k6 hosts go to Loki through Docker's
  `loki` logging plugin, which `roles/loki_driver` installs on those hosts. Logs
  carry `stack`, `container` and `host` labels, so a run is queryable as
  `{stack="runner"}` or `{stack="authentik", container="/authentik-worker-1"}`.
  Shipping is non-blocking, so a Loki outage drops log lines rather than stalling
  the containers under test, and Docker's dual-logging cache keeps
  `docker compose logs` working locally.
- Prometheus scrapes three jobs off the authentik host: authentik itself on
  `authentik_port_metrics`, and `postgres-exporter` on `authentik_port_pg_exporter`,
  which runs alongside the database in the authentik stack.
- authentik sends profiles to Pyroscope via `AUTHENTIK_PYROSCOPE_HOST`. Its Python
  components pick this up on their own; the Go components (server, outposts) only
  profile when `AUTHENTIK_DEBUG=true` is also set, which skews benchmark results.
- The test drives `default-authentication-flow`, overridable with the `FLOW`
  environment variable. It still answers an authenticator validation stage with the
  static code `staticToken`, so a flow with MFA needs matching static tokens on the
  generated users - `gen-blueprint.py` does not create those yet.
- `roles/runner/files/tests/login.ts` is TypeScript, which k6 runs natively. Type
  check it with `bunx tsc --noEmit`.
