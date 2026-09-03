"""Parse CSS before filtering; keep canonical local rules in the JSON model.

tinycss2 decodes escaped identifiers and nested functions before the allowlist is
applied. It is a syntax parser, so this module supplies the actual security policy.
No fetching functions, at-rules, custom properties or viewport positioning.
"""

import re

import tinycss2

PROPERTIES = frozenset("""
color background background-color background-image background-size background-position
background-repeat opacity font font-family font-size font-style font-weight font-variant
line-height letter-spacing word-spacing text-align text-decoration text-transform
text-shadow text-indent white-space word-break overflow-wrap vertical-align
display visibility width height min-width max-width min-height max-height box-sizing
margin margin-top margin-right margin-bottom margin-left padding padding-top padding-right
padding-bottom padding-left border border-width border-style border-color border-radius
border-top border-right border-bottom border-left border-collapse border-spacing box-shadow
outline outline-width outline-style outline-color outline-offset overflow overflow-x overflow-y
position top right bottom left z-index transform transform-origin
flex flex-grow flex-shrink flex-basis flex-direction flex-wrap align-items align-self align-content
justify-content justify-items justify-self gap row-gap column-gap order
grid grid-template-columns grid-template-rows grid-template-areas grid-area grid-column grid-row
grid-auto-flow grid-auto-columns grid-auto-rows list-style-type list-style-position
object-fit object-position aspect-ratio fill stroke stroke-width stroke-linecap stroke-linejoin
stroke-dasharray stroke-dashoffset fill-opacity stroke-opacity
""".split())
FUNCTIONS = frozenset("""
rgb rgba hsl hsla hwb lab lch oklab oklch color color-mix
calc min max clamp linear-gradient radial-gradient conic-gradient
repeating-linear-gradient repeating-radial-gradient translate translatex translatey
scale scalex scaley rotate skew skewx skewy matrix repeat minmax fit-content var
""".split())
_OLD_SCOPE = re.compile(r"^\.ld-html-[\w-]+\s+")


def _safe_tokens(tokens, depth=0):
    if depth > 12:
        return False
    for token in tokens:
        if token.type in {"url", "error", "at-keyword", "{} block"}:
            return False
        if token.type == "function":
            if token.lower_name not in FUNCTIONS or not _safe_tokens(token.arguments, depth + 1):
                return False
        if token.type in {"[] block", "() block"} and not _safe_tokens(token.content, depth + 1):
            return False
    return True


def clean_declarations(value):
    result = []
    dropped = False
    for item in tinycss2.parse_declaration_list(value, skip_comments=True, skip_whitespace=True):
        if item.type != "declaration" or item.lower_name not in PROPERTIES or not _safe_tokens(item.value):
            dropped = True
            continue
        body = tinycss2.serialize(item.value).strip()
        if item.lower_name == "position" and body.lower() not in {"relative", "absolute", "static"}:
            dropped = True
            continue
        # A style element must never contain an HTML closing tag, even offline.
        if not body or "<" in body or ">" in body:
            dropped = True
            continue
        result.append(item.lower_name + ":" + body + (" !important" if item.important else ""))
    return ";".join(result), dropped


def _selectors(tokens):
    groups = [[]]
    for token in tokens:
        if token.type == "literal" and token.value == ",":
            groups.append([])
        elif token.type != "comment":
            groups[-1].append(token)
    result = []
    for group in groups:
        text = tinycss2.serialize(group).strip()
        # Migrate any number of preliminary E0 prefixes before assigning final IDs.
        while _OLD_SCOPE.match(text):
            text = _OLD_SCOPE.sub("", text, count=1)
        if not text or any(c in text for c in "{}@\\<") or ":host" in text or ":root" in text:
            continue
        # No nesting or leading sibling/combinator may escape the block boundary.
        if "&" in text or text[0] in ">+~":
            continue
        result.append(text)
    return result


def clean_rules(css, *, scope=None):
    result = []
    dropped = False
    at_rules = False
    for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if rule.type != "qualified-rule":
            dropped = True
            at_rules |= rule.type == "at-rule"
            continue
        selectors = _selectors(rule.prelude)
        body, bad = clean_declarations(rule.content)
        dropped |= bad or not selectors
        if not body or not selectors:
            continue
        if scope:
            selectors = [scope + " " + selector for selector in selectors]
        result.append(", ".join(selectors) + "{" + body + "}")
    return "\n".join(result), dropped, at_rules
