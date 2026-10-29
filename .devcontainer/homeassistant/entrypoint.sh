#!/bin/sh
set -eu
if [ ! -f /config/configuration.yaml ]; then
  cp /seed/configuration.yaml /config/configuration.yaml
fi
exec /init
