#!/usr/bin/env bash
set -euo pipefail

base_url=${1:?Pass the base URL of the Iris API}

curl -fsS --max-time 3 --retry 30 --retry-all-errors --retry-delay 1 --retry-max-time 45 \
  "$base_url/health" | jq -e '.status == "ready" and .model_version == "iris-v1"'
curl -fsS --max-time 3 "$base_url/live" | jq -e '.status == "alive"'

check_prediction() {
  local expected_class=$1
  local features=$2
  curl -fsS --max-time 3 "$base_url/predict" \
    -H 'Content-Type: application/json' -d "{\"features\":$features}" \
    | jq -e --argjson expected_class "$expected_class" \
      '.class_id == $expected_class and .model_version == "iris-v1"'
}

check_prediction 0 '[5.1,3.5,1.4,0.2]'
check_prediction 1 '[7.0,3.2,4.7,1.4]'
check_prediction 2 '[6.3,3.3,6.0,2.5]'
