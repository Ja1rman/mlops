#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ $# -lt 4 || $# -gt 5 ]]; then
    echo "Usage: $0 {staging|production} IMAGE_REPOSITORY IMAGE_DIGEST BUILD_REVISION [--reverse-drill]" >&2
    exit 1
fi

environment=$1
repository=$2
digest=$3
build_revision=$4
drill=${5:-}
staging_release=ovsyannikov-search-staging

case "$environment" in
    staging) release=$staging_release ;;
    production) release=ovsyannikov-search-prod ;;
    *) echo "Unknown environment: $environment" >&2; exit 1 ;;
esac
if [[ -n "$drill" && ( "$drill" != "--reverse-drill" || "$environment" != "production" ) ]]; then
    echo "The only supported extra argument is --reverse-drill for production" >&2
    exit 1
fi

export HELM_DRIVER=configmap
namespace=mlops-students
chart=helm/mlops-search
helm_args=(--namespace "$namespace")
values=(
    --set-string "image.repository=$repository"
    --set-string "image.digest=$digest"
    --set-string "environment=$environment"
    --set-string "buildRevision=$build_revision"
)

latest_deployed_revision() {
    helm history "$release" "${helm_args[@]}" -o json |
        jq -r '[.[] | select(.status == "deployed")] | max_by(.revision) | .revision // empty'
}

upgrade() {
    helm upgrade --install "$release" "$chart" "${helm_args[@]}" "${values[@]}" \
        --reset-values --set-string "rankingMode=$1" --wait=legacy --timeout 5m
}

verify() {
    local mode=$1
    local target_environment=${2:-$environment}
    local target_release=$release
    if [[ "$target_environment" == "staging" ]]; then
        target_release=$staging_release
    fi
    local actual_image
    actual_image=$(kubectl -n "$namespace" get deployment "$target_release-mlops-search" \
        -o 'jsonpath={.spec.template.spec.containers[?(@.name=="api")].image}') || return 1
    printf '%s deployment image: %s\n' "$target_environment" "$actual_image"
    if [[ "$actual_image" != "$repository@$digest" ]]; then
        echo "Expected deployment image: $repository@$digest" >&2
        return 1
    fi
    python3 scripts/verify_release.py --release "$target_release" --namespace "$namespace" \
        --expect-digest "$digest" --expect-environment "$target_environment" \
        --expect-build-revision "$build_revision" --expect-ranking-mode "$mode"
}

helm lint "$chart" "${values[@]}"
helm template "$release" "$chart" "${helm_args[@]}" "${values[@]}" >/dev/null

if [[ "$environment" == "production" ]]; then
    echo "Rechecking the live staging candidate before production promotion"
    verify normal staging
fi

upgrade normal
if [[ "$environment" == "staging" ]]; then
    helm test "$release" "${helm_args[@]}" --logs --timeout 2m
fi
verify normal

if [[ "$drill" == "--reverse-drill" ]]; then
    known_good=$(latest_deployed_revision)
    if [[ -z "$known_good" ]]; then
        echo "No deployed revision found for rollback" >&2
        exit 1
    fi
    echo "Helm history before the reverse release"
    helm history "$release" "${helm_args[@]}"

    reverse_status=0
    if upgrade reverse; then
        verify reverse || reverse_status=$?
    else
        reverse_status=1
    fi

    if [[ "$reverse_status" == 2 ]]; then
        echo "Reverse candidate passed readiness and failed semantic smoke"
    else
        echo "Reverse drill did not demonstrate the expected semantic failure (status $reverse_status)" >&2
    fi
    echo "Rolling back $release to revision $known_good"
    helm rollback "$release" "$known_good" "${helm_args[@]}" --wait=legacy --timeout 5m
    verify normal
    echo "Helm history after rollback"
    helm history "$release" "${helm_args[@]}"
    [[ "$reverse_status" == 2 ]]
fi
