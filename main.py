import argparse
import logging
import re
import sys
from types import SimpleNamespace
from config import (
    BASE_URL,
    VERSION,
    SCHEDULER_ENABLED,
    SCHEDULER_MODE,
    SCHEDULE_DAY,
    SCHEDULE_HOUR,
    SCHEDULE_MINUTE,
)
from selenium.webdriver.common.by import By

from ibis.auto_recovery import AutoRecovery
from ibis.browser import create_driver
from ibis.downloader import DownloadQueue, extract_link_metadata, build_download_queue
from ibis.downloader_engine import DownloaderEngine
from ibis.grid_walker import collect_grid_download_links, get_devexpress_pager_info
from ibis.invoice import open_invoice_page
from ibis.grid import wait_for_grid, get_grid_text, count_grid_rows
from ibis.login import wait_until_logged_in
from ibis.resume import has_interrupted_session, build_resume_queue
from ibis.scheduler import DownloadPlan, Scheduler
from ibis.period_tracker import PeriodTracker
from ibis.state import DownloadState
from ibis.state_manager import StateManager
from ibis.billing_period import BillingPeriodManager, BillingPeriodNotFoundError
from logger import configure_logging

logger = logging.getLogger(__name__)

_PERIOD_RE = re.compile(r"^\d{6}$")


def normalize_billing_periods(value):
    """Normalize a CLI billing-period value into a mode or unique periods."""
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"latest", "all"}:
        return text
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part or not _PERIOD_RE.fullmatch(part) for part in parts):
        raise argparse.ArgumentTypeError(
            "billing periods must be YYYYMM values separated by commas, 'latest', or 'all'"
        )
    return tuple(dict.fromkeys(parts))


def parse_cli_args(argv=None):
    parser = argparse.ArgumentParser(description="IBIS Auto Downloader")
    parser.add_argument(
        "--billing-period",
        type=normalize_billing_periods,
        default=None,
        metavar="YYYYMM[,YYYYMM]|latest|all",
        help="Billing period selection; defaults to the latest available period.",
    )
    return parser.parse_args([] if argv is None else argv)


def _filter_links_for_periods(links, selected_periods):
    selected = set(selected_periods)
    return [
        link
        for link in links
        if extract_link_metadata(link)[1] in selected
    ]


def _build_selected_queue(links, state_manager, selected_periods):
    """Filter links before queue construction without changing downloader logic."""
    selected_links = _filter_links_for_periods(links, selected_periods)
    pending_links, already_completed_count = state_manager.filter_pending_links(selected_links)
    latest = max(selected_periods) if selected_periods else None
    return SimpleNamespace(
        queue=DownloadQueue.from_links(pending_links),
        found_count=len(selected_links),
        already_completed_count=already_completed_count,
        latest_billing_period=latest,
    )


def _resolve_available_periods(manager, selection):
    available = manager.get_periods()
    if selection in (None, "latest"):
        latest = manager.latest()
        return [latest] if latest is not None else []
    if selection == "all":
        return available
    try:
        for period in selection:
            manager.select(period)
    except BillingPeriodNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    return [period for period in available if period in selection]


def _state_periods(state):
    selected = state.get("selected_periods")
    if selected is not None:
        return sorted(set(selected), reverse=True)
    return sorted(
        {
            item.get("billing_period")
            for item in state.get("queue", [])
            if item.get("billing_period") is not None
        },
        reverse=True,
    )


def _resolve_resume_periods(state, selection):
    available = _state_periods(state)
    if selection is None:
        selected = (
            state["selected_periods"]
            if state.get("selected_periods") is not None
            else (available[:1] if available else [])
        )
        return list(selected)
    if selection == "latest":
        return available[:1]
    if selection == "all":
        return available
    missing = [period for period in selection if period not in available]
    if missing:
        choices = ", ".join(available) or "none"
        raise ValueError(
            f"Billing period '{missing[0]}' does not exist in the resumable session. "
            f"Available periods: {choices}."
        )
    return [period for period in available if period in selection]


