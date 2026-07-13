#!/bin/bash
# lib/format.sh - Structured-formatting layer: turns a plain-text relay
# message into a phone-readable Telegram HTML message (dynamic soft-wrap,
# bolded section headers, code boxes, quotes, light emphasis) instead of a
# wall of text. Sourced by tg-send.sh (never executed directly - same
# convention as lib/tts.sh/lib/relay-common.sh: no shebang execution path).
#
# Telegram has no true font sizes - this uses the formatting Telegram
# actually supports via `parse_mode=HTML` (far safer than MarkdownV2: only
# `< > &` ever need escaping, no ~18-character escape table). See
# https://core.telegram.org/bots/api#html-style for the supported tag set;
# this file only ever emits <b> <i> <code> <pre> <blockquote[ expandable]>,
# all of which Telegram's Bot API HTML mode accepts.
#
# --- Input-markup convention (what a message's PLAIN TEXT can use to get
# --- structure - see README.md/docs/USAGE.md for the documented, canonical
# --- version of this list) ---------------------------------------------
#   ## Header               -> <b>Header</b>, with a blank line above.
#   EMOJI SHORT-CAPS LINE   -> <b>...</b> (a line starting with a leading
#                              emoji/symbol, then EITHER an all-caps phrase
#                              OR a Title-Case phrase, <=60 chars) - e.g.
#                              "✅ BUILD FINISHED" or "🚀 Deploy Started".
#                              A leading-emoji sentence with any lowercase,
#                              non-title-case word is NOT a header (e.g.
#                              "✅ build finished — 2 issues found" stays
#                              plain prose) - conservative by design, a
#                              false negative is always safe.
#   ```lang ... ```          -> a Telegram code box: <pre><code
#                              class="language-lang">...</code></pre>.
#                              Content is NEVER reflowed/wrapped/marked up -
#                              only HTML-escaped, byte-for-byte verbatim.
#                              `myc`/`mycelium` are first-class tags (both
#                              map to language-mycelium); an unrecognized
#                              tag still boxes the code, just without a
#                              language class (plain <pre>).
#   `inline code`            -> <code>...</code> (monospace, no reflow).
#   > quoted line(s)         -> <blockquote>...</blockquote> (consecutive
#                              "> " lines are grouped into ONE blockquote);
#                              a long quote (>3 lines or >200 chars) becomes
#                              an EXPANDABLE blockquote (Telegram feature).
#   *emphasis* / _emphasis_  -> <i>...</i>. Word-boundary-guarded, so a
#                              snake_case identifier like `my_var_name`
#                              (outside backticks) is never mistaken for
#                              emphasis.
#
# --- Dynamic soft-wrap ----------------------------------------------------
# A prose line longer than [format].wrap_width (default 50 - phone-width)
# is wrapped at WORD boundaries. Never breaks a word, a URL, or the
# CONTENTS of an inline `code span` (protected as one atomic token even if
# it contains spaces); an unbreakable token longer than wrap_width is kept
# whole on its own line rather than torn mid-token. A line already <=
# wrap_width is returned untouched. Fenced code-block interiors are never
# wrapped (see above - verbatim always).
#
# --- Config (relay.toml [format], all optional) - see relay.toml.example -
#   enabled     = true    Master switch. false -> every call returns the
#                          INPUT UNCHANGED and parse_mode "" - today's
#                          exact plain-text behavior, byte-for-byte.
#   parse_mode  = "HTML"  "HTML" (default, implemented) | "MarkdownV2"
#                          (accepted but NOT YET RENDERED - falls back to
#                          plain text, logged via emit_metric; MarkdownV2's
#                          escaping rules are a distinct, larger surface -
#                          Declared future work, never silently
#                          mis-rendered) | "none" (same as enabled=false).
#   wrap_width  = 50      Soft-wrap width; < 10 is rejected -> 50.
#   headers     = true    Toggle the header->bold rule.
#   code_spans  = true    Toggle backtick (inline + fenced) -> <code>/<pre>.
#   blockquotes = true    Toggle "> " -> <blockquote>.
#   soft_wrap   = true    Toggle the word-boundary wrap pass.
#
# --- Never-silent safety net (G2) -----------------------------------------
# format_message() self-checks the HTML it produces (open/close tag
# balance) before returning it. If that check ever fails (should not
# happen given only well-formed constructs are emitted, but this file
# never trusts its own output blindly), it falls back to the ESCAPED PLAIN
# TEXT of the original input (still valid to send with parse_mode=HTML -
# just no markup) and logs the downgrade via emit_metric("format",
# "fallback", ...) - a message is NEVER dropped and NEVER sent with
# malformed markup. tg-send.sh adds a second safety net on top: if
# Telegram's API itself rejects an HTML send (400 - a bug here, or a
# Telegram HTML-parser edge case this file's balance check didn't catch),
# it retries ONCE as plain text and logs that fallback too.
set -u

