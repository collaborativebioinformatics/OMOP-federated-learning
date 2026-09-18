#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON:-python3}"
sample_dir="${UKB_SAMPLE_DIR:-../UKB/data/ukb_sampled}"
athena_dir="${ATHENA_DIR:-../OMOP_AGENT}"
run_dir="${RUN_DIR:-solvi/results/run_004_observation_period}"
source_tsv="solvi/data/ukb_10000/ukb_subset.tsv"
spec="ukb_omop_agent/specs/ukb_ad_pd_t2d_labs_v1.json"
scope=(--diagnosis-prefix G30 --diagnosis-prefix F00
       --diagnosis-prefix G20 --diagnosis-prefix E11)

case "${1:-}" in
  prepare)
    "$python_bin" ukb_omop_agent/ukb/fetch_biomarker_sample.py --sample-dir "$sample_dir"
    "$python_bin" ukb_omop_agent/ukb/make_ukb_subset.py \
      --input "$sample_dir" --output solvi/data/ukb_10000 \
      --participants 10000 --cases 0 \
      --fields 31 34 53 30690 30740 30760 30780 30870 41270 41280 \
      > solvi/data/prepare_log.json
    ;;
  inspect)
    "$python_bin" ukb_omop_agent/general_agent.py inspect \
      --input "$source_tsv" --spec "$spec" --vocabulary "$athena_dir" \
      "${scope[@]}" --output "$run_dir"
    ;;
  propose)
    "$python_bin" ukb_omop_agent/ukb/propose_disease_targets.py --run "$run_dir"
    ;;
  apply)
    "$python_bin" ukb_omop_agent/general_agent.py apply \
      --input "$source_tsv" --spec "$spec" --vocabulary "$athena_dir" \
      "${scope[@]}" --review "$run_dir/mapping_review.csv" \
      --output "$run_dir/omop"
    ;;
  qc)
    "$python_bin" ukb_omop_agent/general_agent.py qc \
      --input "$source_tsv" --spec "$spec" --vocabulary "$athena_dir" \
      "${scope[@]}" --review "$run_dir/mapping_review.csv" \
      --output "$run_dir/omop"
    ;;
  plot)
    "$python_bin" ukb_omop_agent/plot_qc.py \
      --run "$run_dir" --omop "$run_dir/omop" \
      --review "$run_dir/mapping_review.csv"
    ;;
  summary)
    "$python_bin" ukb_omop_agent/ukb/summarize.py --source "$source_tsv" --run "$run_dir"
    ;;
  disease-qc)
    "$python_bin" ukb_omop_agent/ukb/disease_qc.py --source "$source_tsv" --run "$run_dir"
    ;;
  visual-qc)
    "$python_bin" ukb_omop_agent/ukb/visual_qc.py --run "$run_dir"
    ;;
  *)
    echo "Usage: bash ukb_omop_agent/ukb/run_ad_pd_t2d_labs.sh {prepare|inspect|propose|apply|qc|plot|summary|disease-qc|visual-qc}" >&2
    echo "Optional: UKB_SAMPLE_DIR, ATHENA_DIR, RUN_DIR, PYTHON" >&2
    exit 2
    ;;
esac
