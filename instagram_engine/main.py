import sys
import time
import signal
import argparse
import logging
from datetime import datetime, timezone
from typing import Optional

from instagram_engine.models import (
    init_db,
    SessionLocal,
    IGAccount,
    AccountRole,
    AccountStatus,
    Lead,
    LeadStatus,
)
from instagram_engine.config import settings
from instagram_engine.scraper_worker import ScraperWorker
from instagram_engine.outreach_worker import OutreachWorker

# Setup structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("instagram_engine.main")

RUNNING = True


def handle_shutdown(signum, frame):
    """Graceful shutdown handler for SIGINT and SIGTERM."""
    global RUNNING
    sig_name = signal.Signals(signum).name
    logger.info("Received termination signal %s. Initiating graceful shutdown...", sig_name)
    RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def cmd_init_db():
    """Initializes PostgreSQL schema and tables."""
    logger.info("Initializing database tables on %s...", settings.database_url.split("@")[-1])
    init_db()
    logger.info("✅ Database schema initialized successfully.")


def cmd_add_account(username: str, password: str, role: str, proxy: Optional[str], tenant: str):
    """Adds a new Instagram account into the pool."""
    db = SessionLocal()
    try:
        existing = db.query(IGAccount).filter(IGAccount.username == username).first()
        if existing:
            logger.warning("Account @%s already exists in database (Status: %s). Updating...", username, existing.status)
            existing.password = password
            existing.role = AccountRole(role.upper())
            existing.proxy_url = proxy
            existing.tenant_id = tenant
            existing.status = AccountStatus.ACTIVE
            existing.cooldown_until = None
        else:
            acc = IGAccount(
                tenant_id=tenant,
                username=username,
                password=password,
                role=AccountRole(role.upper()),
                proxy_url=proxy,
                status=AccountStatus.ACTIVE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(acc)
        db.commit()
        logger.info("✅ Account @%s successfully registered as %s for tenant '%s'.", username, role.upper(), tenant)
    except Exception as exc:
        db.rollback()
        logger.error("Failed to add account @%s: %s", username, exc)
    finally:
        db.close()


def cmd_stats(tenant: str):
    """Displays pool statistics for accounts and leads."""
    db = SessionLocal()
    try:
        scrapers = db.query(IGAccount).filter(IGAccount.tenant_id == tenant, IGAccount.role == AccountRole.SCRAPER).count()
        senders = db.query(IGAccount).filter(IGAccount.tenant_id == tenant, IGAccount.role == AccountRole.SENDER).count()
        active_scrapers = db.query(IGAccount).filter(IGAccount.tenant_id == tenant, IGAccount.role == AccountRole.SCRAPER, IGAccount.status == AccountStatus.ACTIVE).count()
        active_senders = db.query(IGAccount).filter(IGAccount.tenant_id == tenant, IGAccount.role == AccountRole.SENDER, IGAccount.status == AccountStatus.ACTIVE).count()

        new_leads = db.query(Lead).filter(Lead.tenant_id == tenant, Lead.status == LeadStatus.NEW).count()
        sent_leads = db.query(Lead).filter(Lead.tenant_id == tenant, Lead.status == LeadStatus.DM_SENT).count()
        failed_leads = db.query(Lead).filter(Lead.tenant_id == tenant, Lead.status == LeadStatus.FAILED).count()

        print("\n" + "=" * 60)
        print(f"📊 INSTAGRAM LEAD GENERATION ENGINE POOL STATS [Tenant: {tenant}]")
        print("=" * 60)
        print(f"• Scraper Pool: {active_scrapers} active / {scrapers} total")
        print(f"• Sender Pool:  {active_senders} active / {senders} total")
        print("-" * 60)
        print(f"• Pending Leads (NEW):      {new_leads}")
        print(f"• Outreached Leads (DM_SENT): {sent_leads}")
        print(f"• Failed Leads:             {failed_leads}")
        print("=" * 60 + "\n")
    finally:
        db.close()


def run_scraper_loop(query: str, tenant: str, interval_min: int = 60):
    """Continuous scraper worker loop."""
    logger.info("Starting Scraper Loop for query '%s' [Tenant: %s]...", query, tenant)
    worker = ScraperWorker(tenant_id=tenant)

    while RUNNING:
        try:
            logger.info("🔍 Running scraper cycle for query: '%s'...", query)
            worker.process_query(query)
        except Exception as exc:
            logger.exception("Scraper cycle encountered unhandled error: %s", exc)

        logger.info("Scraper cycle complete. Sleeping for %d minutes...", interval_min)
        sleep_until = time.time() + (interval_min * 60)
        while RUNNING and time.time() < sleep_until:
            time.sleep(1)


def run_outreach_loop(tenant: str, batch_size: int = 5, idle_interval_min: int = 15):
    """Continuous outreach worker loop adhering to circadian hours and long random pauses."""
    logger.info("Starting Outreach Worker Loop [Tenant: %s]...", tenant)
    worker = OutreachWorker(tenant_id=tenant)

    while RUNNING:
        try:
            logger.info("📬 Running outreach batch check...")
            worker.process_pending_leads(max_batch=batch_size)
        except Exception as exc:
            logger.exception("Outreach cycle encountered unhandled error: %s", exc)

        logger.info("Outreach batch idle sleep (%d minutes)...", idle_interval_min)
        sleep_until = time.time() + (idle_interval_min * 60)
        while RUNNING and time.time() < sleep_until:
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Multi-Tenant Instagram Lead Generation & Outreach Engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-db
    subparsers.add_parser("init-db", help="Initialize PostgreSQL tables")

    # add-account
    parser_add = subparsers.add_parser("add-account", help="Add an Instagram account to the pool")
    parser_add.add_argument("--username", required=True, help="Instagram username")
    parser_add.add_argument("--password", required=True, help="Instagram password")
    parser_add.add_argument("--role", required=True, choices=["scraper", "sender"], help="Account pool role")
    parser_add.add_argument("--proxy", required=False, default=None, help="Proxy URL (e.g. http://user:pass@host:port)")
    parser_add.add_argument("--tenant", required=False, default=settings.default_tenant_id, help="Tenant ID")

    # stats
    parser_stats = subparsers.add_parser("stats", help="Display account pool and lead statistics")
    parser_stats.add_argument("--tenant", required=False, default=settings.default_tenant_id, help="Tenant ID")

    # scrape
    parser_scrape = subparsers.add_parser("scrape", help="Run scraper worker")
    parser_scrape.add_argument("--query", required=True, help="Search query or hashtag (e.g. 'لباس مجلسی')")
    parser_scrape.add_argument("--tenant", required=False, default=settings.default_tenant_id, help="Tenant ID")
    parser_scrape.add_argument("--interval", type=int, default=45, help="Minutes between scraper cycles")

    # outreach
    parser_outreach = subparsers.add_parser("outreach", help="Run outreach sender worker")
    parser_outreach.add_argument("--tenant", required=False, default=settings.default_tenant_id, help="Tenant ID")
    parser_outreach.add_argument("--batch", type=int, default=5, help="Number of leads to process per batch")

    # all
    parser_all = subparsers.add_parser("all", help="Run both scraper and outreach concurrently")
    parser_all.add_argument("--query", required=True, help="Search query or hashtag to scrape")
    parser_all.add_argument("--tenant", required=False, default=settings.default_tenant_id, help="Tenant ID")

    args = parser.parse_args()

    if args.command == "init-db":
        cmd_init_db()

    elif args.command == "add-account":
        cmd_add_account(args.username, args.password, args.role, args.proxy, args.tenant)

    elif args.command == "stats":
        cmd_stats(args.tenant)

    elif args.command == "scrape":
        run_scraper_loop(args.query, args.tenant, args.interval)

    elif args.command == "outreach":
        run_outreach_loop(args.tenant, args.batch)

    elif args.command == "all":
        import threading
        logger.info("🚀 Launching Scraper and Outreach workers concurrently in daemon threads...")
        t_scraper = threading.Thread(target=run_scraper_loop, args=(args.query, args.tenant, 45), daemon=True)
        t_outreach = threading.Thread(target=run_outreach_loop, args=(args.tenant, 5, 15), daemon=True)

        t_scraper.start()
        t_outreach.start()

        while RUNNING:
            time.sleep(1)
        logger.info("All engine threads stopped. Goodbye!")


if __name__ == "__main__":
    main()
