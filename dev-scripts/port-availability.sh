# Shared by the CPU, additional instance, and GPU launchers.
resolve_auroracoder_ports() {
    local reserved=" "
    local width=$((DEV_PORT_END - DEV_PORT_START + 1))
    if (( width < 1 )); then width=3; fi

    resolve_port_block BACKEND_PORT 1 1000 || return
    resolve_port_block FRONTEND_PORT 1 1000 || return
    resolve_port_block VNC_PORT 1 1000 || return
    resolve_port_block TOOLSTORE_PORT 1 1000 || return
    resolve_port_block DEV_PORT_START "$width" 10000 || return
    DEV_PORT_END=$((DEV_PORT_START + width - 1))
}

resolve_port_block() {
    local target=$1 width=$2 distance=$3
    local start=${!target} base port end
    for ((base=start; base<start+distance && base+width-1<=65535; base++)); do
        end=$((base + width - 1))
        for ((port=base; port<=end; port++)); do
            if [[ $reserved == *" $port "* ]] || (: </dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
                break
            fi
        done
        if (( port > end )); then
            printf -v "$target" '%s' "$base"
            for ((port=base; port<=end; port++)); do reserved+="$port "; done
            return 0
        fi
    done
    printf 'No free port block for %s starting at %s.\n' "$target" "$start" >&2
    return 1
}
