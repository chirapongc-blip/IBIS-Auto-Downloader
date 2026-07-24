import logging
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
from ibis.downloader import build_download_queue
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
from logger import configure_logging

logger = logging.getLogger(__name__)


def _download_workflow():
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
            queue = build_resume_queue(saved_state)
            latest_billing_period = saved_state.get("billing_period")
            already_completed_count = len(saved_state.get("completed", []))
            found_count = len(saved_state.get("queue", []))
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
            queue_result = build_download_queue(all_links, state_manager=state_manager)
            queue = queue_result.queue
            latest_billing_period = queue_result.latest_billing_period
            found_count = queue_result.found_count
            already_completed_count = queue_result.already_completed_count

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
        ar.run(plan)

        engine = last_engine[0]
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


def main():
    run_id = configure_logging()
    logger.info("Starting IBIS Auto Downloader version %s (run %s).", VERSION, run_id)
    if not SCHEDULER_ENABLED:
        # Build 2.6 behaviour: run once and exit.
        scheduler = Scheduler(_download_workflow)
        scheduler.run_once()
        return

    # Build 2.7: use configurable schedule.
    scheduler = Scheduler(
        _download_workflow,
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
    main()
