#!/usr/bin/env bash

set -euo pipefail

usage()
{
	cat <<'EOF'
Usage: qfw_slurm_install.sh --prefix <prefix> --python <python>
       [--source-dir <dir>] [--build-dir <dir>]
       [--without-plugin] [--skip-gateway]

Developer convenience installer. It delegates native installation to CMake
and installs the Python gateway into the explicitly selected Python
environment.

--prefix <prefix>     Native command, plugin, configuration, unit, and manual
                      installation prefix.
--python <python>     Python interpreter for the gateway installation.
--source-dir <dir>    qfw-slurm source tree. Default: parent of this script.
--build-dir <dir>     CMake build directory. Default: <source>/build.
--without-plugin      Build without Slurm headers or the SPANK module.
--skip-gateway        Do not install the Python gateway. This also makes
                      --python optional.
-h, --help            Show this help.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$(cd "${script_dir}/.." && pwd)"
build_dir=""
prefix=""
python=""
build_plugin=ON
install_gateway=1

while [[ $# -gt 0 ]]; do
	case "$1" in
		--prefix)
			[[ $# -ge 2 ]] || {
				echo "--prefix requires a path" >&2
				exit 2
			}
			prefix="$2"
			shift 2
			;;
		--python)
			[[ $# -ge 2 ]] || {
				echo "--python requires a path" >&2
				exit 2
			}
			python="$2"
			shift 2
			;;
		--source-dir)
			[[ $# -ge 2 ]] || {
				echo "--source-dir requires a path" >&2
				exit 2
			}
			source_dir="$2"
			shift 2
			;;
		--build-dir)
			[[ $# -ge 2 ]] || {
				echo "--build-dir requires a path" >&2
				exit 2
			}
			build_dir="$2"
			shift 2
			;;
		--without-plugin)
			build_plugin=OFF
			shift
			;;
		--skip-gateway)
			install_gateway=0
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown argument: $1" >&2
			usage >&2
			exit 2
			;;
	esac
done

if [[ -z "${prefix}" ]]; then
	usage >&2
	exit 2
fi
if [[ ! -f "${source_dir}/CMakeLists.txt" ||
      ! -f "${source_dir}/pyproject.toml" ]]; then
	echo "qfw-slurm source tree is invalid: ${source_dir}" >&2
	exit 2
fi
if [[ -z "${build_dir}" ]]; then
	build_dir="${source_dir}/build"
fi
if [[ "${install_gateway}" -eq 1 && -z "${python}" ]]; then
	echo "--python is required unless --skip-gateway is used" >&2
	exit 2
fi
if [[ -n "${python}" && ! -x "${python}" ]]; then
	echo "Python interpreter is not executable: ${python}" >&2
	exit 2
fi

cmake -S "${source_dir}" -B "${build_dir}" \
	-DCMAKE_BUILD_TYPE=Release \
	-DCMAKE_INSTALL_PREFIX="${prefix}" \
	-DQFW_SLURM_BUILD_PLUGIN="${build_plugin}"
cmake --build "${build_dir}"
cmake --install "${build_dir}"

if [[ "${install_gateway}" -eq 1 ]]; then
	"${python}" -m pip install \
		--no-build-isolation \
		--no-deps \
		"${source_dir}"
fi
