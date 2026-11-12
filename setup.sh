#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' 'Usage: ./setup.sh [--with-yabd4 | --uninstall]'
}

if [[ ${1-} == --help || ${1-} == -h ]]; then
    usage
    exit 0
fi
if (( $# > 1 )) || [[ -n ${1-} && $1 != --with-yabd4 && $1 != --uninstall ]]; then
    usage >&2
    exit 2
fi
if (( EUID == 0 )) || [[ $(uname -m) != x86_64 ]] || ! grep -qx 'ID=arch' /etc/os-release; then
    printf '%s\n' 'Run setup as a regular user on Arch Linux x86_64.' >&2
    exit 1
fi

if [[ ${1-} != --uninstall ]]; then
    packages=(extra/discord core/python core/curl extra/bun)
    iris_xe=false
    nvidia=false
    for node in /sys/class/drm/renderD*; do
        [[ -r $node/device/vendor && -r $node/device/device ]] || continue
        read -r vendor < "$node/device/vendor"
        read -r device < "$node/device/device"
        if [[ $vendor == 0x8086 ]]; then
            case "$device" in
                0x46a6|0x46a8|0x46aa|0x46b0|0x46b1|0x46b6|0x46b8|0x46ba|0x46c1|0x9a40|0x9a49|0xa7a0|0xa7a1)
                    iris_xe=true ;;
            esac
        elif [[ $vendor == 0x10de ]]; then
            nvidia=true
        fi
    done
    if [[ $iris_xe == true && $nvidia == true ]]; then
        packages+=(bubblewrap mesa vulkan-intel intel-media-driver libva-utils)
    fi

    missing=()
    for package in "${packages[@]}"; do
        if ! pacman -Qq -- "${package##*/}" >/dev/null 2>&1; then
            missing+=("$package")
        fi
    done
    if (( ${#missing[@]} )); then
        sudo pacman -S --needed -- "${missing[@]}"
    fi
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec /usr/bin/python3 "$script_dir/discord_patch.py" setup "$@"
