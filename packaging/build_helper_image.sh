#!/bin/bash
# Build the controller helper image: a minimal Ubuntu 22.04 root filesystem
# with BlueZ and NXBT, imported on users' PCs with `wsl --import`.
# Run as root inside an existing Ubuntu WSL distribution (needs internet):
#   wsl -d Ubuntu -u root -- bash packaging/build_helper_image.sh /mnt/c/.../dist/smash-recorder-helper.tar.gz
set -euo pipefail

OUT=${1:?output .tar.gz path}
NXBT_REPO=https://github.com/Brikwerk/nxbt.git
NXBT_COMMIT=ec4b800ad6c55de96bb6c7f9f84b5bdc59a4c975   # the commit verified with Switch pairing
WORK=$(mktemp -d /var/tmp/smash-helper-XXXXXX)
ROOTFS=$WORK/rootfs
trap 'umount -l "$ROOTFS/proc" 2>/dev/null || true; rm -rf "$WORK"' EXIT

apt-get update -qq
apt-get install -y -qq debootstrap git >/dev/null

debootstrap --variant=minbase \
  --include=systemd,systemd-sysv,dbus,kmod,bluez,python3,python3-dbus,python3-psutil,ca-certificates \
  jammy "$ROOTFS" http://archive.ubuntu.com/ubuntu

git clone -q "$NXBT_REPO" "$WORK/nxbt"
git -C "$WORK/nxbt" checkout -q "$NXBT_COMMIT"
mkdir -p "$ROOTFS/opt/smash-recorder"
cp -a "$WORK/nxbt" "$ROOTFS/opt/smash-recorder/nxbt"
rm -rf "$ROOTFS/opt/smash-recorder/nxbt/.git"
echo "$NXBT_COMMIT" > "$ROOTFS/opt/smash-recorder/NXBT_COMMIT"

cat > "$ROOTFS/etc/wsl.conf" <<'EOF'
[boot]
systemd=true
[user]
default=root
[interop]
appendWindowsPath=false
EOF

# Keep the image small and free of build-machine identity.
chroot "$ROOTFS" apt-get clean
rm -rf "$ROOTFS"/var/lib/apt/lists/* "$ROOTFS"/var/cache/apt/* "$ROOTFS"/tmp/* "$ROOTFS"/var/log/*
: > "$ROOTFS/etc/machine-id"
rm -rf "$ROOTFS/var/lib/bluetooth" && install -d -m 700 "$ROOTFS/var/lib/bluetooth"   # empty: no pairings, but bluetoothd requires it

tar --numeric-owner -C "$ROOTFS" -czf "$OUT" .
ls -l "$OUT"
sha256sum "$OUT"
