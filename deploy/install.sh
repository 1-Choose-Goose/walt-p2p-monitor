#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

source_dir=${1:-}
if [[ -z ${source_dir} || ! -f ${source_dir}/pyproject.toml ]]; then
  echo "Usage: $0 /path/to/release-source" >&2
  exit 2
fi

app_dir=/opt/walt-price-bot
data_dir=/var/lib/walt-price-bot
env_file=/etc/walt-price-bot.env

if ! id -u waltbot >/dev/null 2>&1; then
  useradd --system --home-dir "${data_dir}" --shell /usr/sbin/nologin waltbot
fi

install -d -m 0755 -o root -g root "${app_dir}"
install -d -m 0700 -o waltbot -g waltbot "${data_dir}"
rm -rf "${app_dir}/build" "${app_dir}/src"
cp -a "${source_dir}/pyproject.toml" "${source_dir}/uv.lock" \
  "${source_dir}/requirements.lock" "${app_dir}/"
cp -a "${source_dir}/src" "${app_dir}/src"
find "${app_dir}/src" -maxdepth 1 -type d -name '*.egg-info' -exec rm -rf -- {} +
python3 -m venv "${app_dir}/.venv"
"${app_dir}/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
  --require-hashes -r "${app_dir}/requirements.lock"
"${app_dir}/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
  --force-reinstall --no-deps "${app_dir}"
chown -R root:root "${app_dir}"
chmod -R u=rwX,go=rX "${app_dir}"

if [[ ! -f ${env_file} ]]; then
  echo "Missing ${env_file}; create it before installation" >&2
  exit 3
fi
chown waltbot:waltbot "${env_file}"
chmod 0600 "${env_file}"

install -m 0644 "${source_dir}/deploy/walt-price-bot.service" /etc/systemd/system/walt-price-bot.service
systemctl daemon-reload
systemctl enable --now walt-price-bot.service
