from selenium import webdriver
from selenium.webdriver.common.by import By
import pandas as pd
from datetime import datetime

options = webdriver.ChromeOptions()
options.add_argument('--headless')
driver = webdriver.Chrome(options=options)
url = "https://www.imsnsit.org/imsnsit/notifications.php"

# Load the webpage
driver.get(url)
elem = driver.find_element(By.NAME, "olddata")
elem.click()

# Set cutoff date to only parse notifications from 17-02-2025
cutoff_date = datetime.strptime("26-03-2025", "%d-%m-%Y")

# List to store extracted data
data = []

# Iterate through each tr tag
trs = driver.find_elements(By.TAG_NAME, "tr")
for tr in trs:
    tds = tr.find_elements(By.TAG_NAME, "td")
    if len(tds) >= 2:
        date_text = tds[0].text  # Extract date
        try:
            date_obj = datetime.strptime(date_text, "%d-%m-%Y")
            # If the date is before the cutoff date, stop processing further rows.
            if date_obj < cutoff_date:
                break
        except ValueError:
            continue
        
        # Extract the first hyperlink (if available)
        link_tag = tds[1].find_element(By.TAG_NAME, "a") if tds[1].find_elements(By.TAG_NAME, "a") else None
        link = link_tag.get_attribute("href") if link_tag else ""
        title = link_tag.text.strip() if link_tag else ""
        
        # Handle alternative tag structure if link_tag is not found
        b_tags = tr.find_elements(By.TAG_NAME, "b")
        if not link_tag and len(b_tags) >= 2:
            title = b_tags[0].text.strip()
            published_by = b_tags[1].text.replace("Published By: ", "").strip()
        else:
            published_by = b_tags[0].text.replace("Published By: ", "").strip() if b_tags else ""
        
        data.append([date_text, link, title, published_by])

# Close the browser
driver.quit()

# Create a DataFrame
df = pd.DataFrame(data, columns=["Date", "Link", "Title", "Published By"])

# Save to CSV
df.to_csv("output.csv", index=False)

print("CSV file has been created successfully!")
