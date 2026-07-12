from ibis.browser import create_driver

driver = create_driver()

driver.get("https://stationsatcom.satcomhost.com/")

input("登录完成后，请回到这里按 Enter...")

print("Current URL:")
print(driver.current_url)

print()

print("Page title:")
print(driver.title)

driver.quit()
