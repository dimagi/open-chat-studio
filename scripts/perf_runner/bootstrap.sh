#!/usr/bin/env bash
# First-boot setup for the performance benchmark runner (Ubuntu 24.04, 4 vCPUs).
#
# CPU layout: cores 0-1 run the OS, Postgres and Redis; cores 2-3 are isolated
# from the scheduler and used only by the benchmark process (taskset -c 2,3).
set -euxo pipefail

ISOLATED_CPUS="2,3"
SERVICE_CPUS="0,1"
RUNNER_USER="runner"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git docker.io docker-compose-v2
apt-get install -y linux-tools-common "linux-tools-$(uname -r)" || true
systemctl enable --now docker

useradd --create-home --shell /bin/bash "$RUNNER_USER" || true
usermod -aG docker "$RUNNER_USER"

# Reserve the benchmark cores from the scheduler and timer ticks.
sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=\"\(.*\)\"/GRUB_CMDLINE_LINUX_DEFAULT=\"\1 isolcpus=${ISOLATED_CPUS} nohz_full=${ISOLATED_CPUS} rcu_nocbs=${ISOLATED_CPUS}\"/" /etc/default/grub
update-grub

# Fixed CPU frequency behaviour at every boot. The turbo and governor files are
# only present on some instance types, so each step is conditional.
cat > /usr/local/bin/perf-runner-tune.sh <<'TUNE'
#!/usr/bin/env bash
if [ -w /sys/devices/system/cpu/intel_pstate/no_turbo ]; then
    echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo
fi
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [ -w "$gov" ] && echo performance > "$gov"
done
exit 0
TUNE
chmod +x /usr/local/bin/perf-runner-tune.sh

cat > /etc/systemd/system/perf-runner-tune.service <<'UNIT'
[Unit]
Description=Fix CPU frequency settings for benchmarks
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/perf-runner-tune.sh

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable perf-runner-tune.service

# Postgres and Redis run locally on the service cores.
mkdir -p /opt/perf-runner
cat > /opt/perf-runner/docker-compose.yml <<COMPOSE
services:
  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    cpuset: "${SERVICE_CPUS}"
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres_password
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
  redis:
    image: redis:7
    restart: unless-stopped
    cpuset: "${SERVICE_CPUS}"
    ports:
      - "127.0.0.1:6379:6379"
volumes:
  pgdata:
COMPOSE
docker compose -f /opt/perf-runner/docker-compose.yml up -d

# Record the hardware identity so results can be tagged with it.
TOKEN="$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
{
    echo "instance_type=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-type)"
    echo "ami_id=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/ami-id)"
    for image in pgvector/pgvector:pg16 redis:7; do
        echo "image_${image%%[/:]*}=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")"
    done
} > /opt/perf-runner/hardware.env

# The runner itself is installed and registered by hand (needs a short-lived token).
reboot
