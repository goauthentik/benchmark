# authentik benchmarks

Ansible project that provisions a metrics host, one or more authentik hosts and
one or more k6 hosts, then runs login benchmarks against them. Every test is
labelled with its own identity, so several of them can run at once - against the
same authentik deployment or against differently configured ones - and still be
told apart in Grafana.

## Layout

| Path | Purpose |
| --- | --- |
| `inventory/hosts.yml` | The hosts, grouped into `metrics`, `authentik` and `tests` |
| `group_vars/all.yml` | Cross-host settings: addresses, published ports, fixture size, `benchmark_tests` |
| `site.yml` | Generates fixtures, checks the test list, provisions all hosts, deploys each stack |
| `roles/common` | Docker, htop and nano on every host |
| `roles/host_metrics` | node-exporter and cAdvisor on every host |
| `roles/metrics` | Prometheus, Loki, Tempo, Pyroscope and Grafana |
| `roles/authentik` | authentik server, worker, PostgreSQL and its Prometheus exporter |
| `roles/runner` | k6, the test scripts and one container per test |
| `gen-blueprint.py` | Generates the test-data blueprint and matching credentials |

Lint the playbook and roles with `uv run ansible-lint site.yml roles inventory`.

## Usage

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run ansible-galaxy collection install -r requirements.yml -p collections
$EDITOR inventory/hosts.yml                  # point the groups at your hosts
uv run ansible-playbook site.yml
```

That will:

1. Generate `build/test-data.yaml` (the blueprint) and `build/users.json` (the
   matching k6 credentials) on the control node, from the same seed.
2. Resolve `benchmark_tests` and fail before touching anything if a test names a
   host, script or profile that does not exist. The run prints the resolved list.
3. Install Docker, htop and nano on all hosts.
4. Deploy Prometheus + Loki + Tempo + Pyroscope + Grafana on the `metrics` host,
   authentik on every `authentik` host, and k6 on every `tests` host.

## Tests

A test is one entry in `benchmark_tests`. Only `name` is required; everything
else falls back to the matching `runner_*` default, so `-e runner_vus=32` still
moves the whole list at once:

```yaml
benchmark_tests:
  - name: login-two-workers      # unique, [a-z0-9-]
    target: authentik-1          # host in the authentik group it drives
    profile: burn                # quick or burn
  - name: login-eight-workers
    target: authentik-2
    profile: burn
    vus: 32
  - name: login-mfa
    target: authentik-1
    runner: tests-2              # which k6 host generates the load
    script: login.ts             # file in roles/runner/files/tests
    flow: default-authentication-flow-mfa
    iterations: 2000
```

Each test becomes its own compose service (`k6-<name>`) on its runner host, and
k6 tags every metric it emits with `testid=<name>`, `target=<target>` and
`profile=<profile>`. That is what makes a test identifiable: the dashboards' test
selector lists them by name, the *Per test (k6)* row draws one line per test with
the target it drove, and each run also posts a Grafana annotation tagged `k6` and
`test:<name>`.

Two tests may share a target - they then compete for it, which is the point of a
mixed-load run. Names have to be unique, because that is what the dashboards
split on.

### Multiple authentik hosts

Every host in the `authentik` group is a full, independent stack with its own
database, and the vars set on it are what a comparison varies:

```yaml
authentik:
  hosts:
    authentik-1:
      ansible_host: a1.example.com
      authentik_web_workers: 2
    authentik-2:
      ansible_host: a2.example.com
      authentik_web_workers: 8
