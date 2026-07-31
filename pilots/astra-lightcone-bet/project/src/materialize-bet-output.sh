#!/bin/bash

set -euo pipefail

if [ "$#" -ne 4 ]; then
    echo "usage: $0 OUTPUT_ID INPUT_T1W DECISION_OPTION OUTPUT_DIRECTORY" >&2
    exit 2
fi

output_id="$1"
input_t1w="$2"
decision_option="$3"
output_directory="$4"

case "${decision_option}" in
    f_0_5)
        fractional_threshold="0.5"
        filename_token="betf050"
        ;;
    f_0_3)
        fractional_threshold="0.3"
        filename_token="betf030"
        ;;
    *)
        echo "unsupported BET decision option: ${decision_option}" >&2
        exit 2
        ;;
esac

for command_name in bet fslstats slicer pngappend; do
    command -v "${command_name}" >/dev/null || {
        echo "required FSL module command is unavailable: ${command_name}" >&2
        exit 2
    }
done

mkdir -p "${output_directory}"
work_directory="$(mktemp -d "${output_directory}/.bet-work.XXXXXX")"
trap 'rm -rf "${work_directory}"' EXIT

export FSLOUTPUTTYPE=NIFTI_GZ
brain_prefix="${work_directory}/brain"
bet "${input_t1w}" "${brain_prefix}" -f "${fractional_threshold}" -m

case "${output_id}" in
    bet_brain)
        cp "${brain_prefix}.nii.gz" \
            "${output_directory}/sub-01_ses-test_desc-${filename_token}_T1w.nii.gz"
        ;;
    bet_mask)
        cp "${brain_prefix}_mask.nii.gz" \
            "${output_directory}/sub-01_ses-test_desc-${filename_token}_mask.nii.gz"
        ;;
    mask_volume)
        read -r voxel_count volume_mm3 < <(fslstats "${brain_prefix}_mask.nii.gz" -V)
        /opt/conda/bin/python - \
            "${output_directory}/sub-01_ses-test_desc-${filename_token}_mask-metrics.json" \
            "${fractional_threshold}" "${voxel_count}" "${volume_mm3}" <<'PY'
import json
import sys
from pathlib import Path

destination, threshold, voxel_count, volume_mm3 = sys.argv[1:]
Path(destination).write_text(
    json.dumps(
        {
            "betFractionalThreshold": float(threshold),
            "maskVolumeMm3": float(volume_mm3),
            "maskVoxelCount": int(voxel_count),
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
PY
        ;;
    boundary_qc)
        slicer "${input_t1w}" "${brain_prefix}.nii.gz" -s 2 \
            -x 0.35 "${work_directory}/sagittal-035.png" \
            -x 0.50 "${work_directory}/sagittal-050.png" \
            -y 0.35 "${work_directory}/coronal-035.png" \
            -y 0.50 "${work_directory}/coronal-050.png" \
            -z 0.35 "${work_directory}/axial-035.png" \
            -z 0.50 "${work_directory}/axial-050.png"
        pngappend \
            "${work_directory}/sagittal-035.png" + \
            "${work_directory}/sagittal-050.png" + \
            "${work_directory}/coronal-035.png" + \
            "${work_directory}/coronal-050.png" + \
            "${work_directory}/axial-035.png" + \
            "${work_directory}/axial-050.png" \
            "${output_directory}/sub-01_ses-test_desc-${filename_token}_boundary-qc.png"
        ;;
    *)
        echo "unsupported ASTRA output: ${output_id}" >&2
        exit 2
        ;;
esac
