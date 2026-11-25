# Shared by the development launchers after their image checks succeed.

prepare_replacement() {
    local current auto_remove
    AURORA_SWAP_NAME=$1
    AURORA_SWAP_OLD_ID=
    AURORA_SWAP_WAS_RUNNING=
    AURORA_SWAP_DIR=$(mktemp -d) || return 1
    AURORA_SWAP_CID_FILE="$AURORA_SWAP_DIR/new-container"
    current=$(docker container inspect --format '{{.Id}} {{.State.Running}} {{.HostConfig.AutoRemove}}' "$1" 2>/dev/null) || return 0
    read -r AURORA_SWAP_OLD_ID AURORA_SWAP_WAS_RUNNING auto_remove <<< "$current"

    if [ "$auto_remove" = true ]; then
        echo "Replacing a legacy auto-remove container. Its Docker state cannot be saved for rollback."
        docker stop "$AURORA_SWAP_OLD_ID" >/dev/null || return 1
        AURORA_SWAP_OLD_ID=
        return 0
    fi

    docker rename "$AURORA_SWAP_OLD_ID" "$1-previous-${AURORA_SWAP_OLD_ID:0:12}" || return 1
    if [ "$AURORA_SWAP_WAS_RUNNING" = true ] && ! docker stop "$AURORA_SWAP_OLD_ID" >/dev/null; then
        docker rename "$AURORA_SWAP_OLD_ID" "$1"
        return 1
    fi
}

begin_replacement() {
    if ! prepare_replacement "$1"; then
        [ -z "${AURORA_SWAP_DIR:-}" ] || clear_replacement_id_file
        return 1
    fi
    trap 'result=$?; trap - EXIT; restore_replacement || echo "Automatic Docker rollback failed." >&2; exit "$result"' EXIT
}

clear_replacement_id_file() {
    rm -f -- "$AURORA_SWAP_CID_FILE"
    rmdir -- "$AURORA_SWAP_DIR"
}

restore_replacement() {
    local failed running
    failed=$(cat "$AURORA_SWAP_CID_FILE" 2>/dev/null) || failed=
    clear_replacement_id_file
    if [ -n "$failed" ] && running=$(docker container inspect --format '{{.State.Running}}' "$failed" 2>/dev/null); then
        if [ "$running" = true ]; then
            docker stop "$failed" >/dev/null || return 1
        fi
        docker rename "$failed" "$AURORA_SWAP_NAME-failed-${failed:0:12}" || return 1
        echo "Failed replacement retained for inspection: docker logs $failed" >&2
    fi
    if [ -n "$AURORA_SWAP_OLD_ID" ]; then
        docker rename "$AURORA_SWAP_OLD_ID" "$AURORA_SWAP_NAME" || return 1
        if [ "$AURORA_SWAP_WAS_RUNNING" = true ]; then
            docker start "$AURORA_SWAP_OLD_ID" >/dev/null || return 1
        fi
        echo "Restored the previous $AURORA_SWAP_NAME container." >&2
    fi
}

finish_replacement() {
    trap - EXIT
    clear_replacement_id_file
    if [ -n "$AURORA_SWAP_OLD_ID" ]; then
        docker rm "$AURORA_SWAP_OLD_ID" >/dev/null ||
            echo "The previous stopped container remains available: $AURORA_SWAP_OLD_ID" >&2
    fi
}