```

Prometheus labels every authentik and postgres series with `target` (the
inventory name of the deployment) and `host`, so the summary and authentik
dashboards have a target selector, and k6's `target` tag lines a test up with the
deployment it drove. Profiles are separated the same way: the authentik role sets
the server and worker container hostname to the target name, which is the tag
authentik's Pyroscope client sends.

To run two stacks on one machine, give them one inventory host each with the same
`ansible_host` and different ports and `authentik_dir`. node-exporter and
cAdvisor are then scraped once for that machine, under the first of the two
names.

## Traces

Tempo runs in the metrics stack and takes OTLP over HTTP on
`metrics_tempo_otlp_port` (4318). Its own API is not published - Grafana queries
it and Prometheus scrapes it over the stack's compose network.

k6 always propagates trace context: every login flow gets one W3C trace, one
`traceparent` per request, and the headers `X-Benchmark-Test` and
`X-Benchmark-Target`. authentik's sampler is parent-based, so the sampled flag k6
sets is the whole decision - `runner_trace_sample_rate` (1% by default, settable
per test as `trace_sample_rate`) is what controls how many login flows are
traced, not authentik's own `sample_rate`, which only covers spans with no
incoming context such as background tasks.

authentik's side is off by default, because the tracing it needs is
[goauthentik/authentik#25412](https://github.com/goauthentik/authentik/pull/25412)
and the image this repo pins does not have it. On an image built from that branch:

```bash
uv run ansible-playbook site.yml \
  -e authentik_error_reporting=true \
  -e authentik_image=ghcr.io/goauthentik/dev-server \
  -e authentik_tag=gh-wip-root-otel-v1