def _download_workflow(billing_period_selection=None):
    """Execute the full Build 2.5 download workflow (one pass).

    If ``state/download_state.json`` records an interrupted session the
    download queue is restored from that file (pending and failed items only)
    and the grid-scanning step is skipped.  All other behaviour is preserved.
    """

    logger.info("Starting download workflow.")
    ds = DownloadState()
    saved_state = ds.load_state()
    resuming = has_interrupted_session(saved_state)

    driver = create_driver()
    # _initial_driver is consumed by AutoRecovery on its first run; if an
    # exception occurs before that happens the finally block drains it.
    _initial_driver = [driver]
    state_manager = StateManager()

    try:
        driver.get(BASE_URL)

        print("请在浏览器完成登录...")
        wait_until_logged_in(driver)

        print("登录成功。")

        if resuming:
            print("Resuming interrupted download session...")
            selected_periods = _resolve_resume_periods(saved_state, billing_period_selection)
            ds.restore(saved_state, selected_periods=selected_periods)
            ds.save_state()
            queue = build_resume_queue(saved_state, periods=selected_periods)
            latest_billing_period = max(selected_periods) if selected_periods else None
            already_completed_count = len(saved_state.get("completed", []))
            found_count = sum(
                1
                for item in saved_state.get("queue", [])
                if item.get("billing_period") in selected_periods
            )
        else:
            print("正在打开 Invoice 页面...")

            html = open_invoice_page(driver)

            with open("invoice_page.html", "w", encoding="utf-8") as f:
                f.write(html)

            wait_for_grid(driver)

            anchors = driver.find_elements(
                By.CSS_SELECTOR,
                "a[href*='DownloadARExport.aspx']"
            )

            print("Selenium found:", len(anchors))

            for a in anchors[:5]:
                print(a.get_attribute("href"))

            html2 = driver.page_source

            with open("invoice_after_wait.html", "w", encoding="utf-8") as f:
                f.write(html2)

            print("After wait:", html2.count("DownloadARExport.aspx"))
            print("Pager info:", get_devexpress_pager_info(html2))

            print(f"Grid TR 数量：{count_grid_rows(driver)}")
            print("\n========== Grid Preview ==========")
            print(get_grid_text(driver)[:1000])
            print("==================================\n")

            all_links = collect_grid_download_links(driver, BASE_URL)
            period_manager = BillingPeriodManager(driver, base_url=BASE_URL, links=all_links)
            selected_periods = _resolve_available_periods(
                period_manager, billing_period_selection
            )
            if len(selected_periods) <= 1:
                selected_links = _filter_links_for_periods(all_links, selected_periods)
                queue_result = build_download_queue(
                    selected_links, state_manager=state_manager
                )
            else:
                queue_result = _build_selected_queue(
                    all_links, state_manager, selected_periods
                )
            queue = queue_result.queue
            latest_billing_period = queue_result.latest_billing_period
            found_count = queue_result.found_count
            already_completed_count = queue_result.already_completed_count
            ds.selected_periods = selected_periods

        print(f"Found invoices: {found_count}")
        print(f"Already completed: {already_completed_count}")
        print(f"Download Queue: {len(queue)}")

        plan = DownloadPlan(queue, latest_only=False)
        print(f"下载计划已建立，共 {plan.scheduled_count} 个项目。")

        period_tracker = PeriodTracker()
        current_period = latest_billing_period or plan.latest_billing_period

        # Capture the last engine created so its summary is accessible after run.
        last_engine = [None]

        def _engine_factory(d):
            e = DownloaderEngine(d, state_manager=state_manager, download_state=ds)
            if resuming:
                e.preserve_existing_state = True
            last_engine[0] = e
            return e

        # Provide the already-logged-in scan driver on the first attempt; for
        # any recovery attempt a fresh driver is created via create_driver().
        def _driver_factory():
            if _initial_driver:
                return _initial_driver.pop()
            return create_driver()

        ar = AutoRecovery(
            driver_factory=_driver_factory,
            login_fn=wait_until_logged_in,
            open_invoice_fn=open_invoice_page,
            download_state=ds,
            engine_factory=_engine_factory,
        )
        recovery_summary = ar.run(plan)

        engine = last_engine[0]
        if recovery_summary is not None:
            print("Retry and Recovery Summary")
            print("--------------------------")
            print(f"Retry Attempts: {recovery_summary.retry_attempts}")
            print(f"Successful Recoveries: {recovery_summary.successful_recoveries}")
            print(f"Permanent Failures: {recovery_summary.permanent_failures}")
        if engine is not None and engine.summary.failed == 0 and current_period is not None:
            if len(queue) == 0:
                if not period_tracker.last_period_file_exists():
                    period_tracker.save_last_period(current_period)
            elif engine.summary.completed == plan.scheduled_count:
                period_tracker.save_last_period(current_period)

    finally:
        # AutoRecovery quits its own driver when run() returns or raises.
        # Only quit here if AutoRecovery never consumed the initial driver
        # (i.e. an exception occurred before ar.run() was reached).
        if _initial_driver:
            driver.quit()


def main(argv=None):
    args = parse_cli_args(argv)
    run_id = configure_logging()
    logger.info("Starting IBIS Auto Downloader version %s (run %s).", VERSION, run_id)
    if not SCHEDULER_ENABLED:
        # Build 2.6 behaviour: run once and exit.
        scheduler = Scheduler(lambda: _download_workflow(args.billing_period))
        scheduler.run_once()
        return

    # Build 2.7: use configurable schedule.
    scheduler = Scheduler(
        lambda: _download_workflow(args.billing_period),
        mode=SCHEDULER_MODE,
        schedule_day=SCHEDULE_DAY,
        schedule_hour=SCHEDULE_HOUR,
        schedule_minute=SCHEDULE_MINUTE,
        run_immediately=(SCHEDULER_MODE == "immediate"),
    )

    if SCHEDULER_MODE == "immediate":
        if scheduler.should_run_now():
            scheduler.run_once()
    else:
        while True:
            scheduler.wait_until_next_run()
            try:
                scheduler.run_once()
            except Exception:
                logger.exception("Scheduled workflow execution failed; waiting for next run.")


if __name__ == "__main__":
    main(sys.argv[1:])