_FMT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Same guard-before-source shape as lib/tts.sh (relay-config.sh carries
# top-level state - re-sourcing it would reset RELAY_CONFIG_JSON and
# silently drop whatever the caller already loaded).
if ! declare -f cfg_get >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    [[ -f "$_FMT_LIB_DIR/relay-config.sh" ]] && source "$_FMT_LIB_DIR/relay-config.sh"
fi
declare -f cfg_get >/dev/null 2>&1 || cfg_get() { printf '%s' "$2"; }  # lib missing -> default-only shim
# shellcheck disable=SC1091
[[ -f "$_FMT_LIB_DIR/relay-common.sh" ]] && source "$_FMT_LIB_DIR/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }  # lib missing -> no-op shim

# The ONE definition of "what counts as a fence-open line" - shared with
# lib/code_highlight.sh (v0.5.0 host-highlighted code documents), so the
# two files can never diverge on what they each consider a fenced code
# block. lib/code_highlight.sh extends this file's fenced-code handling
# (ADDITIONALLY rendering a highlighted HTML document alongside - never
# instead of - the <pre><code> box below); it reuses this exact regex
# rather than re-detecting fences its own, possibly-drifting way.
_FMT_FENCE_OPEN_RE='^```([A-Za-z0-9_+.-]*)[[:space:]]*$'

# _fmt_escape_html <text>
#
# The ONLY three characters Telegram's HTML parse_mode requires escaped.
# Order matters: & first, so the entities this function itself inserts are
# never double-escaped. The `&` in each replacement is backslash-escaped
# DELIBERATELY - bash >= 5.2's `patsub_replacement` shopt (on by default)
# treats an UNQUOTED `&` in a `${var//pat/repl}` replacement as "the
# matched text" (sed-style); without the `\&` here, `>` would silently
# become the four bytes "&gt;" -> ">gt;" corruption instead of an escape.
_fmt_escape_html() {
    local s="$1"
    s="${s//&/\&amp;}"
    s="${s//</\&lt;}"
    s="${s//>/\&gt;}"
    printf '%s' "$s"
}

# _fmt_known_lang <lowercased-tag>
#
# Prints the normalized language tag for a fenced code block's `language-*`
# class, or returns 1 for an unrecognized tag (caller then boxes the code
# WITHOUT a class - still a code box, just no language hint). `myc` and
# `mycelium` are first-class Mycelium tags - both normalize to
# "mycelium", the project's own language.
_fmt_known_lang() {
    local lang="$1"
    case "$lang" in
        myc|mycelium)
            printf 'mycelium'; return 0 ;;
        rust|python|py|bash|sh|shell|zsh|json|yaml|yml|toml|c|cpp|c++|go|golang| \
        js|javascript|ts|typescript|jsx|tsx|java|kotlin|kt|scala|swift|ruby|rb| \
        php|sql|diff|patch|html|xml|css|scss|less|markdown|md|text|plain|plaintext| \
        dockerfile|docker|makefile|make|ini|cfg|conf|graphql|proto|elixir|erlang| \
        haskell|clojure|julia|dart|nim|zig|vim|powershell|ps1|r|lua|perl|hcl|terraform)
            printf '%s' "$lang"; return 0 ;;
        *)
            return 1 ;;
    esac
}

