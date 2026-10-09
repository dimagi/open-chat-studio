# Performance benchmark runner

Benchmarks that track regressions or support hill-climbing run on a dedicated, pinned EC2 instance registered as a self-hosted GitHub Actions runner. Hosted runners share variable hardware and are too noisy for deltas of a few percent.

## Design

| Setting | Value |
|---|---|
| Region | `us-east-1` |
| Instance type | `c7i.xlarge` (4 vCPU, fixed performance; not a burstable T type) |
| AMI | `ami-0fa5967347d08d2df` (`ubuntu-noble-24.04-amd64-server-20261004`) |
| Runner labels | `self-hosted`, `ocs-perf` |

CPU layout:

* Cores 0-1: OS, Postgres and Redis (containers with `cpuset`).
* Cores 2-3: isolated with `isolcpus`, `nohz_full` and `rcu_nocbs`. Run benchmarks with `taskset -c 2,3`.

Postgres and Redis run on the same machine so network latency to managed services does not add noise.

The instance type, AMI and the Postgres and Redis image digests (pinned in `bootstrap.sh`) are recorded in `/opt/perf-runner/hardware.env` and must be stored with every result. Changing either starts a new baseline.

After the first reboot, confirm the isolation took effect: `cat /proc/cmdline` must contain `isolcpus=2,3`.

Turbo boost and the CPU governor are set at boot only if the guest can control them. `c7i.xlarge` exposes neither (no `intel_pstate` or `cpufreq` in sysfs), so the tune service does nothing there. Measured on an idle instance with a CPU-bound Python workload (15 rounds, three runs each), medians varied by 0.2% between runs on an isolated core and 0.9% on a shared core, within the 3% target.

## Provision

One-time setup in the account: an egress-only security group and an instance profile for Session Manager.

```shell
aws ec2 create-security-group --group-name ocs-perf-runner \
    --description "OCS perf benchmark runner (egress only)" --vpc-id <vpc-id>
aws iam create-role --role-name ocs-perf-runner --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name ocs-perf-runner \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name ocs-perf-runner
aws iam add-role-to-instance-profile --instance-profile-name ocs-perf-runner --role-name ocs-perf-runner
```

To move to a newer AMI (this starts a new baseline), look up the current Ubuntu 24.04 image:

```shell
aws ssm get-parameter --region us-east-1 \
    --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --query Parameter.Value --output text
```

Launch the instance in a subnet with outbound internet access. Connect with `aws ssm start-session --target <instance-id>`. `KEY_NAME` is optional and only needed for SSH, which also requires an inbound rule.

```shell
AMI_ID=ami-0fa5967347d08d2df SUBNET_ID=subnet-... SECURITY_GROUP_ID=sg-... \
    INSTANCE_PROFILE=ocs-perf-runner scripts/perf_runner/provision.sh
```

`scripts/perf_runner/bootstrap.sh` runs as user data on first boot, configures the host and reboots once so the CPU isolation takes effect.

## Register the runner

Create a registration token (repository Settings → Actions → Runners → New self-hosted runner). On the instance, switch to the `runner` user (`sudo su - runner`) and follow the download and `./config.sh` steps GitHub shows, using the label `ocs-perf`. The `runner` user has no sudo rights, so exit back to your own session (which has them, for example `ssm-user`) and install the service from the runner directory with `sudo ./svc.sh install runner && sudo ./svc.sh start`.

Workflows target it with `runs-on: [self-hosted, ocs-perf]`.

## Cost

The instance can be stopped between runs (`instance-initiated-shutdown-behavior` is `stop`, so the EBS volume and runner registration survive). Starting it on demand for the nightly run is a follow-up.
