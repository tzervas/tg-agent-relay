#!/bin/bash
# lib/routing.sh - Multi-backend / multi-chat route resolution.
#
# Sourced by tg-poll.sh and relay-notify.sh (never executed directly).
# Requires relay-config.sh already loaded (cfg_get, RELAY_CONFIG_JSON).
#
# With no [backends] / [[chats]] config, every helper is a no-op-friendly
# passthrough so the single-chat legacy path stays byte-identical.
#
# route_resolve <chat_id> <thread_id> <text>
#   Prints: backend|project|stripped_text|match_kind
#   match_kind: chat | prefix | default | none | legacy
#
# route_lookup_chat <backend> <project>
#   Reverse lookup: first [[chats]] entry matching backend (+ project if
#   non-empty). Prints: chat_id|thread_id  (thread may be empty)
#
# route_format_tag <backend> <project>
#   Outbound prefix like "[claude · mycelium]" (empty when no backend)
#
# project_worktree <project> <backend>
#   Resolves cwd from [projects.<id>.worktrees.<backend>] or
#   [projects.<id>.root] or the bare project string if it looks like a path.
set -u

# Internal: list backend ids from config (empty if none).
_route_backend_ids() {
    if ! declare -f cfg_has_section >/dev/null 2>&1 || ! cfg_has_section "backends"; then
        return 0
    fi
    printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r '.backends // {} | keys[]?' 2>/dev/null
}

# backend_cfg_get <backend_id> <field> <default>
backend_cfg_get() {
    local id="$1" field="$2" default="$3"
    cfg_get ".backends.\"$id\".\"$field\"" "$default"
}

# route_has_routing_config -> 0 if backends or chats configured
route_has_routing_config() {
    if declare -f cfg_has_section >/dev/null 2>&1; then
        cfg_has_section "backends" && return 0
        # chats is an array at top level
        local n
        n="$(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r '(.chats // []) | length' 2>/dev/null)"
        [[ "$n" =~ ^[0-9]+$ ]] && (( n > 0 )) && return 0
    fi
    return 1
}

