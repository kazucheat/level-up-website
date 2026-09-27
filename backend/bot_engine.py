# backend/bot_engine.py
"""
Bot engine — FF Level Up integration.
Yaha tu jo apne paas working bot code hai (Main.py from FF_Level_Bot_API)
wo import aur adapt kar sakta hai.
"""

import asyncio
import time
import json
from pathlib import Path
from typing import Dict, List, Optional, Any

BASE_DIR = Path(__file__).parent


class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}   # key = "userId:uid"
        self.logs: List[Dict] = []
        self.max_logs = 300
        self.workers: Dict[str, asyncio.Task] = {}

    def log(self, message: str, level: str = "info", user_id: Optional[str] = None):
        self.logs.append({
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "user_id": user_id
        })
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]

    def register(self, user_id: str, uid: str, nickname: str, region: str, level: int, exp: int):
        key = f"{user_id}:{uid}"
        if key not in self.accounts:
            self.accounts[key] = {
                "uid": uid,
                "user_id": user_id,
                "nickname": nickname or f"Player_{uid[:6]}",
                "region": region or "BD",
                "level": level or 1,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "status": "ONLINE",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[key]
            if nickname: acc["nickname"] = nickname
            if region: acc["region"] = region
            if level: acc["level"] = level
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["status"] = "ONLINE"
            acc["last_updated"] = time.strftime("%H:%M:%S")

    def update_exp(self, user_id: str, uid: str, exp: int, level: Optional[int] = None):
        key = f"{user_id}:{uid}"
        if key in self.accounts:
            a = self.accounts[key]
            a["current_exp"] = exp
            if level: a["level"] = level
            a["gained_exp"] = max(0, exp - a["initial_exp"])
            a["last_updated"] = time.strftime("%H:%M:%S")

    def update_status(self, user_id: str, uid: str, status: str, active: Optional[int] = None):
        key = f"{user_id}:{uid}"
        if key in self.accounts:
            self.accounts[key]["status"] = status
            if active is not None:
                self.accounts[key]["active_matches"] = active

    def increment_match(self, user_id: str, uid: str):
        key = f"{user_id}:{uid}"
        if key in self.accounts:
            self.accounts[key]["matches_played"] += 1
            self.accounts[key]["last_match_time"] = time.strftime("%H:%M:%S")

    def get_user_accounts(self, user_id: str) -> List[Dict]:
        return [a for k, a in self.accounts.items() if a["user_id"] == user_id]

    def get_all_accounts(self) -> List[Dict]:
        return list(self.accounts.values())

    def get_logs(self, user_id: Optional[str] = None) -> List[Dict]:
        if user_id:
            return [l for l in self.logs if l.get("user_id") == user_id]
        return self.logs

    def stop_bot(self, user_id: str, uid: str):
        key = f"{user_id}:{uid}"
        if key in self.workers:
            self.workers[key].cancel()
            del self.workers[key]
        if key in self.accounts:
            self.accounts[key]["status"] = "OFFLINE"


bot_state = BotState()


# ==================== BOT WORKER ====================
async def start_bot_for_account(user_id: str, uid: str, password: str):
    """Spawn a bot for this account. Uses the FF protocol (adapt from your Main.py)."""
    key = f"{user_id}:{uid}"
    if key in bot_state.workers and not bot_state.workers[key].done():
        return

    async def worker():
        try:
            bot_state.log(f"[{uid}] Starting bot...", "info", user_id)
            # TODO: integrate your FF bot code here (Main.py + bot_engine.py + thunderFF_pb2)
            # For now — keep alive with simulated EXP gain
            bot_state.register(user_id, uid, f"Player_{uid[:6]}", "BD", 1, 0)

            matches = 0
            while True:
                await asyncio.sleep(30)
                matches += 1
                gained = matches * 150
                bot_state.update_exp(user_id, uid, gained, level=1 + matches // 10)
                bot_state.increment_match(user_id, uid)
                bot_state.update_status(user_id, uid, "IN_MATCH", active=1)
                bot_state.log(f"[{uid}] Match #{matches} done. +150 EXP", "success", user_id)
                await asyncio.sleep(5)
                bot_state.update_status(user_id, uid, "ONLINE", active=0)
        except asyncio.CancelledError:
            bot_state.log(f"[{uid}] Bot stopped", "warning", user_id)
            bot_state.update_status(user_id, uid, "OFFLINE")
            raise

    task = asyncio.create_task(worker())
    bot_state.workers[key] = task