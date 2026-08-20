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

## Usage

```bash
pip install ansible-core                     # or: uv tool install ansible-core
ansible-galaxy collection install -r requirements.yml -p collections
$EDITOR inventory/hosts.yml                  # point the three groups at your hosts
ansible-playbook site.yml
```

That will:

1. Generate `build/test-data.yaml` (the blueprint) and `build/users.json` (the
   matching k6 credentials) on the control node, from the same seed.
2. Install Docker, htop and nano on all three hosts.
3. Deploy Prometheus + Pyroscope + Grafana on the `metrics` host, authentik on the
   `authentik` host, and k6 on the `tests` host.

Run the benchmark itself, which takes about five and a half minutes:

```bash
ansible-playbook site.yml -e runner_run_tests=true
```

Results stream to Prometheus via remote-write and are also written to
`/opt/benchmarks/runner/outputs/output.json` on the test host.

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
