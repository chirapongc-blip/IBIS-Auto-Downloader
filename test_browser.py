from ibis.browser import create_driver

if __name__ == "__main__":
    print("Step 1: Creating Chrome driver...")

    driver = create_driver()

    print("Step 2: Chrome driver created.")

    print("Title:", repr(driver.title))

    input("Chrome 已启动。按 Enter 关闭浏览器...")

    driver.quit()

    print("Step 3: Finished.")
