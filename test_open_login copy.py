from ibis.browser import create_driver

driver = create_driver()

driver.get("https://stationsatcom.satcomhost.com/")

input("如果已经打开登录页面，请按 Enter...")

driver.quit()
