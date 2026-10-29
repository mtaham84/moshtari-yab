"""Twitter/X Scraper Account Pool & Load Balancer.

Manages multiple Twitter accounts, rotates searches logically (round-robin / least-loaded),
and handles failover when an account hits rate-limits or auth errors.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

log = logging.getLogger("x_collector.accounts")


@dataclass
class TwitterAccount:
    id: int
    username: str
    password: str
    auth_token: str
    ct0: str
    proxy_url: str
    status: str
    cooldown_until: datetime | None = None
    daily_actions: int = 0
    total_actions: int = 0
    posts_found: int = 0
    last_action_at: datetime | None = None
    is_active: bool = True

    def is_available(self) -> bool:
        if not self.is_active or self.status in {"BANNED", "DISABLED", "CHALLENGED"}:
            return False
        if self.cooldown_until:
            now = datetime.now(timezone.utc)
            cd = self.cooldown_until if self.cooldown_until.tzinfo else self.cooldown_until.replace(tzinfo=timezone.utc)
            if cd > now:
                return False
        return True


class TwitterAccountPool:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn
        self.accounts: list[TwitterAccount] = []
        self._current_index = 0
        self.refresh()

    def _get_pg_conn(self):
        try:
            import psycopg
            from x_ingest.__main__ import _dsn
            return psycopg.connect(self.dsn or _dsn(), connect_timeout=4)
        except Exception as exc:
            log.debug("Database connection failed in TwitterAccountPool: %s", exc)
            return None

    def refresh(self) -> None:
        """Loads accounts from PostgreSQL table x_accounts or falls back to environment defaults."""
        conn = self._get_pg_conn()
        loaded = []
        if conn:
            try:
                with conn:
                    # Check if table exists
                    has_table = conn.execute("SELECT to_regclass('public.x_accounts')").fetchone()[0]
                    if has_table:
                        rows = conn.execute("""
                            SELECT id, username, password, auth_token, ct0, proxy_url, status,
                                   cooldown_until, daily_action_count, total_action_count,
                                   posts_found_count, last_action_at, is_active
                            FROM public.x_accounts
                            ORDER BY id ASC
                        """).fetchall()
                        for r in rows:
                            loaded.append(TwitterAccount(
                                id=r[0],
                                username=r[1],
                                password=r[2] or "",
                                auth_token=r[3] or "",
                                ct0=r[4] or "",
                                proxy_url=r[5] or "",
                                status=r[6] or "ACTIVE",
                                cooldown_until=r[7],
                                daily_actions=r[8] or 0,
                                total_actions=r[9] or 0,
                                posts_found=r[10] or 0,
                                last_action_at=r[11],
                                is_active=bool(r[12]) if r[12] is not None else True
                            ))
            except Exception as exc:
                log.warning("Could not read x_accounts from database: %s", exc)

        if not loaded:
            # Fallback to env / default seeded accounts
            primary_auth = os.getenv("TWITTER_AUTH_TOKEN", "b2fcb8fbdf370a373023aa9758d8e23aaf15dbbf")
            primary_ct0 = os.getenv("TWITTER_CT0", "9abb304674bf282b38ad51a29177e80c5bcd834ea4b56f226cd24b0e9d1a2a76d2c3defe085f4ed499fc5ec54f708735e62de74dbdae17efcb6aa8ad98aec74e335ae75927e7b4d40c50bdd872d1a2a6")
            primary_proxy = os.getenv("TWITTER_PROXY", "socks5h://xray:1080")

            loaded = [
                TwitterAccount(
                    id=1,
                    username="scienceTechno_m",
                    password="",
                    auth_token=primary_auth,
                    ct0=primary_ct0,
                    proxy_url=primary_proxy,
                    status="ACTIVE"
                ),
                TwitterAccount(
                    id=2,
                    username="ebi123456url",
                    password="Sahand1385@",
                    auth_token="9baa2fdd446fc020b08ea56501bd1a991f50ee21",
                    ct0="a" * 32,
                    proxy_url=primary_proxy,
                    status="CHALLENGED"
                )
            ]

        self.accounts = loaded

    def seed_initial_accounts_if_empty(self) -> None:
        """Seeds default accounts into the database if the table is empty."""
        conn = self._get_pg_conn()
        if not conn:
            return
        try:
            with conn:
                has_table = conn.execute("SELECT to_regclass('public.x_accounts')").fetchone()[0]
                if not has_table:
                    return
                count = conn.execute("SELECT count(*) FROM public.x_accounts").fetchone()[0]
                if count == 0:
                    primary_auth = os.getenv("TWITTER_AUTH_TOKEN", "b2fcb8fbdf370a373023aa9758d8e23aaf15dbbf")
                    primary_ct0 = os.getenv("TWITTER_CT0", "9abb304674bf282b38ad51a29177e80c5bcd834ea4b56f226cd24b0e9d1a2a76d2c3defe085f4ed499fc5ec54f708735e62de74dbdae17efcb6aa8ad98aec74e335ae75927e7b4d40c50bdd872d1a2a6")
                    proxy = os.getenv("TWITTER_PROXY", "socks5h://xray:1080")

                    conn.execute("""
                        INSERT INTO public.x_accounts (username, password, auth_token, ct0, proxy_url, status, status_message, daily_action_count, total_action_count, posts_found_count, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, 0, true, now(), now())
                    """, ("scienceTechno_m", "", primary_auth, primary_ct0, proxy, "ACTIVE", "متصل و فعال"))

                    conn.execute("""
                        INSERT INTO public.x_accounts (username, password, auth_token, ct0, proxy_url, status, status_message, daily_action_count, total_action_count, posts_found_count, is_active, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, 0, true, now(), now())
                    """, ("ebi123456url", "Sahand1385@", "9baa2fdd446fc020b08ea56501bd1a991f50ee21", "a" * 32, proxy, "CHALLENGED", "سشن نیازمند لاگین اولیه یا تایید مرورگر"))
                    log.info("Seeded initial Twitter scraper accounts into database")
                    self.refresh()
        except Exception as exc:
            log.warning("Failed to seed initial Twitter accounts: %s", exc)

    def get_next_account(self) -> TwitterAccount | None:
        """Selects the next available healthy account using round-robin logic."""
        if not self.accounts:
            self.refresh()
        available = [acc for acc in self.accounts if acc.is_available()]
        if not available:
            return None

        # Round-robin
        self._current_index = self._current_index % len(available)
        account = available[self._current_index]
        self._current_index = (self._current_index + 1) % len(available)
        return account

    def record_rate_limit(self, account: TwitterAccount, cooldown_seconds: int = 900) -> None:
        """Sets an account in cooldown and persists to database."""
        until = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
        account.cooldown_until = until
        account.status = "COOLDOWN"
        log.warning("Twitter account @%s rate limited until %s", account.username, until.isoformat())

        conn = self._get_pg_conn()
        if conn:
            try:
                with conn:
                    conn.execute("""
                        UPDATE public.x_accounts
                        SET status = 'COOLDOWN', cooldown_until = %s, status_message = 'استراحت به دلیل محدودیت نرخ درخواست (429)', updated_at = now()
                        WHERE id = %s
                    """, (until, account.id))
            except Exception as exc:
                log.debug("Failed to persist rate limit in DB for account %s: %s", account.id, exc)

    def record_auth_error(self, account: TwitterAccount, error_msg: str) -> None:
        account.status = "CHALLENGED"
        log.error("Twitter account @%s auth failed: %s", account.username, error_msg)

        conn = self._get_pg_conn()
        if conn:
            try:
                with conn:
                    conn.execute("""
                        UPDATE public.x_accounts
                        SET status = 'CHALLENGED', status_message = %s, updated_at = now()
                        WHERE id = %s
                    """, (error_msg[:250], account.id))
            except Exception as exc:
                log.debug("Failed to persist auth error in DB for account %s: %s", account.id, exc)

    def record_success(self, account: TwitterAccount, posts_count: int = 0) -> None:
        now = datetime.now(timezone.utc)
        account.daily_actions += 1
        account.total_actions += 1
        account.posts_found += posts_count
        account.last_action_at = now
        if account.status == "COOLDOWN":
            account.status = "ACTIVE"
            account.cooldown_until = None

        conn = self._get_pg_conn()
        if conn:
            try:
                with conn:
                    conn.execute("""
                        UPDATE public.x_accounts
                        SET daily_action_count = daily_action_count + 1,
                            total_action_count = total_action_count + 1,
                            posts_found_count = posts_found_count + %s,
                            last_action_at = %s,
                            status = 'ACTIVE',
                            status_message = 'متصل و فعال',
                            cooldown_until = NULL,
                            updated_at = now()
                        WHERE id = %s
                    """, (posts_count, now, account.id))
            except Exception as exc:
                log.debug("Failed to persist success in DB for account %s: %s", account.id, exc)

    def get_pool_status(self) -> dict[str, Any]:
        available = [acc for acc in self.accounts if acc.is_available()]
        cooldowns = [acc for acc in self.accounts if acc.status == "COOLDOWN" and acc.cooldown_until]
        min_cd = 0
        now = datetime.now(timezone.utc)
        if cooldowns and not available:
            remaining_times = []
            for acc in cooldowns:
                cd = acc.cooldown_until if acc.cooldown_until.tzinfo else acc.cooldown_until.replace(tzinfo=timezone.utc)
                diff = (cd - now).total_seconds()
                if diff > 0:
                    remaining_times.append(diff)
            min_cd = int(min(remaining_times)) if remaining_times else 0

        return {
            "total": len(self.accounts),
            "available": len(available),
            "cooldown_count": len(cooldowns),
            "min_cooldown_seconds": min_cd,
            "accounts": [
                {
                    "id": a.id,
                    "username": a.username,
                    "status": a.status,
                    "available": a.is_available(),
                    "daily_actions": a.daily_actions,
                    "posts_found": a.posts_found,
                    "proxy": a.proxy_url
                }
                for a in self.accounts
            ]
        }


def seed_initial_accounts_if_empty(dsn: str | None = None) -> None:
    """Helper function to seed default accounts into DB if empty."""
    pool = TwitterAccountPool(dsn=dsn)
    pool.seed_initial_accounts_if_empty()

