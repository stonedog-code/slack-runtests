"""The test server: pulls work from the edge, runs pytest, reports to Slack.

Everything interesting happens here. The edge validates and queues; this is
where the logic, the suite and the Slack conversation live. See README.md in
this directory.
"""
