#!/bin/bash
set -e

echo "Fixing Playwright dependencies for Ubuntu (Questing/Noble)..."

# Ensure we have the base libraries
apt-get update
# Install common dependencies for Playwright/Chromium with t64 adjustments
apt-get install -y \
    libicu-dev \
    libxml2-16 \
    libxslt1.1 \
    libffi-dev \
    libjpeg-turbo8 \
    libwoff1 \
    libopus0 \
    libwebp7 \
    libwebpdemux2 \
    libenchant-2-2 \
    libgudev-1.0-0 \
    libsecret-1-0 \
    libhyphen0 \
    libgdk-pixbuf-2.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libegl1 \
    libnotify4 \
    libevent-2.1-7t64 \
    libgles2 \
    libvpx9 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    libdbus-glib-1-2 \
    libasound2t64 \
    libatk-bridge2.0-0t64 \
    libatk1.0-0t64 \
    libcairo2 \
    libcups2t64 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libglib2.0-0t64 \
    libgtk-3-0t64 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxshmfence1 \
    libxss1 \
    lsb-release \
    wget \
    curl

# Fix libicu (expecting 74, have 76/78?)
ICU_LIB=$(find /usr/lib/aarch64-linux-gnu -name "libicuuc.so.*" | grep -v ".so.74" | sort -V | tail -n 1)
if [ -n "$ICU_LIB" ]; then
    CURRENT_VER=$(basename "$ICU_LIB" | sed 's/libicuuc.so.//')
    echo "Found libicu version: $CURRENT_VER"
    
    # If the file IS .74, we don't need to link.
    if [[ "$CURRENT_VER" != "74" && "$CURRENT_VER" != "74."* ]]; then
        echo "Symlinking libicu 74 to $CURRENT_VER..."
        ln -sf "/usr/lib/aarch64-linux-gnu/libicuuc.so.$CURRENT_VER" "/usr/lib/aarch64-linux-gnu/libicuuc.so.74"
        ln -sf "/usr/lib/aarch64-linux-gnu/libicudata.so.$CURRENT_VER" "/usr/lib/aarch64-linux-gnu/libicudata.so.74"
        ln -sf "/usr/lib/aarch64-linux-gnu/libicui18n.so.$CURRENT_VER" "/usr/lib/aarch64-linux-gnu/libicui18n.so.74"
    else
        echo "libicu 74 already present."
    fi
else
    echo "Could not find libicu."
fi

# Fix libxml2 (expecting .2, have .16?)
if [ ! -e "/usr/lib/aarch64-linux-gnu/libxml2.so.2" ]; then
    XML_LIB=$(find /usr/lib/aarch64-linux-gnu -name "libxml2.so.*" | sort -V | tail -n 1)
    if [ -n "$XML_LIB" ]; then
        echo "Symlinking libxml2.so.2 to $(basename "$XML_LIB")..."
        ln -sf "$XML_LIB" "/usr/lib/aarch64-linux-gnu/libxml2.so.2"
    fi
else
    echo "libxml2.so.2 already exists."
fi

echo "Done. Playwright should now be usable."