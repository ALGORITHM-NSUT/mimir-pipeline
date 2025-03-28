from selenium import webdriver
from selenium.webdriver.common.by import By
import pandas as pd
from datetime import datetime
from pymongo import MongoClient
from googleapiclient.discovery import build
import os
import logging
mongo_client = MongoClient(os.getenv("MONGO_URI"))

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename='flask_app.log',
    filemode='a'
)

mongo_client = MongoClient(os.getenv("MONGO_URI"))
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

def list_public_drive_files(folder_id):
    logging.info(f"Listing public drive files in folder: {folder_id}")
    service = build("drive", "v3", developerKey=os.getenv("DRIVE_API"))
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get("files", [])
    file_links = []
    for file in files:
        if file["mimeType"] == "application/vnd.google-apps.folder":
            file_links.extend(list_public_drive_files(file["id"]))
        else:
            file_links.append(f"https://drive.google.com/uc?id={file['id']}")
    logging.debug(f"Found {len(file_links)} files in folder {folder_id}")
    return file_links

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
        links = []
        if "drive.google.com/file/d/" in link:
            link = link.replace("drive.google.com/file/d/", "drive.google.com/uc?id=")
        if "drive.google.com/drive/folders" in url:
            folder_id = url.split("folders/")[-1].split("?")[0]
            links = list_public_drive_files(folder_id)
        # Handle alternative tag structure if link_tag is not found
        b_tags = tr.find_elements(By.TAG_NAME, "b")
        if not link_tag and len(b_tags) >= 2:
            title = b_tags[0].text.strip()
            published_by = b_tags[1].text.replace("Published By: ", "").strip()
        else:
            published_by = b_tags[0].text.replace("Published By: ", "").strip() if b_tags else ""
        if len(links) == 0:
            data.append([date_text, link, title, published_by])
        else:
            for link in links:
                data.append([date_text, link, title, published_by])

# Close the browser
driver.quit()

db = mongo_client["Docs"]
documents_collection = db["documents"]
fetched_links = set(documents_collection.distinct("Link"))

filtered_data = []

for tuple in data:
    if tuple[1] not in fetched_links:
        filtered_data.append(tuple)
    else:
        logging.debug("Skipping a file with title : %s", tuple[2])

# Create a DataFrame
# Check if file exists
file_exists = os.path.isfile("output.csv")

# Create a DataFrame
df = pd.DataFrame(filtered_data, columns=["Date", "Link", "Title", "Published By"])

# Append to CSV without overwriting, and avoid adding headers again if the file exists
df.to_csv("output.csv", mode="a", index=False, header=not file_exists)

logging.info("New data has been appended to the CSV file successfully!")
