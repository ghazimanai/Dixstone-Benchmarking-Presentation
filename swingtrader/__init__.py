"""Scheduled swing-trade research assistant.

Automates the parts of a daily pre-market routine that *can* be automated
(price/volume history, technical setups, risk sizing, screener pulls) and
produces an explicit verification checklist for the parts that cannot be
automated from a server (Bloomberg Terminal, LSEG Workspace, Morningstar).

This is a research and screening tool. It does not place orders and its
output is not investment advice.
"""

__version__ = "0.1.0"
