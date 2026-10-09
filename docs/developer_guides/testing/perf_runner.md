# Performance benchmark runner

Benchmarks that track regressions or support hill-climbing run on a dedicated, pinned EC2 instance registered as a self-hosted GitHub Actions runner. Hosted runners share variable hardware and are too noisy for deltas of a few percent.

## Design

| Setting | Value |
|---|---|
| Region | `us-east-1` |
| Instance type | `c7i.xlarge` (4 vCPU, fixed performance; not a burstable T type) |
| AMI | A specific Ubuntu 24.04 AMI ID, passed explicitly and never "latest" |
| Runner labels | `self-hosted`, `ocs-perf` |

CPU layout:

* Cores 0-1: OS, Postgres and Redis (containers with `cpuset`).
* Cores 2-3: isolated with `isolcpus`, `nohz_full` and `rcu_nocbs`. Run benchmarks with `taskset -c 2,3`.

Postgres and Redis run on the same machine so network latency to managed services does not add noise.

The instance type and AMI are recorded in `/opt/perf-runner/hardware.env` and must be stored with every result. Changing either starts a new baseline.

Turbo boost is disabled at boot only if the guest can control it (`/sys/devices/system/cpu/intel_pstate/no_turbo` is writable). Virtualized `c7i` sizes may not expose it. Check after the first boot; if it is missing, measure run-to-run variance anyway (target under 3% on medians) and consider a larger size or a `.metal` instance if variance is too high.

## Provision

Find a current Ubuntu 24.04 AMI once and record the ID:

```shell
aws ssm get-parameter --region us-east-1 \
    --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --query Parameter.Value --output text
```

Launch the instance. The security group needs outbound access only; no inbound rules are required.

```shell
AMI_ID=ami-... SUBNET_ID=subnet-... SECURITY_GROUP_ID=sg-... KEY_NAME=... \
    scripts/perf_runner/provision.sh
```

`scripts/perf_runner/bootstrap.sh` runs as user data on first boot, configures the host and reboots once so the CPU isolation takes effect.

## Register the runner

Create a registration token (repository Settings → Actions → Runners → New self-hosted runner), then on the instance as the `runner` user follow the download and `./config.sh` steps GitHub shows, using the label `ocs-perf`. Install it as a service with `sudo ./svc.sh install runner && sudo ./svc.sh start`.

Workflows target it with `runs-on: [self-hosted, ocs-perf]`.

## Cost

The instance can be stopped between runs (`instance-initiated-shutdown-behavior` is `stop`, so the EBS volume and runner registration survive). Starting it on demand for the nightly run is a follow-up.
