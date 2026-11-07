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
    local expected_image="$1@$2"
    local target_environment=${5:-$environment}
    local target_release=$release
    if [[ "$target_environment" == "staging" ]]; then
        target_release=$staging_release
    fi
    local actual_image
    actual_image=$(kubectl -n "$namespace" get deployment "$target_release-mlops-search" \
        -o 'jsonpath={.spec.template.spec.containers[?(@.name=="api")].image}') || return 1
    printf '%s deployment image: %s\n' "$target_environment" "$actual_image"
    if [[ "$actual_image" != "$expected_image" ]]; then
        echo "Expected deployment image: $expected_image" >&2
        return 1
    fi
    python3 scripts/verify_release.py --release "$target_release" --namespace "$namespace" \
        --expect-digest "$2" --expect-environment "$target_environment" \
        --expect-build-revision "$3" --expect-ranking-mode "$4"
}

rollback() {
    local revision=$1
    shift
    echo "Rolling back $release to revision $revision"
    helm rollback "$release" "$revision" "${helm_args[@]}" --wait=legacy --timeout 5m || return 1
    verify "$@" normal || return 1
    helm history "$release" "${helm_args[@]}"
}

helm lint "$chart" "${values[@]}"
helm template "$release" "$chart" "${helm_args[@]}" "${values[@]}" >/dev/null

if [[ "$environment" == "production" ]]; then
    echo "Rechecking the live staging candidate before production promotion"
    verify "$repository" "$digest" "$build_revision" normal staging
fi

known_good=
release_count=$(helm list "${helm_args[@]}" --filter "^$release$" -o json | jq length)
if [[ "$release_count" != 0 ]]; then
    previous_revision=$(latest_deployed_revision)
    if [[ -n "$previous_revision" ]]; then
        previous_values=$(helm get values "$release" "${helm_args[@]}" \
            --revision "$previous_revision" --all -o json)
        previous_repository=$(jq -r '.image.repository // empty' <<< "$previous_values")
        previous_digest=$(jq -r '.image.digest // empty' <<< "$previous_values")
        previous_build=$(jq -r '.buildRevision // empty' <<< "$previous_values")
        if [[ -n "$previous_digest" ]] && verify "$previous_repository" "$previous_digest" "$previous_build" normal; then
            known_good=$previous_revision
            echo "Last verified revision: $known_good"
        fi
    fi
fi

if ! upgrade normal ||
    ! helm test "$release" "${helm_args[@]}" --logs --timeout 2m ||
    ! verify "$repository" "$digest" "$build_revision" normal; then
    echo "Release verification failed for $release" >&2
    if [[ -n "$known_good" ]]; then
        rollback "$known_good" "$previous_repository" "$previous_digest" "$previous_build"
    fi
    exit 1
fi

if [[ "$drill" == "--reverse-drill" ]]; then
    known_good=$(latest_deployed_revision)
    if [[ -z "$known_good" ]]; then
        echo "No deployed revision found for rollback" >&2
        exit 1
    fi
    echo "Helm history before the reverse release"
    helm history "$release" "${helm_args[@]}"

    reverse_status=0
    if upgrade reverse && helm test "$release" "${helm_args[@]}" --logs --timeout 2m; then
        verify "$repository" "$digest" "$build_revision" reverse || reverse_status=$?
    else
        reverse_status=1
    fi

    if [[ "$reverse_status" == 2 ]]; then
        echo "Reverse candidate passed readiness and failed semantic smoke"
    else
        echo "Reverse drill did not demonstrate the expected semantic failure (status $reverse_status)" >&2
    fi
    echo "Helm history after the reverse release"
    helm history "$release" "${helm_args[@]}" || true
    rollback "$known_good" "$repository" "$digest" "$build_revision"
    [[ "$reverse_status" == 2 ]]
fi

if [[ "$environment" == "production" ]]; then
    echo "Final staging and production verification"
    verify "$repository" "$digest" "$build_revision" normal staging
    verify "$repository" "$digest" "$build_revision" normal
    echo "Both deployments use $repository@$digest"
fi