# _route_chat_binding <chat_id> <thread_id>
# Prints: backend|project|label  if a [[chats]] row matches; else empty.
# Prefer exact thread match; fall back to chat-only rows with no thread_id.
_route_chat_binding() {
    local chat_id="$1" thread_id="${2:-}"
    local json
    json="$(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r --arg cid "$chat_id" --arg tid "$thread_id" '
      (.chats // []) as $chats
      | (
          if $tid != "" then
            ($chats
              | map(select((.chat_id|tostring) == $cid
                           and ((.thread_id|tostring) == $tid)))
              | .[0] // null)
          else null end
        ) as $exact
      | (if $exact != null then $exact
         else
           ($chats
             | map(select(
                 (.chat_id|tostring) == $cid
                 and (
                   (.thread_id == null)
                   or (.thread_id == "")
                   or ((.thread_id|tostring) == "0")
                   or (has("thread_id")|not)
                 )
               ))
             | .[0] // null)
         end) as $hit
      | if $hit == null then empty
        else "\($hit.backend // "")|\($hit.project // "")|\($hit.label // "")"
        end
    ' 2>/dev/null)"
    printf '%s' "$json"
}

# _route_strip_prefix <text>
# Tries every backend's prefixes (longest first). Prints:
#   backend|project|stripped   on match, else empty.
_route_strip_prefix() {
    local text="$1"
    local id pref project stripped best_len=0 best=""
    local -a pref_list

    while IFS= read -r id; do
        [[ -z "$id" ]] && continue
        project="$(backend_cfg_get "$id" project "")"
        # Read prefixes as a JSON array via jq -c (cfg_get pretty-prints arrays).
        mapfile -t pref_list < <(
            printf '%s' "${RELAY_CONFIG_JSON:-{}}" \
                | jq -r --arg id "$id" '(.backends[$id].prefixes // []) | if type=="array" then .[] else (if type=="string" and length>0 then . else empty end) end' 2>/dev/null
        )
        for pref in "${pref_list[@]}"; do
            pref="${pref#"${pref%%[![:space:]]*}"}"
            pref="${pref%"${pref##*[![:space:]]}"}"
            [[ -z "$pref" ]] && continue
            if [[ "$text" == "$pref" || "$text" == "$pref "* || "$text" == "${pref}:"* ]]; then
                if (( ${#pref} > best_len )); then
                    best_len=${#pref}
                    if [[ "$text" == "$pref" ]]; then
                        stripped=""
                    elif [[ "$text" == "${pref}:"* ]]; then
                        stripped="${text#"${pref}:"}"
                        stripped="${stripped#"${stripped%%[![:space:]]*}"}"
                    else
                        stripped="${text#"$pref"}"
                        stripped="${stripped#"${stripped%%[![:space:]]*}"}"
                    fi
                    best="${id}|${project}|${stripped}"
                fi
            fi
        done
    done < <(_route_backend_ids)

    printf '%s' "$best"
}

# route_resolve <chat_id> <thread_id> <text>
route_resolve() {
    local chat_id="${1:-}" thread_id="${2:-}" text="${3:-}"
    local binding backend project rest stripped text_out default_backend proj_default

    if ! route_has_routing_config; then
        printf '||%s|legacy' "$text"
        return 0
    fi

    binding="$(_route_chat_binding "$chat_id" "$thread_id")"
    if [[ -n "$binding" ]]; then
        backend="${binding%%|*}"
        rest="${binding#*|}"
        project="${rest%%|*}"
        # Project-only room (backend empty): sticky project; backend from
        # @prefix inside the room, else project/global default.
        if [[ -z "$backend" && -n "$project" ]]; then
            stripped="$(_route_strip_prefix "$text")"
            if [[ -n "$stripped" ]]; then
                backend="${stripped%%|*}"
                rest="${stripped#*|}"
                # keep sticky project; ignore prefix's project field
                text_out="${rest#*|}"
                printf '%s|%s|%s|chat' "$backend" "$project" "$text_out"
                return 0
            fi
            proj_default="$(cfg_get ".projects.\"$project\".default_backend" "")"
            [[ -z "$proj_default" ]] && proj_default="$(cfg_get '.routing.default_backend' "")"
            backend="$proj_default"
            printf '%s|%s|%s|chat' "$backend" "$project" "$text"
            return 0
        fi
        # Fully sticky (backend + project, or backend-only legacy)
        printf '%s|%s|%s|chat' "$backend" "$project" "$text"
        return 0
    fi

    stripped="$(_route_strip_prefix "$text")"
    if [[ -n "$stripped" ]]; then
        backend="${stripped%%|*}"
        rest="${stripped#*|}"
        project="${rest%%|*}"
        text_out="${rest#*|}"
        [[ -z "$project" ]] && project="$(backend_cfg_get "$backend" project "")"
        printf '%s|%s|%s|prefix' "$backend" "$project" "$text_out"
        return 0
    fi

    default_backend="$(cfg_get '.routing.default_backend' "")"
    if [[ -n "$default_backend" ]]; then
        project="$(backend_cfg_get "$default_backend" project "")"
        printf '%s|%s|%s|default' "$default_backend" "$project" "$text"
        return 0
    fi

    require="$(cfg_get '.routing.require_prefix' 'false')"
    if [[ "$require" == "true" ]]; then
        printf '||%s|none' "$text"
        return 0
    fi

    printf '||%s|legacy' "$text"
}

# route_lookup_chat <backend> <project>
# Prefer: exact backend+project → project-only room → backend-only.
route_lookup_chat() {
    local backend="$1" project="${2:-}"
    printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r --arg b "$backend" --arg p "$project" '
      (.chats // []) as $c
      | (
          if $p != "" and $b != "" then
            ($c | map(select((.project // "") == $p and (.backend // "") == $b)) | .[0] // null)
          else null end
        ) as $exact
      | (
          if $exact != null then $exact
          elif $p != "" then
            ($c | map(select((.project // "") == $p and ((.backend // "") == ""))) | .[0] // null)
          else null end
        ) as $proj
      | (
          if $proj != null then $proj
          elif $p != "" then
            ($c | map(select((.project // "") == $p)) | .[0] // null)
          else null end
        ) as $anyproj
      | (
          if $anyproj != null then $anyproj
          elif $b != "" then
            ($c | map(select((.backend // "") == $b)) | .[0] // null)
          else null end
        ) as $hit
      | if $hit == null then empty
        else "\($hit.chat_id)|\($hit.thread_id // "")"
        end
    ' 2>/dev/null
}

# route_lookup_project <project> → chat_id|thread_id for primary project room
route_lookup_project() {
    local project="${1:-}"
    [[ -z "$project" ]] && return 0
    printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r --arg p "$project" '
      (.chats // [])
      | map(select((.project // "") == $p))
      | .[0] // empty
      | if . == null or . == empty then empty
        else "\(.chat_id)|\(.thread_id // "")"
        end
    ' 2>/dev/null
}

# project_from_cwd <path> → project slug (longest matching root/worktree prefix)
project_from_cwd() {
    local cwd="${1:-}" slug best_len=0 best="" root wt abs
    [[ -z "$cwd" ]] && { printf ''; return 0; }
    # Resolve ~ and make absolute if possible
    cwd="${cwd/#\~/$HOME}"
    if command -v realpath >/dev/null 2>&1; then
        cwd="$(realpath -m "$cwd" 2>/dev/null || printf '%s' "$cwd")"
    fi
    while IFS= read -r slug; do
        [[ -z "$slug" ]] && continue
        root="$(cfg_get ".projects.\"$slug\".root" "")"
        root="${root/#\~/$HOME}"
        if [[ -n "$root" ]]; then
            if command -v realpath >/dev/null 2>&1; then
                root="$(realpath -m "$root" 2>/dev/null || printf '%s' "$root")"
            fi
            if [[ "$cwd" == "$root" || "$cwd" == "$root"/* ]]; then
                if (( ${#root} > best_len )); then
                    best_len=${#root}
                    best="$slug"
                fi
            fi
        fi
        # worktrees map values
        while IFS= read -r wt; do
            [[ -z "$wt" ]] && continue
            abs="${wt/#\~/$HOME}"
            if command -v realpath >/dev/null 2>&1; then
                abs="$(realpath -m "$abs" 2>/dev/null || printf '%s' "$abs")"
            fi
            if [[ "$cwd" == "$abs" || "$cwd" == "$abs"/* ]]; then
                if (( ${#abs} > best_len )); then
                    best_len=${#abs}
                    best="$slug"
                fi
            fi
        done < <(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r --arg s "$slug" '(.projects[$s].worktrees // {}) | .[]?' 2>/dev/null)
    done < <(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r '.projects // {} | keys[]?' 2>/dev/null)
    printf '%s' "$best"
}

# route_format_tag <backend> <project>
route_format_tag() {
    local backend="${1:-}" project="${2:-}"
    local tag style
    [[ -z "$backend" ]] && { printf ''; return 0; }
    tag="$(backend_cfg_get "$backend" tag "$backend")"
    style="$(cfg_get '.routing.tag_style' 'bracket')"
    # project may be a path — use basename slug for display
    local proj_disp="$project"
    if [[ "$proj_disp" == */* || "$proj_disp" == ~* ]]; then
        proj_disp="$(basename "${proj_disp/#\~/$HOME}")"
    fi
    case "$style" in
        none) printf '' ;;
        bare)
            if [[ -n "$proj_disp" ]]; then
                printf '%s · %s' "$tag" "$proj_disp"
            else
                printf '%s' "$tag"
            fi
            ;;
        *)
            if [[ -n "$proj_disp" ]]; then
                printf '[%s · %s]' "$tag" "$proj_disp"
            else
                printf '[%s]' "$tag"
            fi
            ;;
    esac
}

# project_worktree <project> <backend>
project_worktree() {
    local project="${1:-}" backend="${2:-}"
    local wt root
    [[ -z "$project" ]] && { printf ''; return 0; }

    # If project is a slug with worktrees table
    wt="$(cfg_get ".projects.\"$project\".worktrees.\"$backend\"" "")"
    if [[ -n "$wt" ]]; then
        printf '%s' "${wt/#\~/$HOME}"
        return 0
    fi
    root="$(cfg_get ".projects.\"$project\".root" "")"
    if [[ -n "$root" ]]; then
        printf '%s' "${root/#\~/$HOME}"
        return 0
    fi
    # Bare path project
    if [[ "$project" == /* || "$project" == ~* || "$project" == ./* ]]; then
        printf '%s' "${project/#\~/$HOME}"
        return 0
    fi
    printf '%s' "$project"
}

# route_inbound_tag <backend> <project>
# Tag for stdout/fifo event lines.
route_inbound_tag() {
    local backend="${1:-}" project="${2:-}"
    local proj_slug="$project"
    if [[ "$proj_slug" == */* || "$proj_slug" == ~* ]]; then
        proj_slug="$(basename "${proj_slug/#\~/$HOME}")"
    fi
    if [[ -n "$backend" && -n "$proj_slug" ]]; then
        printf '[telegram:backend:%s:project:%s]' "$backend" "$proj_slug"
    elif [[ -n "$backend" ]]; then
        printf '[telegram:backend:%s]' "$backend"
    else
        printf '[telegram]'
    fi
}