# _fmt_render_code_block <raw-lang-tag> <verbatim-body>
#
# <verbatim-body> is HTML-escaped (the ONLY transform ever applied to a
# fenced block's contents - no reflow, no wrap, no markup) and boxed as
# <pre><code class="language-X">...</code></pre>, or a plain <pre>...</pre>
# when the tag is empty/unrecognized (still a code box, per DN: "unknown
# language still boxes the code").
#
# Mycelium inline-highlighting alias (relay.toml [code_highlight].
# myc_inline_lang, default "rust"): Telegram's CLIENT-side highlighter (the
# one that lights up THIS inline box, independent of lib/code_highlight.sh's
# host-rendered HTML-document path below) doesn't recognize "mycelium" - an
# in-development language it has never heard of - so a bare
# `language-mycelium` class renders as plain, uncolored monospace on every
# client. Telegram's built-in RUST highlighter, by contrast, DOES color
# Mycelium reasonably well - fn/let/match/impl/strings/comments/generic
# types all align (Mycelium is Rust-family syntax); only Mycelium-unique
# keywords (nodule/phylum/swap/fuse/hypha/colony) render as plain
# identifiers under the rust grammar - harmless, never miscolored as an
# actively wrong token class. So a myc/mycelium fence's INLINE box emits
# `language-<myc_inline_lang>` (default "rust") instead of the literal
# `language-mycelium` - zero-infra, good-enough client-side color on
# every phone, today. This is the "good-enough, zero-infra" tier; the
# host-rendered HTML document (lib/code_highlight.sh, [code_highlight]
# mode="html-doc") is the "exact" tier - it uses the REAL MyceliumLexer,
# coloring the unique keywords correctly too. Set
# Set `myc_inline_lang = "mycelium"` to opt back into the literal,
# uncolored-on-most-clients `language-mycelium` tag. (Not `""` - an
# explicit empty-string TOML value is indistinguishable from "unset" to
# `cfg_get`'s own `[[ -n "$val" ]]` check, a pre-existing, repo-wide
# limitation of that helper - see lib/relay-config.sh - so it falls
# through to this key's own default, "rust", same as leaving it unset.)
_fmt_render_code_block() {
    local lang="$1" body="$2" esc norm
    esc="$(_fmt_escape_html "$body")"
    lang="$(printf '%s' "$lang" | tr '[:upper:]' '[:lower:]')"
    if [[ -n "$lang" ]] && norm="$(_fmt_known_lang "$lang")"; then
        if [[ "$norm" == "mycelium" ]]; then
            local myc_alias myc_alias_norm
            myc_alias="$(cfg_get '.code_highlight.myc_inline_lang' 'rust')"
            myc_alias="$(printf '%s' "$myc_alias" | tr '[:upper:]' '[:lower:]')"
            # Fail CLOSED: an arbitrary config value must not land straight
            # in `class="language-%s"` - validate it against the same
            # allowlist real fence tags go through (_fmt_known_lang) before
            # using it as the alias. An unrecognized/malformed config value
            # falls back to the safe default already established above
            # ("mycelium" itself), never passed through unchecked.
            if [[ -n "$myc_alias" ]] && myc_alias_norm="$(_fmt_known_lang "$myc_alias")"; then
                norm="$myc_alias_norm"
            fi
        fi
        printf '<pre><code class="language-%s">%s</code></pre>' "$norm" "$esc"
    else
        printf '<pre>%s</pre>' "$esc"
    fi
}

# _fmt_join_lines <line1> [<line2> ...] - join with real newlines.
_fmt_join_lines() {
    local IFS=$'\n'
    printf '%s' "$*"
}

