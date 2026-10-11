#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

source_dir=${1:-}
required_files=(
  pyproject.toml
  uv.lock
  requirements.lock
  src
  deploy/walt-price-bot.service
)
if [[ -z ${source_dir} ]]; then
  echo "Usage: $0 /path/to/release-source" >&2
  exit 2
fi
for required_file in "${required_files[@]}"; do
  if [[ ! -e ${source_dir}/${required_file} ]]; then
    echo "Missing release file: ${required_file}" >&2
    exit 2
  fi
done

app_dir=/opt/walt-price-bot
data_dir=/var/lib/walt-price-bot
env_file=/etc/walt-price-bot.env
database_file=${data_dir}/walt-price-bot.db
unit_file=/etc/systemd/system/walt-price-bot.service
timestamp=$(date -u +%Y%m%dT%H%M%SZ)

if [[ ! -f ${env_file} ]]; then
  echo "Missing ${env_file}; create it before installation" >&2
  exit 3
fi

if ! id -u waltbot >/dev/null 2>&1; then
  useradd --system --home-dir "${data_dir}" --shell /usr/sbin/nologin waltbot
fi
install -d -m 0700 -o waltbot -g waltbot "${data_dir}"

preflight_dir=$(mktemp -d /tmp/walt-price-bot-preflight.XXXXXX)
unit_backup=$(mktemp /tmp/walt-price-bot-unit.XXXXXX)
previous_release=
failed_release=
backup_file=
was_active=false
was_enabled=false
service_stopped=false
swapped=false
unit_had_file=false

if systemctl is-active --quiet walt-price-bot.service; then
  was_active=true
fi
if systemctl is-enabled --quiet walt-price-bot.service; then
  was_enabled=true
fi
if [[ -f ${unit_file} ]]; then
  cp -a -- "${unit_file}" "${unit_backup}"
  unit_had_file=true
fi

rollback() {
  status=$?
  trap - EXIT
  if [[ ${status} -eq 0 ]]; then
    rm -rf -- "${preflight_dir}" "${unit_backup}"
    return
  fi

  echo "Installation failed; restoring the previous release" >&2
  if [[ ${service_stopped} == true ]]; then
    systemctl stop walt-price-bot.service >/dev/null 2>&1 || true
  fi
  if [[ ${swapped} == true ]]; then
    if [[ -e ${app_dir} || -L ${app_dir} ]]; then
      failed_release=${app_dir}.failed.${timestamp}
      mv -- "${app_dir}" "${failed_release}" || true
    fi
    if [[ -n ${previous_release} && -e ${previous_release} ]]; then
      mv -- "${previous_release}" "${app_dir}"
    fi
  fi
  if [[ ${unit_had_file} == true ]]; then
    install -m 0644 "${unit_backup}" "${unit_file}"
  else
    rm -f -- "${unit_file}"
  fi
  if [[ ${service_stopped} == true && -n ${backup_file} && -f ${backup_file} ]]; then
    python3 - "${backup_file}" "${database_file}" <<'PY'
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
temporary = destination.with_name(destination.name + ".rollback.tmp")
temporary.unlink(missing_ok=True)
try:
    with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(temporary)) as target_db:
        source_db.backup(target_db)
        if target_db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("restored SQLite database is invalid")
    os.replace(temporary, destination)
finally:
    temporary.unlink(missing_ok=True)
PY
    chown waltbot:waltbot "${database_file}"
    chmod 0600 "${database_file}"
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ ${was_enabled} == true ]]; then
    systemctl enable walt-price-bot.service >/dev/null 2>&1 || true
  else
    systemctl disable walt-price-bot.service >/dev/null 2>&1 || true
  fi
  if [[ ${was_active} == true ]]; then
    systemctl start walt-price-bot.service >/dev/null 2>&1 || true
  fi
  rm -rf -- "${preflight_dir}" "${unit_backup}"
  exit "${status}"
}
trap rollback EXIT

cp -a "${source_dir}/pyproject.toml" "${source_dir}/uv.lock" \
  "${source_dir}/requirements.lock" "${preflight_dir}/"
cp -a "${source_dir}/src" "${preflight_dir}/src"
python3 -m venv "${preflight_dir}/.venv"
"${preflight_dir}/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-cache-dir --require-hashes \
  -r "${preflight_dir}/requirements.lock"
"${preflight_dir}/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-cache-dir --force-reinstall --no-deps \
  "${preflight_dir}"
"${preflight_dir}/.venv/bin/python" -c \
  'import walt_price_bot; from walt_price_bot.config import Settings'

if ! grep -q '^CREDENTIAL_ENCRYPTION_KEY=' "${env_file}"; then
  encryption_key=$("${preflight_dir}/.venv/bin/python" -c \
    'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode("ascii"))')
  printf "\nCREDENTIAL_ENCRYPTION_KEY=%s\n" "${encryption_key}" >> "${env_file}"
  unset encryption_key
fi
chown waltbot:waltbot "${env_file}"
chmod 0600 "${env_file}"

"${preflight_dir}/.venv/bin/python" - "${env_file}" <<'PY'
import sys
from pathlib import Path

from walt_price_bot.config import Settings

Settings(_env_file=Path(sys.argv[1]))
PY

if [[ -f ${database_file} ]]; then
  backup_file=${database_file}.pre-upgrade.${timestamp}
  "${preflight_dir}/.venv/bin/python" -c \
    'import sys; from pathlib import Path; from walt_price_bot.schema import backup_sqlite_database; backup_sqlite_database(Path(sys.argv[1]), Path(sys.argv[2]))' \
    "${database_file}" "${backup_file}"
  chown waltbot:waltbot "${backup_file}"
  chmod 0600 "${backup_file}"
fi

if [[ ${was_active} == true ]]; then
  systemctl stop walt-price-bot.service
fi
service_stopped=true

if [[ -e ${app_dir} || -L ${app_dir} ]]; then
  previous_release=${app_dir}.previous.${timestamp}
  mv -- "${app_dir}" "${previous_release}"
fi
swapped=true
install -d -m 0755 -o root -g root "${app_dir}"
cp -a "${source_dir}/pyproject.toml" "${source_dir}/uv.lock" \
  "${source_dir}/requirements.lock" "${app_dir}/"
cp -a "${source_dir}/src" "${app_dir}/src"
find "${app_dir}/src" -maxdepth 1 -type d -name '*.egg-info' -exec rm -rf -- {} +
python3 -m venv "${app_dir}/.venv"
"${app_dir}/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-cache-dir --require-hashes \
  -r "${app_dir}/requirements.lock"
"${app_dir}/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-cache-dir --force-reinstall --no-deps \
  "${app_dir}"
chown -R root:root "${app_dir}"
chmod -R u=rwX,go=rX "${app_dir}"

install -m 0644 "${source_dir}/deploy/walt-price-bot.service" "${unit_file}"
systemctl daemon-reload
systemctl enable --now walt-price-bot.service
sleep 2
systemctl is-active --quiet walt-price-bot.service

service_stopped=false
trap - EXIT
rm -rf -- "${preflight_dir}" "${unit_backup}"
echo "Walt P2P Monitor updated successfully"
if [[ -n ${previous_release} ]]; then
  echo "Previous release: ${previous_release}"
fi
