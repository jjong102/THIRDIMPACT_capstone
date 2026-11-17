#!/usr/bin/env bash
set -euo pipefail

export HOME="${HOME:-/home/third_impact}"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
  # nvm.sh uses unbound variables internally
  set +u
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  set -u
fi

cd /home/third_impact/THIRDIMPACT_capstone/air-scent-dashboard
exec npm run dev -- --host 0.0.0.0 --port 5173
