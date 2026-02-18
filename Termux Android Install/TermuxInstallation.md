# Installation Guide for Termux on Android

This guide covers how to set up the environment required to run `cbz2xtc` and `web2xtc` on an Android device using Termux.

These tools require a specific Linux environment (Ubuntu) running inside Termux to support the web browser automation features (Playwright).

## Prerequisites

1.  **Install Termux**: Download and install Termux from F-Droid (recommended) or GitHub. Do not use the Play Store version as it is outdated.
2.  **Storage Permission**: Open Termux and run the following command to allow access to your phone's storage:
    ```bash
    termux-setup-storage
    ```

## Step 1: Install Ubuntu in Termux

We will use `proot-distro` to install a lightweight Ubuntu Linux environment.

1.  Open Termux.
2.  Update the Termux package repositories:
    ```bash
    pkg update && pkg upgrade
    ```
3.  Install the proot-distro utility:
    ```bash
    pkg install proot-distro
    ```
4.  Install Ubuntu:
    ```bash
    proot-distro install ubuntu
    ```

## Step 2: Configure the Ubuntu Environment

Now we will log into Ubuntu and install Python and the necessary system tools.

1.  Log into the Ubuntu environment:
    ```bash
    proot-distro login ubuntu
    ```
    *Your prompt should change to look like `root@localhost`.*

2.  Update the Ubuntu package lists:
    ```bash
    apt update && apt upgrade -y
    ```

3.  Install Python, pip, and other required tools:
    ```bash
    apt install -y python3 python3-pip python3-venv git wget nano curl
    ```

## Step 3: Set Up the Python Environment

To keep the installation clean, we will use a virtual environment.

1.  Ensure you are still inside Ubuntu (`root@localhost`).
2.  Create a virtual environment named `venv` in the root directory:
    ```bash
    python3 -m venv /root/venv
    ```
3.  Activate the virtual environment:
    ```bash
    source /root/venv/bin/activate
    ```
    *You should see `(venv)` appear at the start of your command prompt.*

4.  Install the required Python libraries:
    ```bash
    pip install pillow numpy numba pymupdf playwright
    ```

## Step 4: Install and Fix Playwright

Playwright controls the browser used by `web2xtc`. It requires specific system libraries that are often missing or version-mismatched on Android/Ubuntu.

1.  Install the Playwright browsers:
    ```bash
    playwright install chromium
    ```
    *Note: You may see "BEWARE" warnings or failure messages. This is normal.*

2.  Create the fix script. Copy the entire block below and paste it into your Termux terminal:

    ```bash
    cat << 'EOF' > fix_playwright.sh
    #!/bin/bash
    set -e
    echo "Installing dependencies..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y libicu-dev libxml2-16 libxslt1.1 libffi-dev libjpeg-turbo8 
        libwoff1 libopus0 libwebp7 libwebpdemux2 libenchant-2-2 libgudev-1.0-0 
        libsecret-1-0 libhyphen0 libgdk-pixbuf-2.0-0 libgdk-pixbuf-xlib-2.0-0 
        libegl1 libnotify4 libevent-2.1-7t64 libgles2 libvpx9 libxcomposite1 
        libxcursor1 libxdamage1 libxfixes3 libxi6 libxrandr2 libxrender1 
        libxtst6 libdbus-glib-1-2 libasound2t64 libatk-bridge2.0-0t64 
        libatk1.0-0t64 libcairo2 libcups2t64 libdbus-1-3 libexpat1 libfontconfig1 
        libgbm1 libglib2.0-0t64 libgtk-3-0t64 libnspr4 libnss3 libpango-1.0-0 
        libpangocairo-1.0-0 libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxshmfence1 
        libxss1 lsb-release wget curl

    echo "Fixing library links..."
    # Fix libicu
    ICU_LIB=$(find /usr/lib/aarch64-linux-gnu -name "libicuuc.so.*" | grep -v ".so.74" | sort -V | tail -n 1)
    if [ -n "$ICU_LIB" ]; then
        VER=$(basename "$ICU_LIB" | sed 's/libicuuc.so.//')
        if [[ "$VER" != "74" && "$VER" != "74."* ]]; then
            ln -sf "/usr/lib/aarch64-linux-gnu/libicuuc.so.$VER" "/usr/lib/aarch64-linux-gnu/libicuuc.so.74"
            ln -sf "/usr/lib/aarch64-linux-gnu/libicudata.so.$VER" "/usr/lib/aarch64-linux-gnu/libicudata.so.74"
            ln -sf "/usr/lib/aarch64-linux-gnu/libicui18n.so.$VER" "/usr/lib/aarch64-linux-gnu/libicui18n.so.74"
        fi
    fi

    # Fix libxml2
    if [ ! -e "/usr/lib/aarch64-linux-gnu/libxml2.so.2" ]; then
        XML_LIB=$(find /usr/lib/aarch64-linux-gnu -name "libxml2.so.*" | sort -V | tail -n 1)
        if [ -n "$XML_LIB" ]; then
            ln -sf "$XML_LIB" "/usr/lib/aarch64-linux-gnu/libxml2.so.2"
        fi
    fi
    echo "Done."
    EOF
    ```

3.  Run the fix script:
    ```bash
    bash fix_playwright.sh
    ```

## Step 5: How to Run the Tools

You must always be inside the Ubuntu environment with the virtual environment activated to run the tools.

**To start a new session:**

1.  Open Termux.
2.  Login to Ubuntu:
    ```bash
    proot-distro login ubuntu
    ```
3.  Activate Python:
    ```bash
    source /root/venv/bin/activate
    ```
4.  Navigate to your script folder.
    *   If your scripts are in your Downloads folder:
        ```bash
        cd /storage/emulated/0/Download/cbz2xtc
        ```
    *   If your scripts are in the home folder inside Ubuntu:
        ```bash
        cd cbz2xtc
        ```

5.  Run the tool:
    ```bash
    python3 web2xtc.py "https://example.com"
    ```
