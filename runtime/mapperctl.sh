#!/bin/sh
# Resolve Python 3.7+ on webOS (stock /usr/bin/python3, Entware /opt/bin/python3, PATH, or
# webos-entware-installer's chroot helper on webOS 4-6) and invoke runtime/mapperctl.py.
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONTROLLER="$SCRIPT_DIR/mapperctl.py"
ENTWARE_ROOT="${MAGIC_MAPPER_ENTWARE_ROOT:-/var/lib/webos-entware/webos-entware-root}"

is_python37() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' >/dev/null 2>&1
}

CANDIDATES="${MAGIC_MAPPER_PYTHON_PATHS:-/usr/bin/python3:/opt/bin/python3}"
if [ "${IFS+set}" = "set" ]; then
  OLD_IFS=$IFS
  HAD_IFS=1
else
  HAD_IFS=0
fi
IFS=:
for candidate in $CANDIDATES; do
  [ -n "$candidate" ] || continue
  if [ -f "$candidate" ] && [ -x "$candidate" ] && is_python37 "$candidate"; then
    if [ "$HAD_IFS" = "1" ]; then IFS=$OLD_IFS; else unset IFS; fi
    exec "$candidate" "$CONTROLLER" "$@"
  fi
done
if [ "$HAD_IFS" = "1" ]; then IFS=$OLD_IFS; else unset IFS; fi

if [ "${MAGIC_MAPPER_SKIP_PATH:-0}" != "1" ]; then
  PATH_PYTHON=$(command -v python3 2>/dev/null || true)
  if [ -n "$PATH_PYTHON" ] && [ -f "$PATH_PYTHON" ] && [ -x "$PATH_PYTHON" ] && is_python37 "$PATH_PYTHON"; then
    exec "$PATH_PYTHON" "$CONTROLLER" "$@"
  fi
fi

if [ -f "$ENTWARE_ROOT" ] && [ -x "$ENTWARE_ROOT" ]; then
  if "$ENTWARE_ROOT" run /opt/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' >/dev/null 2>&1; then
    exec "$ENTWARE_ROOT" run /opt/bin/python3 "$CONTROLLER" "$@"
  fi
fi

printf '%s\n' '{"code":"missing_python3","error":"Python 3.7 or newer was not found on this TV (checked /usr/bin/python3 and /opt/bin/python3). Install Python 3 through Entware and try again.","ok":false}'
exit 1