```

`authentik_error_reporting` is the branch's own switch (`error_reporting.enabled`)
for exporting spans. **On a released image that same flag means "send errors to
authentik's Sentry" instead**, which is why it defaults to off. With it on, the
role sets the OTLP endpoint, the sample rate, `deployment.environment` to the
target's name, and asks the Django instrumentation to record the two benchmark
headers as span attributes.

The dashboard's collapsed *Traces (Tempo)* row then works off the same selectors
as everything else:

| Question | TraceQL |
| --- | --- |
| Flows one test caused | `{ span.http.request.header.x_benchmark_test = "login-baseline" }` |
| Everything on one deployment | `{ resource.deployment.environment = "authentik-1" }` |
| Slow outliers | `{ resource.deployment.environment = "authentik-1" && duration > 500ms }` |

k6 also logs `traceID=<id>` for each flow it samples, and those lines reach Loki,
where the datasource turns them into a link to the trace - look for
`{stack="runner"} |= "sampled login"`.

Keep the sample rate low. The branch exports spans with a `SimpleSpanProcessor`,
so every sampled request pays for an inline OTLP export - at 100% on a burn test
you are benchmarking the exporter. Traces also take about 30 seconds to become
searchable (Tempo's `query_end_cutoff`), while a lookup by trace id is immediate,
and Tempo drops blocks older than `metrics_tempo_retention` (24h) because this is
local disk on a host that is busy doing something else.

## Profiles

`profile` decides how k6 runs a test. Both use the `per-vu-iterations` executor,
so the work per VU is fixed rather than the run length.

### quick (default)

Every VU runs `iterations` logins, so a run is `vus * iterations` logins of fixed
work and ends when they are done. `quick_duration` (150s) is only a cap for a
target that cannot keep up:

```bash
uv run ansible-playbook site.yml -e runner_run_tests=true
```

The playbook runs each quick test in turn, blocks until it finishes and prints
k6's summary. Results stream to Prometheus via remote-write and are also written
to `/opt/benchmarks/runner/outputs/<name>.json` on the runner host. Every run
gets a region annotation in Grafana, so it can be found again afterwards.

### burn

The test runs in a background container with `restart: unless-stopped`, and its
iteration count is high enough that the container's lifetime is the only limit.
Use it to keep authentik under continuous load while watching Grafana or
Pyroscope. Tests with `profile: burn` start on any normal run:

```bash
uv run ansible-playbook site.yml
```

`runner_profile` is the fallback for tests that do not set one, so a list that
leaves it out can be switched over as a whole:

```bash
uv run ansible-playbook site.yml -e runner_profile=burn
```

Stop the burn tests again with:

```bash
uv run ansible-playbook site.yml -e runner_burn_state=absent
```

Burn tests only remote-write to Prometheus - they skip the JSON output, which
would otherwise grow without bound. Follow them with `docker compose logs -f` in
`/opt/benchmarks/runner` on the runner host; that directory's `.env` enables the
`burn` profile, so plain `docker compose ps` shows them.

## Notes

- Upgrading from the single-test layout: the old `k6-burn` service is gone, and
  the role removes its container on the next run so it cannot keep loading a
  target unnoticed. The default test is named `login` and so keeps emitting the
  same `testid` the old burn test did - its series continue, with the `target` tag
  added - but the load does stop and start once while the container is replaced.
  The authentik containers are also recreated once, to pick up the hostname their
  profiles are tagged with. Series recorded before all this have no `target` or
  `host` label at all, so leave those selectors on `All` when looking further back
  than the last deploy.
- Renaming a test starts a new series under the new `testid`, and the container
  for the old name is removed on the next run. Switching a test from `burn` to
  `quick` also stops its background container.
- k6's web dashboard is published per test, starting at `runner_dashboard_port_base`
  (5665) in the order the tests appear in `benchmark_tests`.
- Every playbook run posts a region annotation to Grafana tagged `ansible`,
  covering the run from start to finish, listing the tests it deployed. The
  provisioned dashboards show it as a purple band, so a change in the numbers can
  be lined up against a deploy. It goes through Grafana's API as the anonymous
  admin user the stack already enables; the run fails loudly if that is ever
  locked down.
- Fixtures are only generated once, and every authentik host gets the same
  blueprint, so the same users exist everywhere and a test can be pointed at
  another target without regenerating anything. Delete `build/` to regenerate,
  e.g. after changing `fixture_users`.
- The authentik database password and secret key are generated on first run and
  cached in `credentials/` on the control node so re-runs do not rotate them. All
  authentik hosts share them.
- Prometheus, Loki, Tempo, Pyroscope and Grafana bind to `0.0.0.0` because the
  other hosts connect to them. Override `metrics_bind_address` or firewall the
  host.
- PostgreSQL logs statements slower than `authentik_pg_log_min_duration` (500ms),
  plus lock waits and temp-file spills. Those lines go to stdout, so they end up in
  Loki: `{container="authentik-postgresql-1"} |= "duration:"`. Lowering the
  threshold towards 0 logs everything, which on a saturated benchmark host changes
  what you are measuring.
- Container logs from the authentik and k6 hosts go to Loki through Docker's
  `loki` logging plugin, which `roles/loki_driver` installs on those hosts. Logs
  carry `stack`, `container` and `host` labels, so a host's runner is queryable as
  `{stack="runner", host="tests-1"}` or `{stack="authentik", container="/authentik-worker-1"}`.
  Shipping is non-blocking, so a Loki outage drops log lines rather than stalling
  the containers under test, and Docker's dual-logging cache keeps
  `docker compose logs` working locally.
- Prometheus scrape jobs: `prometheus`, `loki`, `tempo` and `pyroscope` locally;
  `authentik` on `authentik_port_metrics` and `postgres` on
  `authentik_port_pg_exporter` off every authentik host, both labelled with
  `target` and `host`; and `node` on `node_exporter_port` plus `cadvisor` on
  `cadvisor_port` off every machine, labelled with `host`. The node-exporter
  container uses the host network and PID namespaces so its metrics describe the
  host, not the container. cAdvisor attributes usage to individual containers,
  e.g. `sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[5m]))`.
- cAdvisor polls every `host_metrics_cadvisor_housekeeping_interval` (10s, not its
  1s default)
  with most collectors disabled, because it is measuring hosts that are already
  the bottleneck.
- authentik sends profiles to Pyroscope via `AUTHENTIK_PYROSCOPE_HOST`. Its Python
  components pick this up on their own; the Go components (server, outposts) only
  profile when `AUTHENTIK_DEBUG=true` is also set, which skews benchmark results.
- Grafana's Tempo datasource links a span to `{stack="authentik"}` in Loki for the
  span's own time range. Service graphs and span metrics are off: they need
  Tempo's metrics-generator, which would compete for CPU with what is being
  measured.
- The imported `PostgreSQL Monitoring Dashboard` and `Node Exporter Full`
  dashboards predate the `target` label and select by `instance` and `nodename`
  instead; the `k6 Prometheus` dashboard filters by `testid` on its own.
- Tests answer an authenticator validation stage with the static code
  `staticToken`, so a flow with MFA needs matching static tokens on the generated
  users - `gen-blueprint.py` does not create those yet.
- `roles/runner/files/tests/login.ts` is TypeScript, which k6 runs natively. Type
  check it with `bunx tsc --noEmit`. A new script goes in the same directory and
  is picked by a test's `script`; reading `TEST_ID`, `TARGET` and `PROFILE` from
  the environment keeps it labelled like the others.
