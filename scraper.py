import requests
from bs4 import BeautifulSoup
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

url = "https://www.imsnsit.org/imsnsit/notifications.php"
response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

data = []
cutoff_date = datetime.strptime("29-03-2025", "%d-%m-%Y")

trs = soup.find_all("tr")
for tr in trs:
    tds = tr.find_all("td")
    if len(tds) >= 2:
        date_text = tds[0].text.strip()
        try:
            date_obj = datetime.strptime(date_text, "%d-%m-%Y")
            if date_obj < cutoff_date:
                break
        except ValueError:
            continue
        
        link_tag = tds[1].find("a")
        link = link_tag["href"] if link_tag else ""
        title = link_tag.text.strip() if link_tag else ""
        if not link:
            link = "https://www.imsnsit.org/imsnsit/notifications.php" + " | " + title.strip().lower()
        links = []
        if "drive.google.com/file/d/" in link:
            link = link.replace("drive.google.com/file/d/", "drive.google.com/uc?id=")
        if "drive.google.com/drive/folders" in link:
            folder_id = link.split("folders/")[-1].split("?")[0]
            links = list_public_drive_files(folder_id)
        
        b_tags = tr.find_all("b")
        published_by = b_tags[0].text.replace("Published By: ", "").strip() if b_tags else ""
        
        if len(links) == 0:
            data.append([date_text, link, title, published_by])
        else:
            for link in links:
                data.append([date_text, link, title, published_by])

db = mongo_client["Docs"]
documents_collection = db["documents"]
fetched_links = set(documents_collection.distinct("Link"))
fetched_titles_dates = set(
    (doc["Title"].strip().casefold(), doc["Publish Date"].strftime("%d-%m-%Y")) for doc in documents_collection.find({}, {"Title": 1, "Publish Date": 1})
)

filtered_data = []
for entry in data:
    date_text, link, title, published_by = entry
    if link in fetched_links:
        logging.debug("Skipping file with title: %s (already exists by link)", title)
        continue
    if (title.casefold(), date_text) in fetched_titles_dates:
        logging.debug("Skipping file with title: %s and date: %s (already exists by title-date combination)", title, date_text)
        continue
    else:
        logging.debug("Inserting new file in csv with title: %s and date: %s", title, date_text)
    filtered_data.append(entry)

file_exists = os.path.isfile("output.csv")
df = pd.DataFrame(filtered_data, columns=["Date", "Link", "Title", "Published By"])
df.to_csv("output.csv", mode="a", index=False, header=not file_exists)

logging.info("New data has been appended to the CSV file successfully!")