# _fmt_is_header_line <line>
#
# See this file's header comment for the documented convention. Two ways
# in: an explicit "## " prefix, or a leading-emoji short ALL-CAPS/Title-Case
# phrase. Conservative: anything ambiguous returns 1 (stays plain prose) -
# a missed header is harmless, a wrongly-bolded sentence would not be.
_fmt_is_header_line() {
    local line="$1"
    [[ "$line" == "## "* ]] && return 0

    # Requires a genuine leading run of non-ASCII-printable bytes (an
    # emoji/symbol is virtually always outside the space(0x20)-tilde(0x7e)
    # printable-ASCII range) followed by a space - a bare all-caps line
    # with NO leading emoji is deliberately NOT treated as a header.
    [[ "$line" =~ ^([^\ -~]+)\ (.*)$ ]] || return 1
    local rest="${BASH_REMATCH[2]}"
    (( ${#rest} == 0 || ${#rest} > 60 )) && return 1

    # ALL-CAPS short phrase: no lowercase ASCII letters, at least one upper.
    if [[ ! "$rest" =~ [a-z] ]] && [[ "$rest" =~ [A-Z] ]]; then
        return 0
    fi

    # Title-Case short phrase: every whitespace-delimited word starts with
    # an uppercase ASCII letter.
    local word
    for word in $rest; do
        [[ "$word" =~ ^[A-Z] ]] || return 1
    done
    return 0
}

# _fmt_render_emphasis <text-with-no-code-spans>
#
# *word* / _word_ -> <i>escaped</i>; everything else HTML-escaped.
# Word-boundary-guarded (the char immediately outside each marker must not
# be alphanumeric) so `my_var_name` (a snake_case identifier, NOT
# emphasis) is left alone - the classic Markdown-in-prose false positive.
#
# A left-to-right character scan, DELIBERATELY not a single anchored
# regex: a regex of the shape `^([^*_]*)(\*(...)\*|_(..)_)(.*)$` cannot
# find a valid `*pair*` that occurs AFTER an earlier unpaired `_` (or vice
# versa), because its `[^*_]*` "before" group excludes BOTH marker
# characters and so can never skip past the unpaired one to reach a real
# pair further on (e.g. "my_var_name has a *real* word" - one lone `_`
# inside the identifier would otherwise blind the matcher to the `*real*`
# that follows it). The char scan has no such blind spot: an
# unpaired/boundary-rejected marker is emitted literally and scanning
# simply resumes at the very next character.
_fmt_render_emphasis() {
    local s="$1" out="" run="" i=0 ch
    local len=${#s}
    while (( i < len )); do
        ch="${s:i:1}"
        if [[ "$ch" == '*' || "$ch" == '_' ]]; then
            local close=-1 j
            for (( j = i + 1; j < len; j++ )); do
                if [[ "${s:j:1}" == "$ch" ]]; then close=$j; break; fi
            done
            if (( close > i + 1 )); then
                local inner="${s:i+1:close-i-1}"
                # NOTE: `${s:i-1:1}` when i==0 is NOT "no char before start"
                # - bash treats a negative computed offset as relative to
                # the END of the string, silently returning the LAST
                # character instead. Guard explicitly.
                local prev_char="" next_char="${s:close+1:1}"
                (( i > 0 )) && prev_char="${s:i-1:1}"
                local boundary_ok=1
                [[ -n "$prev_char" && "$prev_char" =~ [A-Za-z0-9] ]] && boundary_ok=0
                [[ -n "$next_char" && "$next_char" =~ [A-Za-z0-9] ]] && boundary_ok=0
                if (( boundary_ok )); then
                    out+="$(_fmt_escape_html "$run")"; run=""
                    out+="<i>$(_fmt_escape_html "$inner")</i>"
                    i=$(( close + 1 ))
                    continue
                fi
            fi
        fi
        run+="$ch"
        i=$(( i + 1 ))
    done
    out+="$(_fmt_escape_html "$run")"
    printf '%s' "$out"
}

# _fmt_render_inline <one-line-of-text>
#
# A left-to-right character scan (same rationale as _fmt_render_emphasis
# above - a single anchored regex for `` `code` `` has the identical blind
# spot: an earlier unpaired backtick would otherwise mis-pair with a LATER
# span's opening backtick instead of correctly falling through as a
# literal character). `` `code span` `` -> <code>escaped</code> (never
# reflowed/marked-up inside - highest priority, matched first); everything
# between/outside code spans is handed to _fmt_render_emphasis, which
# itself escapes. An unmatched stray backtick is just a literal character
# in the final HTML - safe, backtick has no HTML meaning.
_fmt_render_inline() {
    local s="$1" out="" run="" i=0 ch
    local len=${#s}
    local code_on="${FMT_CODE_SPANS:-true}"
    while (( i < len )); do
        ch="${s:i:1}"
        if [[ "$code_on" == "true" && "$ch" == '`' ]]; then
            local close=-1 j
            for (( j = i + 1; j < len; j++ )); do
                if [[ "${s:j:1}" == '`' ]]; then close=$j; break; fi
            done
            if (( close > i + 1 )); then
                out+="$(_fmt_render_emphasis "$run")"; run=""
                out+="<code>$(_fmt_escape_html "${s:i+1:close-i-1}")</code>"
                i=$(( close + 1 ))
                continue
            fi
        fi
        run+="$ch"
        i=$(( i + 1 ))
    done
    out+="$(_fmt_render_emphasis "$run")"
    printf '%s' "$out"
}

# _fmt_wrap_line <line> <width> - prints one or more wrapped lines.
#
# Word-boundary wrap only: never breaks a word, a URL (URLs never contain
# a literal space, so they're already an atomic "word"), or the inside of
# an inline `code span` (spaces inside a matched backtick pair are
# protected before splitting, restored after). A single token longer than
# <width> is kept whole on its own output line rather than torn. A line
# already <= <width> is printed unchanged (single line, no trailing
# blank/marker).
_fmt_wrap_line() {
    local line="$1" width="$2"
    if (( ${#line} <= width )); then
        printf '%s\n' "$line"
        return
    fi

    local sp=$'\x1f'   # placeholder for a space PROTECTED inside `code`
    local protected="" in_span=0 i len=${#line} ch
    for (( i = 0; i < len; i++ )); do
        ch="${line:i:1}"
        if [[ "$ch" == '`' ]]; then
            in_span=$(( 1 - in_span ))
            protected+="$ch"
        elif [[ "$ch" == ' ' && $in_span -eq 1 ]]; then
            protected+="$sp"
        else
            protected+="$ch"
        fi
    done

    local -a words=()
    read -ra words <<< "$protected"

    local cur="" w real
    for w in "${words[@]}"; do
        real="${w//$sp/ }"
        if [[ -z "$cur" ]]; then
            cur="$real"
        elif (( ${#cur} + 1 + ${#real} <= width )); then
            cur+=" $real"
        else
            printf '%s\n' "$cur"
            cur="$real"
        fi
    done
    [[ -n "$cur" ]] && printf '%s\n' "$cur"
}

# _fmt_html_balanced <rendered-html>
#
# Defensive self-check, not a general HTML validator: counts open/close
# occurrences for exactly the tags this file ever emits. This file's own
# generation logic should make an imbalance impossible, but format_message
# never trusts that blindly - see this file's header ("never-silent safety
# net").
_fmt_html_balanced() {
    local s="$1"
    local bo bc io ic co cc po pc qo qc
    bo=$(grep -o '<b>' <<<"$s" 2>/dev/null | wc -l);        bc=$(grep -o '</b>' <<<"$s" 2>/dev/null | wc -l)
    io=$(grep -o '<i>' <<<"$s" 2>/dev/null | wc -l);        ic=$(grep -o '</i>' <<<"$s" 2>/dev/null | wc -l)
    co=$(grep -oE '<code( class="language-[a-z0-9+.]+")?>' <<<"$s" 2>/dev/null | wc -l)
    cc=$(grep -o '</code>' <<<"$s" 2>/dev/null | wc -l)
    po=$(grep -o '<pre>' <<<"$s" 2>/dev/null | wc -l);      pc=$(grep -o '</pre>' <<<"$s" 2>/dev/null | wc -l)
    qo=$(grep -oE '<blockquote( expandable)?>' <<<"$s" 2>/dev/null | wc -l)
    qc=$(grep -o '</blockquote>' <<<"$s" 2>/dev/null | wc -l)
    [[ "$bo" -eq "$bc" && "$io" -eq "$ic" && "$co" -eq "$cc" \
        && "$po" -eq "$pc" && "$qo" -eq "$qc" ]]
}

# _fmt_render <input-text> <wrap-width>
#
# The line-oriented state machine: fenced code blocks (verbatim) and
# consecutive "> " lines (grouped into one blockquote) are pulled out
# whole; everything else is header-detected, inline-rendered, and
# word-wrapped. Reads FMT_HEADERS/FMT_CODE_SPANS/FMT_BLOCKQUOTES/
# FMT_SOFT_WRAP (set by format_message from relay.toml before calling).
_fmt_render() {
    local input="$1" width="$2"
    local -a out=()
    local -a qbuf=()
    local have_out=0 last_blank=1
    local in_code=0 code_lang=""
    local -a code_lines=()

    while IFS= read -r line || [[ -n "$line" ]]; do
        if (( in_code )); then
            if [[ "$line" == '```' ]]; then
                in_code=0
                local body; body="$(_fmt_join_lines "${code_lines[@]}")"
                if [[ "${FMT_CODE_SPANS:-true}" == "true" ]]; then
                    out+=("$(_fmt_render_code_block "$code_lang" "$body")")
                else
                    out+=("$(_fmt_escape_html "$body")")
                fi
                code_lines=()
                code_lang=""
                have_out=1
                last_blank=0
                continue
            fi
            code_lines+=("$line")
            continue
        fi

        if [[ "${FMT_CODE_SPANS:-true}" == "true" \
            && "$line" =~ $_FMT_FENCE_OPEN_RE ]]; then
            if (( ${#qbuf[@]} > 0 )); then
                _fmt_flush_quote qbuf out have_out last_blank
            fi
            if (( have_out )) && (( ! last_blank )); then
                out+=("")
                last_blank=1
            fi
            in_code=1
            code_lang="${BASH_REMATCH[1]}"
            code_lines=()
            continue
        fi

        if [[ "${FMT_BLOCKQUOTES:-true}" == "true" && ( "$line" == "> "* || "$line" == ">" ) ]]; then
            if (( ${#qbuf[@]} == 0 )) && (( have_out )) && (( ! last_blank )); then
                out+=("")
                last_blank=1
            fi
            local qline="${line#>}"
            qline="${qline# }"
            qbuf+=("$qline")
            continue
        elif (( ${#qbuf[@]} > 0 )); then
            _fmt_flush_quote qbuf out have_out last_blank
        fi

        if [[ -z "$line" ]]; then
            if (( have_out )) && (( ! last_blank )); then
                out+=("")
                last_blank=1
            fi
            continue
        fi

        if [[ "${FMT_HEADERS:-true}" == "true" ]] && _fmt_is_header_line "$line"; then
            local text="$line"
            [[ "$text" == "## "* ]] && text="${text#\#\# }"
            if (( have_out )) && (( ! last_blank )); then
                out+=("")
            fi
            out+=("<b>$(_fmt_render_inline "$text")</b>")
            have_out=1
            last_blank=0
            continue
        fi

        if [[ "${FMT_SOFT_WRAP:-true}" == "true" ]]; then
            local wline
            while IFS= read -r wline; do
                out+=("$(_fmt_render_inline "$wline")")
            done < <(_fmt_wrap_line "$line" "$width")
        else
            out+=("$(_fmt_render_inline "$line")")
        fi
        have_out=1
        last_blank=0
    done <<< "$input"

    (( ${#qbuf[@]} > 0 )) && _fmt_flush_quote qbuf out have_out last_blank

    # An unterminated fence at EOF - no closing ``` ever appeared - is very
    # likely NOT a real fence (a stray ``` later in an otherwise-plain
    # message, or a closing marker lost to truncation/pagination), so it is
    # NEVER boxed as <pre>...</pre> (previously: it was - which produced a
    # stray, ugly, sometimes entirely EMPTY <pre></pre> for a bare trailing
    # ``` with no body). Never drop the content either way: the opening
    # marker line plus any collected body lines are emitted as literal,
    # escaped TEXT instead - visible, harmless, honest about what was
    # actually written (found in the #12 PR review).
    if (( in_code )); then
        out+=("$(_fmt_escape_html "\`\`\`${code_lang}")")
        local cl
        for cl in "${code_lines[@]}"; do
            out+=("$(_fmt_escape_html "$cl")")
        done
    fi

    _fmt_join_lines "${out[@]}"
}

# _fmt_flush_quote <qbuf-array-name> <out-array-name> <have_out-var-name> <last_blank-var-name>
#
# Namerefs so _fmt_render's local arrays/flags can be mutated from here
# without duplicating this logic at both of its call sites.
_fmt_flush_quote() {
    local -n _qb="$1" _ob="$2"
    local -n _ho="$3" _lb="$4"
    (( ${#_qb[@]} == 0 )) && return

    if [[ "${FMT_BLOCKQUOTES:-true}" == "true" ]]; then
        local joined="" i n=${#_qb[@]}
        for (( i = 0; i < n; i++ )); do
            joined+="${_qb[$i]}"
            (( i < n - 1 )) && joined+=$'\n'
        done
        # _fmt_render_inline (not a bare escape): `code`/*emphasis* inside a
        # quote render the same as everywhere else - a multi-line joined
        # string is safe input, embedded newlines are just literal
        # characters to its char-scan (no per-line splitting needed).
        local esc; esc="$(_fmt_render_inline "$joined")"
        local attr=""
        { (( n > 3 )) || (( ${#joined} > 200 )); } && attr=" expandable"
        _ob+=("<blockquote${attr}>${esc}</blockquote>")
    else
        local q
        for q in "${_qb[@]}"; do
            _ob+=("&gt; $(_fmt_escape_html "$q")")
        done
    fi
    _qb=()
    _ho=1
    _lb=0
}

# format_message <text>
#
# Sets two globals - FMT_TEXT (the message ready to send) and
# FMT_PARSE_MODE (the parse_mode tg-send.sh's curl call should use, ""
# meaning no parse_mode param at all - Telegram's plain-text default).
#
# DELIBERATELY NOT "prints to stdout, capture with $(...)": command
# substitution runs its command in a SUBSHELL, so a global this function
# set inside a `$(...)` call would vanish the instant the subshell exits -
# the caller would only ever see FMT_PARSE_MODE up to date with the LAST
# time format_message ran as a plain (non-substituted) statement, one call
# behind. Callers MUST invoke this as a plain statement -
# `format_message "$text"` - then read $FMT_TEXT/$FMT_PARSE_MODE
# afterward, never `x="$(format_message "$text")"`.
#
# [format].enabled=false OR parse_mode="none" -> FMT_TEXT=<text> UNCHANGED
# and FMT_PARSE_MODE="" - byte-for-byte identical to pre-format-layer
# behavior. This is the backward-compat guarantee; everything else in this
# file is additive on top of it.
format_message() {
    local input="$1"
    FMT_TEXT="$input"
    FMT_PARSE_MODE=""

    local enabled parse_mode
    enabled="$(cfg_get '.format.enabled' 'true')"
    parse_mode="$(cfg_get '.format.parse_mode' 'HTML')"

    if [[ "$enabled" != "true" || "$parse_mode" == "none" ]]; then
        return 0
    fi

    if [[ "$parse_mode" == "MarkdownV2" ]]; then
        # Declared future work (see this file's header) - never silently
        # mis-render with the wrong escaping rules.
        emit_metric "format" "fallback" "parse_mode=MarkdownV2 not yet implemented - sent as plain text"
        return 0
    fi
    parse_mode="HTML"  # anything else unrecognized also defaults to HTML

    local wrap_width
    wrap_width="$(cfg_get '.format.wrap_width' 50)"
    [[ "$wrap_width" =~ ^[0-9]+$ && "$wrap_width" -ge 10 ]] || wrap_width=50

    FMT_HEADERS="$(cfg_get '.format.headers' 'true')"
    FMT_CODE_SPANS="$(cfg_get '.format.code_spans' 'true')"
    FMT_BLOCKQUOTES="$(cfg_get '.format.blockquotes' 'true')"
    FMT_SOFT_WRAP="$(cfg_get '.format.soft_wrap' 'true')"

    local rendered
    rendered="$(_fmt_render "$input" "$wrap_width")"

    # shellcheck disable=SC2034  # consumed by sourcing scripts (tg-send.sh)
    if _fmt_html_balanced "$rendered"; then
        FMT_TEXT="$rendered"
        FMT_PARSE_MODE="$parse_mode"
    else
        emit_metric "format" "fallback" "rendered HTML failed the balance check - sent as escaped plain text"
        FMT_TEXT="$(_fmt_escape_html "$input")"
        FMT_PARSE_MODE="$parse_mode"
    fi
}
