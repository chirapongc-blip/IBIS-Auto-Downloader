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

from ibis.browser import create_driver
from ibis.downloader import build_download_queue
from ibis.downloader_engine import DownloaderEngine
from ibis.grid_walker import collect_grid_download_links, get_devexpress_pager_info
from ibis.invoice import open_invoice_page
from ibis.grid import wait_for_grid, get_grid_text, count_grid_rows
from ibis.login import wait_until_logged_in
from ibis.scheduler import DownloadPlan, Scheduler
from ibis.period_tracker import PeriodTracker
from ibis.state_manager import StateManager

logger = logging.getLogger(__name__)


def _download_workflow():
    """Execute the full Build 2.5 download workflow (one pass)."""

    driver = create_driver()

    try:
        driver.get(BASE_URL)

        print("请在浏览器完成登录...")
        wait_until_logged_in(driver)

        print("登录成功。")
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
        state_manager = StateManager()
        queue_result = build_download_queue(all_links, state_manager=state_manager)
        queue = queue_result.queue

        print(f"Found invoices: {queue_result.found_count}")
        print(f"Already completed: {queue_result.already_completed_count}")
        print(f"Download Queue: {len(queue)}")

        plan = DownloadPlan(queue, latest_only=False)
        print(f"下载计划已建立，共 {plan.scheduled_count} 个项目。")

        engine = DownloaderEngine(driver, state_manager=state_manager)
        period_tracker = PeriodTracker()
        current_period = queue_result.latest_billing_period or plan.latest_billing_period
        engine.run(plan)
        if engine.summary.failed == 0 and current_period is not None:
            if len(queue) == 0:
                if not period_tracker.last_period_file_exists():
                    period_tracker.save_last_period(current_period)
            elif engine.summary.completed == plan.scheduled_count:
                period_tracker.save_last_period(current_period)

    finally:
        driver.quit()


def main():
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
