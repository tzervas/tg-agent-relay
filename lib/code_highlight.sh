#!/bin/bash
# lib/code_highlight.sh - Host-highlighted, self-contained HTML documents
# for fenced code blocks (v0.5.0). Sourced by tg-send.sh (never executed
# directly - same convention as every other lib/*.sh in this repo: no
# shebang execution path).
#
# --- The hard constraint (why a DOCUMENT, not inline color) ---------------
# Telegram message TEXT supports NO color at all - a fixed HTML entity set,
# and `<pre>`/`<code>` cannot even NEST `<b>`/`<i>` around individual
# tokens (see lib/format.sh's header). So true per-token colored syntax
# highlighting INSIDE a chat bubble is structurally impossible as text -
# full stop, no workaround. lib/format.sh's EXISTING, ALWAYS-ON v0.3.0
# `<pre><code class="language-X">` box (unchanged, still the default for
# every fenced block, in every message) is the best a chat bubble can do:
# it lights up only on a Telegram CLIENT that ships its own highlighter,
# and even then it's monochrome-per-message-theme, never truly per-token
# colored.
#
# This file adds a SECOND, ADDITIVE capability: when [code_highlight]
# mode = "html-doc", each fenced block is ALSO rendered host-side to a
# self-contained HTML document (lib/code_highlight.py, pygments'
# `HtmlFormatter(full=True, noclasses=True)` - every color inlined as CSS,
# no external stylesheet, no network fetch needed to view it) and sent via
# Telegram's `sendDocument`. Opened in the phone's browser, it shows REAL
# per-token colors on ANY device, no local highlighter needed - and unlike
# an image, the code stays selectable/copyable right there in the
# document. The v0.3.0 inline box in the message itself is UNCHANGED and
# UNAFFECTED either way - this file never touches or removes it, only
# adds a follow-up document.
#
# --- This file EXTENDS lib/format.sh's fenced-code handling, it does not
# --- replace or duplicate it ------------------------------------------------
# Fence detection reuses lib/format.sh's OWN regex (`$_FMT_FENCE_OPEN_RE`,
# defined there) - so this file and lib/format.sh can never disagree about
# what counts as a fenced block. lib/format.sh itself also reads ONE key
# back from this file's config section - `[code_highlight].myc_inline_lang`
# (default "rust") - to alias its OWN inline `language-mycelium` class to
# something Telegram's client highlighter actually recognizes (see
# `_fmt_render_code_block`'s header in lib/format.sh for the full
# rationale); that aliasing is unconditional (applies even with
# `[code_highlight] mode = "off"`), independent of everything below.
#
# --- The pipeline (per PAGE, called by tg-send.sh BEFORE format_message) --
# `_img_process_message <page>` walks <page> line-by-line (the same fence
# tokenizer shape as lib/format.sh's own), and for every CLOSED fenced
# block, when mode = "html-doc": renders it via lib/code_highlight.py and,
# on success, queues a sendDocument job (IMG_PENDING_* arrays, below). This
# function is PURELY ADDITIVE and READ-ONLY with respect to the message
# text - it never mutates or strips anything; the page's text flows into
# format_message() completely unchanged, exactly as it always has. Any
# render failure (dependency absent, over max_lines, a genuine error) is
# just a metric-logged no-op for that block - the v0.3.0 inline box already
# sent in the main message is entirely unaffected. An UNCLOSED fence at
# EOF (no closing ``` ever appeared) is never handled here either - see
# lib/format.sh's matching EOF fix; this file's tokenizer only ever acts on
# a fence it saw actually CLOSE.
#
# Sets, as globals (same "plain statement, not $(...)" convention
# format_message() documents - see lib/format.sh's header for why):
#   IMG_PENDING_DOCS[]        \  one entry per successfully-rendered
#   IMG_PENDING_LANGS[]        | fenced block, in source order (a
#   IMG_PENDING_CAPTIONS[]    /  caption may be an empty string)
#
# --- Config (relay.toml [code_highlight], all optional - see
# --- relay.toml.example) ---------------------------------------------------
#   mode          = "inline-only"  "off" (bypass this file entirely - not
#                                   even the fence scan runs) |
#                                   "inline-only" (the DEFAULT - the
#                                   v0.3.0 inline box above is already
#                                   good-enough/zero-tap; this file stays
#                                   a no-op) | "html-doc" (ALSO render +
#                                   send the highlighted HTML document per
#                                   fenced block). "off" and
#                                   "inline-only" are behaviourally
#                                   IDENTICAL today (both no-op here) -
#                                   kept as two distinct values so a user
#                                   can express "I don't want this feature
#                                   touched at all" vs. "I considered it,
#                                   the default inline box is enough".
#   theme         = "monokai"  A pygments style name; dark by default
#                               (monokai/dracula/... - any name
#                               pygments.styles.get_all_styles() knows; an
#                               unknown name degrades that ONE document to
#                               a skip, never a crash).
#   line_numbers  = false      Toggle a line-number gutter in the document.
#   max_lines     = 60         A fenced block over this many lines skips
#                               the document render entirely - NEVER an
#                               unbounded document. The v0.3.0 inline box
#                               is unaffected regardless (it has no line
#                               cap of its own).
#   keep_text     = "caption"  Whether the sendDocument call ALSO carries
#                               a `<pre>` caption (Telegram documents
#                               support HTML captions, up to 1024 chars,
#                               same as photos) - "caption" (the DEFAULT:
#                               a caption when the code fits; silently
#                               omitted, never truncated, when it
#                               doesn't - the v0.3.0 inline box in the
#                               main message already carries the full
#                               code either way) | "none" (no caption at
#                               all). There is no "companion" mode here
#                               (unlike an earlier draft of this
#                               feature): the v0.3.0 inline box ALREADY
#                               travels in the main message unconditionally
#                               now, so a separate companion text message
#                               would just duplicate it - keep_text only
#                               ever governs the DOCUMENT's own caption.
#   myc_inline_lang = "rust"   See lib/format.sh's `_fmt_render_code_block`
#                               header - NOT scoped to mode = "html-doc",
#                               applies whenever ANY myc/mycelium fence is
#                               rendered inline, unconditionally.
#
# --- Never-silent (G2) ------------------------------------------------------
# lib/code_highlight.py never raises; a SKIP (dependency absent, over
# max_lines, an actual render error) is logged via
# emit_metric("code_highlight", "fallback", ...) - the v0.3.0 inline box
# already sent in the main message means there is nothing to "fall back
# TO" here; a skipped document is simply not sent, never a dropped code
# block (the code was never solely represented by the document in the
# first place).
set -u

