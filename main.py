import argparse
import logging
import re
import sys
from datetime import datetime, timezone
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
from ibis.reporting import RunReporter
from ibis.performance import PerformanceTracker
from logger import configure_logging

logger = logging.getLogger(__name__)
_DOWNLOADER_ENGINE_TYPE = DownloaderEngine

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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the selected download queue without downloading or changing state.",
    )
    return parser.parse_args([] if argv is None else argv)


def _filter_links_for_periods(links, selected_periods):
    selected = set(selected_periods)
    return [
        link
        for link in links
        if extract_link_metadata(link)[1] in selected
    ]


def _build_selected_queue(links, state_manager, selected_periods, *, read_only=False):
    """Filter links before queue construction without changing downloader logic."""
    selected_links = _filter_links_for_periods(links, selected_periods)
    pending_links, already_completed_count = state_manager.filter_pending_links(
        selected_links, read_only=read_only
    )
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


def _item_key(item):
    """Return the stable report identity for a queue/link/state item."""
    if isinstance(item, dict):
        invoice_id = item.get("invoice_id")
        billing_period = item.get("billing_period")
        if (invoice_id is None or billing_period is None) and item.get("url"):
            invoice_id, billing_period, _ = extract_link_metadata(item)
    else:
        invoice_id = getattr(item, "invoice_id", None)
        billing_period = getattr(item, "billing_period", None)
    return invoice_id, billing_period


def _report_invoice_details(items, state, skipped_keys, recovered_keys, *, dry_run=False,
                            queued_keys=None):
    """Build report records from the final state without changing downloads."""
    state = state if isinstance(state, dict) else {}
    completed = {_item_key(item): item for item in state.get("completed", [])}
    failed = {_item_key(item): item for item in state.get("failed", [])}
    details = []
    for item in items:
        invoice_id, billing_period = _item_key(item)
        key = invoice_id, billing_period
        final_item = completed.get(key) or failed.get(key) or item
        if dry_run:
            status = "would_download" if key in (queued_keys or set()) else "skipped"
        elif key in completed:
            status = "completed"
        elif key in failed:
            status = "failed"
        elif key in skipped_keys:
            status = "skipped"
        elif isinstance(final_item, dict):
            status = final_item.get("download_status", "pending")
        else:
            status = getattr(final_item, "download_status", "pending")
        filename = (
            final_item.get("filename")
            if isinstance(final_item, dict)
            else getattr(final_item, "filename", None)
        )
        retry_count = (
            final_item.get("retry_count", 0)
            if isinstance(final_item, dict)
            else getattr(final_item, "retry_count", 0)
        )
        details.append(
            {
                "billing_period": billing_period,
                "invoice_id": invoice_id,
                "filename": filename,
                "final_status": status,
                "retry_count": retry_count,
                "recovered": bool(key in recovered_keys and status == "completed"),
                "elapsed_seconds": 0.0,
            }
        )
    return details


def _preview_filename(item):
    """Describe the filename without guessing or changing its extension."""
    filename = item.get("filename") if isinstance(item, dict) else getattr(item, "filename", None)
    if filename:
        return filename
    invoice_id, billing_period = _item_key(item)
    if invoice_id and billing_period:
        return f"{billing_period}_{invoice_id} (server extension)"
    return "server-provided filename"


def _print_dry_run_preview(plan, selected_periods, found_count, already_completed_count):
    """Print a human-readable, side-effect-free queue preview."""
    from collections import Counter

    grouped = Counter(item.billing_period for item in plan.scheduled_items)
    print("\n========== DRY RUN PREVIEW ==========")
    print(f"Selected billing periods: {', '.join(selected_periods) or 'none'}")
    print(f"Invoices discovered: {found_count}")
    print(f"Already completed / skipped: {already_completed_count}")
    print(f"Invoices that would be queued: {plan.scheduled_count}")
    print("Queue by billing period:")
    for period in sorted(grouped, reverse=True):
        print(f"  {period}: {grouped[period]}")
    for item in plan.scheduled_items:
        print(
            f"  Invoice {item.invoice_id} | period {item.billing_period} | "
            f"expected filename: {_preview_filename(item)}"
        )
    print("DRY RUN — no files were downloaded")
    print("=====================================\n")


