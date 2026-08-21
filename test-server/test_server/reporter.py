"""The four things a test server says about a job.

The edge deliberately holds no Slack token and posts nothing. Every message in
the channel originates here, on the machine that actually ran the code, which
means the message cannot claim something the runner did not do.

    1 · received   the job arrived on this machine
    2 · started    the suite is running, and with what arguments
    3 · complete   it finished, and whether it passed
    4 · summary    the counts, and which tests failed

Four messages rather than one edited in place, because these are four distinct
facts and someone scrolling back wants to see the sequence. Progress *within* a
run is what `SlackNotifier.update()`'s throttling is for; these are milestones.

With no SLACK_BOT_TOKEN, every one of them prints to the console saying exactly
what it would have sent and where. That is the library's own behaviour and the
reason it is safe to call unconditionally — see slack_runtests/slack.py.
"""

from __future__ import annotations

from slack_runtests.slack import SlackNotifier


class JobReporter:
    """One reporter per job. Posts four messages to the job's channel."""

    def __init__(self, channel: str, runner_id: str) -> None:
        self.channel = channel
        self.runner_id = runner_id

    def _notifier(self) -> SlackNotifier:
        # A fresh notifier per message on purpose. SlackNotifier remembers the
        # `ts` of what it last posted so it can edit it; reusing one here would
        # turn these four milestones into one message overwritten three times.
        return SlackNotifier(channel=self.channel)

    @property
    def configured(self) -> bool:
        return self._notifier().enabled

    def received(self, job: dict) -> None:
        self._notifier().post(
            f"📥 Received `{job['product']}` on `{job['server']}` "
            f"(`{job['job_id']}`) — picked up by `{self.runner_id}`."
        )

    def started(self, job: dict, argv: list[str]) -> None:
        # The command is shown because it is the single most useful thing when
        # a run does something unexpected — and it is safe to show precisely
        # because every value in it came off an allowlist.
        self._notifier().post(
            f"▶️ Running `{job['product']}` on `{job['server']}` (`{job['job_id']}`)\n"
            f"`{' '.join(argv)}`"
        )

    def completed(self, job: dict, exit_code: int, duration: float) -> None:
        icon = "✅" if exit_code == 0 else "❌"
        verdict = "passed" if exit_code == 0 else f"failed (exit {exit_code})"
        self._notifier().post(
            f"{icon} Finished `{job['product']}` on `{job['server']}` "
            f"(`{job['job_id']}`) in {duration:.1f}s — {verdict}."
        )

    def summary(
        self,
        passed: int,
        failed: int,
        skipped: int,
        duration: float,
        failed_ids: list[str] | None = None,
    ) -> None:
        """Counts and failed test ids — never the suite's stdout.

        A run's output pasted into a channel is how a channel gets muted, and
        how internal hostnames and stack traces reach a searchable, wide
        audience. `finish()` on a fresh notifier posts rather than edits,
        because there is no earlier message of ours to edit.
        """
        self._notifier().finish(
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration=duration,
            failed_ids=failed_ids,
        )