_IMG_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Same guard-before-source shape every lib/*.sh in this repo uses.
if ! declare -f cfg_get >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    [[ -f "$_IMG_LIB_DIR/relay-config.sh" ]] && source "$_IMG_LIB_DIR/relay-config.sh"
fi
declare -f cfg_get >/dev/null 2>&1 || cfg_get() { printf '%s' "$2"; }  # lib missing -> default-only shim
# shellcheck disable=SC1091
[[ -f "$_IMG_LIB_DIR/relay-common.sh" ]] && source "$_IMG_LIB_DIR/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }  # lib missing -> no-op shim
# This file EXTENDS lib/format.sh's fenced-code handling - it needs
# format.sh's shared fence regex (see this file's header). Guard the same
# way (format.sh carries no top-level mutable state, so re-sourcing it is
# always safe/idempotent - just redefines functions).
if ! declare -f _fmt_join_lines >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    [[ -f "$_IMG_LIB_DIR/format.sh" ]] && source "$_IMG_LIB_DIR/format.sh"
fi
# format.sh missing entirely (shouldn't happen) -> no fences can ever be
# detected, so _img_process_message degrades to a pure no-op further down
# (checked explicitly, never assumed).

# _img_available
#
# Probes python3 + pygments ONCE per process (cached in $_IMG_AVAILABLE) -
# avoids spawning a python3 subprocess per fenced block in a message with
# several. Returns 0 iff a document can actually be rendered right now.
# NOTE: unlike an earlier (PNG-based) draft of this feature, Pillow/a
# system font are NOT needed at all - `HtmlFormatter(noclasses=True)` is
# pure text generation.
_IMG_AVAILABLE_CHECKED=0
_IMG_AVAILABLE=1
_img_available() {
    if (( _IMG_AVAILABLE_CHECKED )); then
        return "$_IMG_AVAILABLE"
    fi
    _IMG_AVAILABLE_CHECKED=1
    if command -v python3 >/dev/null 2>&1 \
        && [[ -f "$_IMG_LIB_DIR/code_highlight.py" ]] \
        && python3 -c 'import pygments, pygments.formatters' >/dev/null 2>&1; then
        _IMG_AVAILABLE=0
    else
        _IMG_AVAILABLE=1
    fi
    return "$_IMG_AVAILABLE"
}

# _img_handle_fence <lang> <theme> <line_numbers> <max_lines> <keep_text> <body-array-name>
#
# Nameref for the body-lines-in array (same pattern as lib/format.sh's
# _fmt_flush_quote). Renders <lang>'s body via lib/code_highlight.py:
#   SUCCESS -> queues a sendDocument job in the IMG_PENDING_* globals.
#   SKIP    -> emits a fallback metric and returns - nothing queued. The
#              v0.3.0 inline box for this fence already went out (or will
#              go out) as part of the ordinary, UNTOUCHED main-message
#              pipeline - there is no text to reconstruct or restore here.
_img_handle_fence() {
    local lang="$1" theme="$2" line_numbers="$3" max_lines="$4" keep_text="$5"
    local -n _img_body="$6"

    local body
    body="$(_fmt_join_lines "${_img_body[@]}")"

    local doc
    doc="$(mktemp "${TMPDIR:-/tmp}/relay-code-doc-XXXXXX.html")"

    local ln_flag=()
    [[ "$line_numbers" == "true" ]] && ln_flag=(--line-numbers)

    local render_out
    render_out="$(printf '%s' "$body" | python3 "$_IMG_LIB_DIR/code_highlight.py" \
        "$lang" "$doc" "--theme=${theme}" "--max-lines=${max_lines}" \
        "${ln_flag[@]}" 2>/dev/null)"

    if [[ "$render_out" == DOC:* && -s "$doc" ]]; then
        IMG_PENDING_DOCS+=("$doc")
        IMG_PENDING_LANGS+=("$lang")

        local caption=""
        if [[ "$keep_text" == "caption" ]]; then
            local rendered_box
            rendered_box="$(_fmt_render_code_block "$lang" "$body")"
            # Telegram's document-caption cap is 1024 chars; never send a
            # truncated caption - the v0.3.0 inline box in the main
            # message already carries the FULL code either way, so simply
            # omitting an over-long caption here loses nothing.
            (( ${#rendered_box} <= 1024 )) && caption="$rendered_box"
        fi
        IMG_PENDING_CAPTIONS+=("$caption")

        emit_metric "code_highlight" "render" "lang=${lang:-plain} lines=${#_img_body[@]}"
    else
        local reason="${render_out#SKIP:}"
        [[ -z "$reason" ]] && reason="render unavailable or failed"
        emit_metric "code_highlight" "fallback" "lang=${lang:-plain} reason=${reason}"
        rm -f "$doc"
    fi
}

# _img_process_message <page-text>
#
# See this file's header for the full contract/globals. Purely READ-ONLY
# with respect to <page-text> - never mutates or returns a modified text,
# only populates the IMG_PENDING_* job queue. MUST be called as a plain
# statement (`_img_process_message "$PAGE"`), never
# `x="$(_img_process_message ...)"` - a command-substitution subshell would
# silently discard the globals it sets (same rationale as
# lib/format.sh's format_message()).
_img_process_message() {
    local input="$1"
    IMG_PENDING_DOCS=()
    IMG_PENDING_LANGS=()
    IMG_PENDING_CAPTIONS=()

    local mode
    mode="$(cfg_get '.code_highlight.mode' 'inline-only')"
    [[ "$mode" == "html-doc" ]] || return 0   # "off"/"inline-only"/anything else -> no-op

    declare -f _fmt_join_lines >/dev/null 2>&1 || return 0  # format.sh unavailable -> nothing to scan with
    [[ "$input" == *'```'* ]] || return 0                    # fast path: no fence marker at all

    if ! _img_available; then
        # The feature is unavailable at the WHOLE-MESSAGE level (pygments
        # missing, or python3 itself absent) - log it once here (rather
        # than silently doing nothing), since the per-fence "fallback"
        # metric in _img_handle_fence never fires in this path (the fence
        # loop below never runs). The v0.3.0 inline box for every fence in
        # this message is unaffected either way.
        emit_metric "code_highlight" "fallback" "pygments unavailable - message contained a fenced block"
        return 0
    fi

    local theme line_numbers max_lines keep_text
    theme="$(cfg_get '.code_highlight.theme' 'monokai')"
    line_numbers="$(cfg_get '.code_highlight.line_numbers' 'false')"
    [[ "$line_numbers" == "true" ]] || line_numbers="false"
    max_lines="$(cfg_get '.code_highlight.max_lines' 60)"
    [[ "$max_lines" =~ ^[0-9]+$ ]] || max_lines=60
    keep_text="$(cfg_get '.code_highlight.keep_text' 'caption')"
    case "$keep_text" in
        caption | none) ;;
        *) keep_text="caption" ;;
    esac

    local -a body_lines=()
    local in_code=0 lang=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        if (( in_code )); then
            if [[ "$line" == '```' ]]; then
                in_code=0
                _img_handle_fence "$lang" "$theme" "$line_numbers" "$max_lines" "$keep_text" body_lines
                body_lines=()
                lang=""
                continue
            fi
            body_lines+=("$line")
            continue
        fi

        if [[ "$line" =~ $_FMT_FENCE_OPEN_RE ]]; then
            in_code=1
            lang="$(printf '%s' "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
            body_lines=()
            continue
        fi
    done <<< "$input"

    # An unclosed fence at EOF is deliberately IGNORED here (never handed
    # to _img_handle_fence) - see this file's header and lib/format.sh's
    # matching EOF fix: an unclosed fence isn't treated as a real code
    # block by the main-message pipeline either, so this file mirrors
    # that and renders no document for it.
}

# _img_post_send_document <bot_token> <chat_id> <doc_path> <filename> <caption_html>
#
# One sendDocument multipart POST (same "trust Telegram's ok:true, not
# just curl's exit code" contract as tg-send.sh's own
# _tg_post_send_message). <caption_html> may be empty (no caption param
# sent at all). <filename> sets the uploaded document's display name
# (curl's `;filename=` modifier - the temp path itself is a random
# mktemp name, not a useful download name on its own).
_img_post_send_document() {
    local bot_token="$1" chat_id="$2" doc_path="$3" filename="$4" caption="$5" resp
    if [[ -n "$caption" ]]; then
        resp="$(curl -s -m 30 -X POST "https://api.telegram.org/bot${bot_token}/sendDocument" \
            -F "chat_id=${chat_id}" \
            -F "document=@${doc_path};filename=${filename}" \
            -F "caption=${caption}" \
            -F "parse_mode=HTML" \
            2>/dev/null)"
    else
        resp="$(curl -s -m 30 -X POST "https://api.telegram.org/bot${bot_token}/sendDocument" \
            -F "chat_id=${chat_id}" \
            -F "document=@${doc_path};filename=${filename}" \
            2>/dev/null)"
    fi
    [[ "$resp" == *'"ok":true'* ]]
}

# img_send_pending <bot_token> <chat_id>
#
# Sends every job queued by the last _img_process_message call (in source
# order): the highlighted HTML document via sendDocument (with its
# caption, if any). Always cleans up its own temp files. Best-effort - an
# individual document-send failure is metric-logged and does not abort the
# rest of the queue (one failure must never block the other, independent
# documents in the same message).
img_send_pending() {
    local bot_token="$1" chat_id="$2"
    local n=${#IMG_PENDING_DOCS[@]}
    (( n == 0 )) && return 0

    local i doc caption lang filename
    for (( i = 0; i < n; i++ )); do
        doc="${IMG_PENDING_DOCS[$i]}"
        caption="${IMG_PENDING_CAPTIONS[$i]}"
        lang="${IMG_PENDING_LANGS[$i]}"
        filename="snippet.${lang:-txt}.html"

        if _img_post_send_document "$bot_token" "$chat_id" "$doc" "$filename" "$caption"; then
            emit_metric "code_highlight" "sent" "lang=${lang:-plain}"
        else
            emit_metric "code_highlight" "send_fail" "lang=${lang:-plain} sendDocument failed"
        fi
        rm -f "$doc"
    done
}
