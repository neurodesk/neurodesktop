#!/bin/bash
# Sourced by before_notebook.sh, which docker-stacks sources into the shell
# that later execs Jupyter. Sourcing only defines functions; calling
# detect_resource_limits exports MEM_LIMIT (bytes) and CPU_LIMIT (CPUs) for
# jupyter-resource-usage. Detection is best-effort: an unreadable or malformed
# limit leaves the variable as it was, and the function always returns 0.

extract_first_uint() {
	local raw
	raw="$(printf '%s' "${1:-}" | tr -d '[:space:]')"
	if [[ "${raw}" =~ ^([0-9]+) ]]; then
		echo "${BASH_REMATCH[1]}"
		return 0
	fi
	return 1
}

parse_memory_to_mb() {
	local raw value unit
	raw="$(printf '%s' "${1:-}" | tr -d '[:space:]')"
	if [ -z "${raw}" ]; then
		return 1
	fi

	if [[ "${raw}" =~ ^([0-9]+)([KkMmGgTt]?)$ ]]; then
		value="${BASH_REMATCH[1]}"
		unit="${BASH_REMATCH[2]}"
	else
		return 1
	fi

	if [ "${value}" -le 0 ]; then
		return 1
	fi

	case "${unit}" in
	"" | [Mm]) echo "${value}" ;;
	[Kk]) echo $(((value + 1023) / 1024)) ;;
	[Gg]) echo $((value * 1024)) ;;
	[Tt]) echo $((value * 1024 * 1024)) ;;
	*) return 1 ;;
	esac
}

detect_slurm_cpu_limit() {
	local slurm_cpu_limit
	slurm_cpu_limit="$(extract_first_uint "${SLURM_CPUS_ON_NODE:-}" || true)"
	if [ -z "${slurm_cpu_limit}" ]; then
		slurm_cpu_limit="$(extract_first_uint "${SLURM_JOB_CPUS_PER_NODE:-}" || true)"
	fi
	if [ -z "${slurm_cpu_limit}" ]; then
		slurm_cpu_limit="$(extract_first_uint "${SLURM_CPUS_PER_TASK:-}" || true)"
	fi

	if [ -n "${slurm_cpu_limit}" ] && [ "${slurm_cpu_limit}" -gt 0 ]; then
		echo "${slurm_cpu_limit}"
	fi
}

detect_slurm_mem_limit_bytes() {
	local mem_per_node_mb mem_per_cpu_mb slurm_cpu_limit
	mem_per_node_mb="$(parse_memory_to_mb "${SLURM_MEM_PER_NODE:-}" || true)"
	if [ -n "${mem_per_node_mb}" ] && [ "${mem_per_node_mb}" -gt 0 ]; then
		echo $((mem_per_node_mb * 1024 * 1024))
		return 0
	fi

	mem_per_cpu_mb="$(parse_memory_to_mb "${SLURM_MEM_PER_CPU:-}" || true)"
	if [ -z "${mem_per_cpu_mb}" ] || [ "${mem_per_cpu_mb}" -le 0 ]; then
		return 1
	fi

	slurm_cpu_limit="$(detect_slurm_cpu_limit || true)"
	if [ -n "${slurm_cpu_limit}" ] && [ "${slurm_cpu_limit}" -gt 0 ]; then
		echo $((mem_per_cpu_mb * slurm_cpu_limit * 1024 * 1024))
		return 0
	fi

	return 1
}

