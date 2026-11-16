"""Stand-in for bot.py with the same interface, so tests never need the private bot.

FAKE_BOT_MODE: "call" (default: check/call), "raise", "slow" (hangs), "crash" (raises), "garbage".
"""
import os
import time


class Agent:
    def __init__(self):
        self.ends = 0

    def on_table_start(self, info):
        self.info = info

    def on_hand_end(self, summary):
        self.ends += 1

    def act(self, state):
        mode = os.environ.get("FAKE_BOT_MODE", "call")
        if mode == "slow":
            time.sleep(30)
        if mode == "crash":
            raise RuntimeError("SECRET_INTERNAL_VALUE equity=0.73")
        if mode == "garbage":
            return {"action": "raise", "amount": "lots", "equity": 0.73}
        if mode == "raise" and "raise" in state["legal_actions"]:
            return {"action": "raise", "amount": state["min_raise_to"], "debug": "SECRET_INTERNAL_VALUE"}
        if "check" in state["legal_actions"]:
            return {"action": "check"}
        return {"action": "call"}
