#!/bin/sh
# Builds OpenGMice.flatpak, a single-file bundle anyone can install with
#   flatpak install --user OpenGMice.flatpak
set -e
cd "$(dirname "$0")/.."
APP=io.github.sabam54.OpenGMice
flatpak --user remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak --user install -y --noninteractive flathub org.flatpak.Builder
flatpak run org.flatpak.Builder --user --install-deps-from=flathub --force-clean \
    --repo=.flatpak-repo .flatpak-build "$APP.yml"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    .flatpak-repo OpenGMice.flatpak "$APP"
echo "Built $(pwd)/OpenGMice.flatpak"