# Usage: detect_resource_limits [cgroup_root]
# The root defaults to /sys/fs/cgroup; tests pass a temporary directory.
# Slurm allocation variables override the cgroup values inside a Slurm job.
detect_resource_limits() {
	local cgroup_root="${1:-/sys/fs/cgroup}"
	local cgroup_mem_limit cgroup_mem_limit_file
	local cpu_max_line cpu_quota cpu_period cpu_limit
	local cgroup_cpu_quota_file cpu_period_file quota_file
	local slurm_mem_limit_bytes slurm_cpu_limit

	echo "Detecting container resource limits from cgroups..."
	if [ -f "${cgroup_root}/memory.max" ]; then
		cgroup_mem_limit="$(cat "${cgroup_root}/memory.max")"
		if [ "${cgroup_mem_limit}" != "max" ]; then
			export MEM_LIMIT="${cgroup_mem_limit}"
			echo "Memory limit detected (cgroup v2): $(numfmt --to=iec "${cgroup_mem_limit}")"
		else
			echo "Memory limit: unlimited"
		fi
	elif [ -f "${cgroup_root}/memory/memory.limit_in_bytes" ] || [ -f "${cgroup_root}/memory.limit_in_bytes" ]; then
		cgroup_mem_limit_file="${cgroup_root}/memory/memory.limit_in_bytes"
		if [ ! -f "${cgroup_mem_limit_file}" ]; then
			cgroup_mem_limit_file="${cgroup_root}/memory.limit_in_bytes"
		fi
		cgroup_mem_limit="$(cat "${cgroup_mem_limit_file}")"
		if [[ "${cgroup_mem_limit}" =~ ^[0-9]+$ ]] && [ "${cgroup_mem_limit}" -gt 0 ] && [ "${cgroup_mem_limit}" -lt 9000000000000000000 ]; then
			export MEM_LIMIT="${cgroup_mem_limit}"
			echo "Memory limit detected (cgroup v1): $(numfmt --to=iec "${cgroup_mem_limit}")"
		else
			echo "Memory limit: unlimited"
		fi
	else
		echo "No cgroup memory limit file found."
	fi

	if [ -f "${cgroup_root}/cpu.max" ]; then
		cpu_max_line="$(cat "${cgroup_root}/cpu.max")"
		cpu_quota="$(echo "${cpu_max_line}" | awk '{print $1}')"
		cpu_period="$(echo "${cpu_max_line}" | awk '{print $2}')"
		if [ "${cpu_quota}" != "max" ] && [[ "${cpu_period}" =~ ^[0-9]+$ ]] && [ "${cpu_period}" -gt 0 ]; then
			if [[ "${cpu_quota}" =~ ^[0-9]+$ ]]; then
				cpu_limit="$(awk "BEGIN {printf \"%.2f\", ${cpu_quota}/${cpu_period}}")"
				export CPU_LIMIT="${cpu_limit}"
				echo "CPU limit detected (cgroup v2): ${cpu_limit} CPUs"
			fi
		else
			echo "CPU limit: unlimited"
		fi
	else
		cgroup_cpu_quota_file=""
		for quota_file in "${cgroup_root}/cpu/cpu.cfs_quota_us" "${cgroup_root}/cpu,cpuacct/cpu.cfs_quota_us" "${cgroup_root}/cpu.cfs_quota_us"; do
			if [ -f "${quota_file}" ]; then
				cgroup_cpu_quota_file="${quota_file}"
				break
			fi
		done

		if [ -n "${cgroup_cpu_quota_file}" ]; then
			cpu_quota="$(cat "${cgroup_cpu_quota_file}")"
			cpu_period_file="${cgroup_cpu_quota_file%cpu.cfs_quota_us}cpu.cfs_period_us"
			cpu_period="$(cat "${cpu_period_file}" 2>/dev/null || echo 0)"
			if [ "${cpu_quota}" -gt 0 ] && [ "${cpu_period}" -gt 0 ]; then
				cpu_limit="$(awk "BEGIN {printf \"%.2f\", ${cpu_quota}/${cpu_period}}")"
				export CPU_LIMIT="${cpu_limit}"
				echo "CPU limit detected (cgroup v1): ${cpu_limit} CPUs"
			else
				echo "CPU limit: unlimited"
			fi
		else
			echo "No cgroup cpu limit file found."
		fi
	fi

	if [ -n "${SLURM_JOB_ID:-}" ]; then
		echo "Running inside a SLURM job (Job ID: ${SLURM_JOB_ID}). Detecting SLURM limits..."

		slurm_mem_limit_bytes="$(detect_slurm_mem_limit_bytes || true)"
		if [ -n "${slurm_mem_limit_bytes}" ] && [ "${slurm_mem_limit_bytes}" -gt 0 ]; then
			export MEM_LIMIT="${slurm_mem_limit_bytes}"
			echo "Memory limit set from SLURM: $(numfmt --to=iec "${MEM_LIMIT}")"
		fi

		slurm_cpu_limit="$(detect_slurm_cpu_limit || true)"
		if [ -n "${slurm_cpu_limit}" ] && [ "${slurm_cpu_limit}" -gt 0 ]; then
			export CPU_LIMIT="${slurm_cpu_limit}"
			echo "CPU limit set from SLURM: ${CPU_LIMIT}"
		fi
	fi

	return 0
}