def _download_workflow(billing_period_selection=None, *, run_id=None, dry_run=False):
    """Execute the full Build 2.5 download workflow (one pass).

    If ``state/download_state.json`` records an interrupted session the
    download queue is restored from that file (pending and failed items only)
    and the grid-scanning step is skipped.  All other behaviour is preserved.
    """

    logger.info("Starting download workflow.")
    started_at = datetime.now(timezone.utc)
    performance = PerformanceTracker()
    with performance.stage("state_loading"):
        ds = DownloadState()
        saved_state = ds.load_state()
        resuming = has_interrupted_session(saved_state)

    driver = None
    # _initial_driver is consumed by AutoRecovery on its first run; if an
    # exception occurs before that happens the finally block drains it.
    _initial_driver = []
    state_manager = StateManager()
    report_items = []
    skipped_keys = set()
    recovered_keys = set()
    selected_periods = []
    found_count = 0
    already_completed_count = 0
    queue = DownloadQueue()
    engine = None
    recovery_summary = None
    ar = None
    workflow_error = None
    failure_stage = "initialization"

    try:
        with performance.stage("browser_startup"):
            driver = create_driver()
            _initial_driver.append(driver)
        failure_stage = "authentication"
        with performance.stage("authentication"):
            driver.get(BASE_URL)
            print("请在浏览器完成登录...")
            wait_until_logged_in(driver)

        print("登录成功。")

        if resuming:
            with performance.stage("resume"):
                failure_stage = "resume"
                print("Resuming interrupted download session...")
                selected_periods = _resolve_resume_periods(saved_state, billing_period_selection)
                # A dry run reads the snapshot but never rewrites it, including
                # avoiding a legacy-session migration during preview generation.
                if not dry_run:
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
                report_items = [
                    item for item in saved_state.get("queue", [])
                    if item.get("billing_period") in selected_periods
                ]
        else:
            with performance.stage("invoice_discovery"):
                failure_stage = "discovery"
                print("正在打开 Invoice 页面...")

                html = open_invoice_page(driver)

                if not dry_run:
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

                if not dry_run:
                    with open("invoice_after_wait.html", "w", encoding="utf-8") as f:
                        f.write(html2)

                print("After wait:", html2.count("DownloadARExport.aspx"))
                print("Pager info:", get_devexpress_pager_info(html2))

                print(f"Grid TR 数量：{count_grid_rows(driver)}")
                print("\n========== Grid Preview ==========")
                print(get_grid_text(driver)[:1000])
                print("==================================\n")

                all_links = collect_grid_download_links(driver, BASE_URL, grid_ready=True)
                period_manager = BillingPeriodManager(driver, base_url=BASE_URL, links=all_links)
                selected_periods = _resolve_available_periods(
                    period_manager, billing_period_selection
                )
                selected_links = _filter_links_for_periods(all_links, selected_periods)
                if len(selected_periods) <= 1 and not dry_run:
                    queue_result = build_download_queue(
                        selected_links, state_manager=state_manager
                    )
                else:
                    queue_result = _build_selected_queue(
                        all_links, state_manager, selected_periods, read_only=dry_run
                    )
                queue = queue_result.queue
                latest_billing_period = queue_result.latest_billing_period
                found_count = queue_result.found_count
                already_completed_count = queue_result.already_completed_count
                ds.selected_periods = selected_periods
                report_items = selected_links
                skipped_keys = {
                    _item_key(link) for link in selected_links
                } - {_item_key(item) for item in queue}

        print(f"Found invoices: {found_count}")
        print(f"Already completed: {already_completed_count}")
        print(f"Download Queue: {len(queue)}")

        with performance.stage("queue_planning"):
            plan = DownloadPlan(queue, latest_only=False)
        print(f"下载计划已建立，共 {plan.scheduled_count} 个项目。")

        if dry_run:
            with performance.stage("dry_run_preview"):
                _print_dry_run_preview(
                    plan, selected_periods, found_count, already_completed_count
                )
            failure_stage = None
            return

        period_tracker = PeriodTracker()
        current_period = latest_billing_period or plan.latest_billing_period

        # Capture the last engine created so its summary is accessible after run.
        last_engine = [None]

        def _engine_factory(d):
            e = DownloaderEngine(d, state_manager=state_manager, download_state=ds)
            if resuming:
                e.preserve_existing_state = True
            if isinstance(e, _DOWNLOADER_ENGINE_TYPE):
                original_run = e.run

                def _tracked_run(recovery_plan):
                    if getattr(e, "preserve_existing_state", False):
                        recovered_keys.update(
                            _item_key(item) for item in recovery_plan.scheduled_items
                        )
                    return original_run(recovery_plan)

                e.run = _tracked_run
            last_engine[0] = e
            return e

        # Provide the already-logged-in scan driver on the first attempt; for
        # any recovery attempt a fresh driver is created via create_driver().
        def _driver_factory():
            if _initial_driver:
                return _initial_driver.pop()
            return create_driver()

        failure_stage = "download"
        ar = AutoRecovery(
            driver_factory=_driver_factory,
            login_fn=wait_until_logged_in,
            open_invoice_fn=open_invoice_page,
            download_state=ds,
            engine_factory=_engine_factory,
        )
        with performance.stage("download_execution"):
            recovery_summary = ar.run(plan)

        engine = last_engine[0]
        if recovery_summary is not None:
            print("Retry and Recovery Summary")
            print("--------------------------")
            print(f"Retry Attempts: {recovery_summary.retry_attempts}")
            print(f"Successful Recoveries: {recovery_summary.successful_recoveries}")
            print(f"Permanent Failures: {recovery_summary.permanent_failures}")
        failure_stage = None
        if engine is not None and engine.summary.failed == 0 and current_period is not None:
            if len(queue) == 0:
                if not period_tracker.last_period_file_exists():
                    period_tracker.save_last_period(current_period)
            elif engine.summary.completed == plan.scheduled_count:
                period_tracker.save_last_period(current_period)

    except Exception as exc:
        workflow_error = exc
        engine = engine or last_engine[0] if "last_engine" in locals() else engine
        if recovery_summary is None and ar is not None:
            recovery_summary = getattr(ar, "summary", None)
        logger.exception("Workflow terminated during %s.", failure_stage)
        raise

    finally:
        engine_summary = getattr(engine, "summary", None)
        completed_count = getattr(engine_summary, "completed", 0)
        failed_count = getattr(engine_summary, "failed", 0)
        permanent_failure_count = getattr(recovery_summary, "permanent_failures", 0)
        completed_count = completed_count if isinstance(completed_count, int) else 0
        failed_count = failed_count if isinstance(failed_count, int) else 0
        permanent_failure_count = (
            permanent_failure_count if isinstance(permanent_failure_count, int) else 0
        )
        if workflow_error is not None:
            run_status = "failed"
        elif failed_count or permanent_failure_count:
            run_status = "completed_with_failures"
        else:
            run_status = "completed"
        try:
            with performance.stage("report_generation"):
                final_state = ds.load_state()
                report_result = RunReporter().generate(
                    run_id or "unknown-run",
                    start_time=started_at,
                    end_time=datetime.now(timezone.utc),
                    selected_billing_periods=selected_periods,
                    invoices_discovered=found_count,
                    queued=len(queue),
                    completed=completed_count,
                    skipped=already_completed_count,
                    retry_attempts=getattr(recovery_summary, "retry_attempts", 0),
                    successful_recoveries=getattr(recovery_summary, "successful_recoveries", 0),
                    permanent_failures=permanent_failure_count,
                    invoices=_report_invoice_details(
                        report_items, final_state, skipped_keys, recovered_keys,
                        dry_run=dry_run,
                        queued_keys={_item_key(item) for item in queue},
                    ),
                    run_status=run_status,
                    failure_stage=failure_stage if workflow_error is not None else None,
                    error_type=type(workflow_error).__name__ if workflow_error else None,
                    error_message=str(workflow_error) if workflow_error else None,
                    dry_run=dry_run,
                )
            logger.info("Run report generated: %s", report_result["json"])
        except Exception:
            logger.exception("Run report generation failed.")
            if workflow_error is None:
                raise
        # AutoRecovery quits its own driver when run() returns or raises.
        # Only quit here if AutoRecovery never consumed the initial driver
        # (i.e. an exception occurred before ar.run() was reached).
        try:
            if _initial_driver and driver is not None:
                driver.quit()
        finally:
            performance.print_summary()


def main(argv=None):
    args = parse_cli_args(argv)
    run_id = configure_logging()
    logger.info("Starting IBIS Auto Downloader version %s (run %s).", VERSION, run_id)
    if not SCHEDULER_ENABLED:
        # Build 2.6 behaviour: run once and exit.
        scheduler = Scheduler(lambda: _download_workflow(
            args.billing_period, run_id=run_id, dry_run=args.dry_run
        ))
        scheduler.run_once()
        return

    # Build 2.7: use configurable schedule.
    scheduler = Scheduler(
        lambda: _download_workflow(
            args.billing_period, run_id=run_id, dry_run=args.dry_run
        ),
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
