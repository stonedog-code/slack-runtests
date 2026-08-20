"""Parse the slash-command argument string.

Slack hands you the whole argument string as one field, `text`. The obvious
thing to do is split on whitespace and read positionally:

    parts = text.split()
    product = parts[1]

That works, and it stops working the moment there is a fourth argument. Nobody
remembers whether the server or the product comes first, an optional argument
cannot be added without changing the meaning of every invocation already in
people's muscle memory, and `parts[1]` silently accepts anything at all —
including `../../etc`.

Flags fix all three, and `argparse`'s `choices=` gives an allowlist and the help
text from the same line.
"""

from __future__ import annotations

import argparse
import re
import shlex

#: Allowlists. These are the reason `tests/{product}` cannot be talked into
#: `tests/../../anything` — the value is checked against a fixed set before it
#: is ever interpolated into a path.
PRODUCTS = ("webapp", "billing", "catalog")

#: `prod` is deliberately absent. If a production run genuinely must exist, put
#: it behind a GitHub Actions environment with required reviewers, so the
#: approval happens somewhere other than a chat box.
SERVERS = ("local", "dev", "staging")

ACTIONS = ("run", "results")

#: -k and -m are pytest expressions. They cannot reach a shell from here — the
#: runner builds argv as a list — but in V2 they travel through a GitHub Actions
#: input, and THAT can. Constrain them at the door; the workflow's `env:` mapping
#: is the second lock on the same door and both are kept.
EXPRESSION = re.compile(r"^[A-Za-z0-9_ -]{1,80}$")


class SlackArgError(Exception):
    """A bad command, to be shown to the user rather than logged as a 500."""


class _Parser(argparse.ArgumentParser):
    """argparse that raises instead of exiting.

    Stock argparse calls `sys.exit()` on a bad flag. Inside a web handler that
    is a 500, and the user sees Slack's generic "dispatch_failed" instead of the
    reason their command was wrong — which is unhelpful precisely when they most
    need help.
    """

    def error(self, message: str):  # type: ignore[override]
        raise SlackArgError(message)

    def exit(self, status: int = 0, message: str | None = None):  # type: ignore[override]
        raise SlackArgError(message or "bad command")


def build_parser() -> _Parser:
    parser = _Parser(prog="/runtests", add_help=False)
    parser.add_argument("action", nargs="?", default="run", choices=ACTIONS)
    parser.add_argument("-p", "--product", required=True, choices=PRODUCTS)
    parser.add_argument("-s", "--server", default="staging", choices=SERVERS)
    parser.add_argument("-k", "--select", default=None)
    parser.add_argument("-m", "--marker", default=None)
    return parser


def parse(text: str) -> argparse.Namespace:
    """Parse `text` into a validated namespace, or raise SlackArgError.

    `shlex.split` is what makes `-k "smoke and not slow"` arrive as ONE argument
    instead of four. It is also why the expression regex below is applied after
    splitting rather than to the raw string.
    """
    args = build_parser().parse_args(shlex.split(text))
    for name in ("select", "marker"):
        value = getattr(args, name)
        if value is not None and not EXPRESSION.match(value):
            raise SlackArgError(
                f"--{name} may only contain letters, numbers, spaces, _ and -"
            )
    return args


USAGE_HINT = "Try: `/runtests -p webapp -s staging -k smoke`"
