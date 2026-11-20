"""Legacy prototype agents (pre-dashboard version).

These seven modules (parser, matcher, ranker, bias guard, scheduler, career
coach and their coordinator) powered the original single-page prototype.
They are NOT part of the recruiter/candidate dashboards, which use the eight
agents in agents/platform_agents.py. Kept for reference only; nothing in the
running app imports this package except the legacy /api/run-pipeline endpoints.
"""
