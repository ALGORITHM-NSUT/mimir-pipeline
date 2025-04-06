import os
import time
import threading
import requests
import re
import gdown
import json
import pandas as pd
from queue import Queue, Empty
from pathlib import Path
from googleapiclient.discovery import build
from pymongo import MongoClient
from google.genai.types import EmbedContentConfig
from pdf2image import convert_from_path, pdfinfo_from_path
from fpdf import FPDF
import math
from PIL import Image
from datetime import datetime
from google import genai
from prompt import GEMINI_PROMPT
from pydantic import BaseModel
from google.genai import types
import random
import hashlib
from dotenv import load_dotenv
import mimetypes
import logging
import csv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename='app.log',   # This directs logs to a file named "app.log"
    filemode='a'          # 'a' for appending to the file
)

load_dotenv()

class innerchunks(BaseModel):
    text: str
    optional_summary: str

class GeminiConfig(BaseModel):
    chunks: list[innerchunks]
    summary: str

download_dir = "downloads"
os.makedirs(download_dir, exist_ok=True)

GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

download_semaphore = threading.Semaphore(1)
gemini_lock = threading.Lock()
last_gemini_time = 0
GEMINI_RPM = 14
GEMINI_INTERVAL = 60 / GEMINI_RPM
MAX_RETRIES = 6
HOURLY_RETRY_INTERVAL = 3600
embedding_lock = threading.Lock()
last_embedding_time = 0
request_interval = 60.0 / 149.0  # Enforces rate limits
download_done = False    
parse_done = False 
total_pages_processed = 0
processed_indexes = set()
llm = "gemini-2.0-flash"

mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client["Docs"]
documents_collection = db["documents"]
chunks_collection = db["chunks"]
failed_collection = db["failed_documents"]

data = pd.read_csv("./output.csv")
headers = {
    "Referer": "https://www.imsnsit.org/imsnsit/notifications.php",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
}


def sanitize_filename(filename):
    sanitized = re.sub(r'[<>:"/\\|?*];', '', filename)
    return sanitized

def get_filename_from_header(response):
    content_disposition = response.headers.get('Content-Disposition', '')
    if 'filename=' in content_disposition:
        filename = content_disposition.split('filename=')[1].strip('"')
        logging.debug(f"Extracted filename from header: {filename}")
        return filename
    return None

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

def get_extension(response, filename):
    # If filename already ends with a common extension, return as-is.
    if filename.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp')):
        return filename.lower()
    content_type = response.headers.get('Content-Type', '').lower()
    if "image" in content_type:
        if "jpeg" in content_type or "jpg" in content_type:
            extension = ".jpg"
        elif "png" in content_type:
            extension = ".png"
        elif "gif" in content_type:
            extension = ".gif"
        else:
            extension = mimetypes.guess_extension(content_type) or ".img"
    else:
        extension = mimetypes.guess_extension(content_type) or ".pdf"
    full_filename = filename + extension
    logging.debug(f"Determined filename with extension: {full_filename}")
    return full_filename

def download_drivefile(file_url, index, fetched_links):
    try:
        response = requests.head(file_url, allow_redirects=True)
        header_filename = get_filename_from_header(response)
        if header_filename:
            filename = header_filename
        else:
            title_hash = hashlib.sha256(data.iloc[index]["Title"][:100].encode('utf-8')).hexdigest()[:8]
            base_filename = f"{data.iloc[index]['Title'][:100]}_{title_hash}"
            filename = get_extension(response, base_filename)
        file_path = os.path.join(download_dir, filename)
        shareurl = "drive.google.com/file/d/" + file_url.split("id=")[1]
        if os.path.exists(file_path):
            logging.info(f"File {filename} exists, skipping download.")
            download_queue.put((filename, index, shareurl))
            return
        
        if shareurl in fetched_links:
            logging.info(f"File {filename} already processed (Drive), skipping download.")
            download_semaphore.release()
            return
        gdown.download(file_url, file_path, quiet=False)
        logging.info(f"Downloaded Drive file {filename} with URL: {file_url}")
        download_queue.put((filename, index, shareurl))
    except Exception as e:
        logging.error(f"Failed to download Drive file {file_url}: {e}")
        download_semaphore.release()

def download_file(url, session, headers, index, fetched_links):
    logging.debug(f"Acquiring semaphore for download_file")
    download_semaphore.acquire()
    try:        
        if url == "" or (isinstance(url, float) and math.isnan(url)) or "https://docs.google.com/forms" in url:
            logging.info(f"⬇️ Downloading file with URL: {url}")
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", size=12)
            pdf_text = data.iloc[index]['Title']
            if "https://docs.google.com/forms" in url:
                pdf_text += "\nGoogle Form Link: " + url
            pdf.cell(200, 10, txt=data.iloc[index]["Title"], ln=True, align='C')
            file_name = sanitize_filename(f"{data.iloc[index]['Title'][:100]}.pdf")
            file_path = os.path.join(download_dir, file_name)
            if os.path.exists(file_path):
                logging.info(f"File {file_name} exists, skipping creation.")
                download_queue.put((file_name, index, ""))
                return
            pdf.output(file_path)
            logging.info(f"PDF saved: {file_path}")
            download_queue.put((file_name, index, ""))
        elif "drive.google.com/uc?id=" in url:
            download_drivefile(file_url=url, index=index, fetched_links=fetched_links)
        elif "drive.google.com/file/d/" in url:
            file_id = url.split('/d/')[1].split('/')[0]
            file_url = f"https://drive.google.com/uc?id={file_id}"
            download_drivefile(file_url=file_url, index=index, fetched_links=fetched_links)
        elif "drive.google.com/drive/folders" in url:
            logging.info(f"Checking Google Drive folder: {url}")
            folder_id = url.split("folders/")[-1].split("?")[0]
            file_links = list_public_drive_files(folder_id)
            download_semaphore.release()  # Release current slot before processing folder links.
            for link in file_links:
                download_file(link, session, headers, index, fetched_links)
        elif "https://www.imsnsit.org/" in url:
            logging.info(f"⬇️ Downloading file with URL: {url}")
            response = session.get(url, headers=headers, stream=True, timeout=120)
            response.raise_for_status()
            filename = get_filename_from_header(response) or os.path.basename(url)
            filename = sanitize_filename(filename)
            filename = get_extension(response, filename)
            file_path = os.path.join(download_dir, filename)
            if os.path.exists(file_path):
                logging.info(f"File {filename} exists, skipping download.")
                download_queue.put((filename, index, ""))
                return
            with open(file_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            logging.info(f"Downloaded file: {filename} with URL: {url}")
            download_queue.put((filename, index, ""))
        else:
            logging.warning(f"Unknown URL format: {url}")
            download_semaphore.release()
            return
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download {url}: {e}")
        download_semaphore.release()

def generate_metadata(filename, raw_text, page_summary_text, index, num_pages, url):
    global last_gemini_time
    try:
        with gemini_lock:
            current_time = time.time()
            elapsed = current_time - last_gemini_time
            if elapsed < GEMINI_INTERVAL:
                time.sleep(GEMINI_INTERVAL - elapsed)
            last_gemini_time = time.time()
        if num_pages > 1:
            summary = _call_llm(page_summary_text)
        else:
            summary = page_summary_text
        metadata = {}
        metadata["content"] = raw_text
        publishing = str(data.iloc[index]["Published By"]).split(',')
        metadata["Publish Date"] = datetime.strptime(str(data.iloc[index]["Date"]).lower(), '%d-%m-%Y')
        metadata["Title"] = str(data.iloc[index]["Title"]).strip().lower()
        if len(publishing) == 3:
            metadata["Published By"] = publishing[0].strip().lower()
            metadata["Publishing Post"] = publishing[1].strip().lower()
            metadata["Publishing Department"] = publishing[2].strip().lower()
        else:
            metadata["Published By"] = publishing[0].strip().lower()
            metadata["Publishing Department"] = publishing[1].strip().lower()
        metadata["doc_id"] = filename
        if url != "":
            link = url
        else:
            link = str(data.iloc[index]["Link"]).strip()
            if not link:
                link = "https://www.imsnsit.org/imsnsit/notifications.php" + " | " + str(data.iloc[index]["Title"]).strip().lower()
        metadata["Link"] = link
        logging.info(f"Metadata successfully generated for {filename}.")
        metadata["summary"] = f'''
Title: {metadata["Title"]}  
Published on: {metadata["Publish Date"]}  
Published by: {metadata["Published By"]} ({metadata["Publishing Department"]}, {metadata.get("Publishing Post", "")})  

Summary: {summary}'''
        metadata["Pages"] = num_pages
        return metadata

    except Exception as e:
        logging.error(f"Metadata generation failed for {filename}: {e}")
        raise

def _call_llm(content):
    prompt = f"""You are provided with summaries of pages from a university circular.
Each page summary is separated by two newlines.

Please analyze these page summaries and generate a comprehensive yet concise overall summary of the circular.
Include key announcements, important dates, involved departments, and any significant changes mentioned.
Return only the final summary and nothing else.

Page Summaries:
{content}"""
    attempts = 0
    global last_gemini_time
    while True:
        with gemini_lock:
            current_time = time.time()
            elapsed = current_time - last_gemini_time
            if elapsed < GEMINI_INTERVAL:
                time.sleep(GEMINI_INTERVAL - elapsed)
            
            last_gemini_time = time.time()
            try:
                response = client.models.generate_content(
                    model=llm,
                    contents=[prompt],
                    config=types.GenerateContentConfig(temperature=0.2)
                ).text
                if response:
                    logging.debug("LLM returned a summary response successfully.")
                    return response
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "rate limit" in error_msg or "quota exceeded" in error_msg:
                    logging.warning(f"Rate limit hit in summarization, attempt {attempts + 1}. Retrying...")
                else:
                    logging.warning(f"API call failed in summarization: {error_msg}. Retrying...")
                attempts += 1
                if attempts >= MAX_RETRIES:
                    if "429" in error_msg or "rate limit" in error_msg or "quota exceeded" in error_msg:
                        logging.error(f"Max retries reached in image_to_markdown ({MAX_RETRIES}). Retrying every hour...")
                        time.sleep(HOURLY_RETRY_INTERVAL)
                    else:
                        logging.info(f"failing pdf in summarization...")
                        raise
                else:
                    wait_time = exponential_backoff(attempts)
                    logging.info(f"Waiting {wait_time:.2f} seconds before retrying image_to_markdown...")
                    time.sleep(wait_time)

def exponential_backoff(attempts):
    return min(2 ** attempts + random.uniform(0, 1), HOURLY_RETRY_INTERVAL)

def image_to_markdown(image, previous_markdown, first_summary, index, failed_markdown, attempt_markdown):
    global last_gemini_time
    with gemini_lock:
        current_time = time.time()
        elapsed = current_time - last_gemini_time
        if elapsed < GEMINI_INTERVAL:
            time.sleep(GEMINI_INTERVAL - elapsed)
        
        last_gemini_time = time.time()
    assisting_prompt = f"""Title of Document: {data.iloc[index]["Title"]}"""
    if previous_markdown:
            assisting_prompt = f"""first page summary: '{first_summary}'
        previous page markdown: 
        {previous_markdown}"""
    llm = "gemini-2.0-flash"
    if failed_markdown:
        assisting_prompt += "\n" + f"previous attempt was unsuccessful attempt for this page, do not follow this, give correct JSON output: \n failed attemot = {failed_markdown}"
        if attempt_markdown <= 3:
            llm = "gemini-2.0-pro-exp-02-05"
        else:
            llm == "gemini-2.0-flash-thinking-exp-01-21"
        
    attempts = 0
    while True:
        try:
            if llm == "gemini-2.0-flash" or llm == "gemini-2.0-pro-exp-02-05":
                config=types.GenerateContentConfig(
                        system_instruction=GEMINI_PROMPT,
                        response_mime_type='application/json',
                        response_schema=GeminiConfig,
                        temperature=0.1
                    )
            else:
                config=types.GenerateContentConfig(
                        system_instruction=GEMINI_PROMPT,
                        temperature=0.1
                    )
            response = client.models.generate_content(
                model=llm,
                contents=[image, assisting_prompt],
                config=config
            ).text
            if response:
                logging.debug("Image to markdown conversion successful.")
                return response
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate limit" in error_msg or "quota exceeded" in error_msg:
                logging.warning(f"Rate limit hit in image_to_markdown, attempt {attempts + 1}. Retrying...")
                if failed_markdown:
                    if llm == "gemini-2.0-pro-exp-02-05":
                        llm = "gemini-2.0-flash-thinking-exp-01-21"
                        
                    elif llm == "gemini-2.0-flash-thinking-exp-01-21":
                        llm = "gemini-2.0-pro-exp-02-05"
            else:
                logging.warning(f"API call failed in image_to_markdown: {error_msg}. Retrying...")
            attempts += 1
            if attempts >= MAX_RETRIES:
                if "429" in error_msg or "rate limit" in error_msg or "quota exceeded" in error_msg:
                    logging.error(f"Max retries reached in image_to_markdown ({MAX_RETRIES}). Retrying every hour...")
                    time.sleep(HOURLY_RETRY_INTERVAL)
                else:
                    logging.info(f"failing pdf in image_to_markdown...")
                    raise
            else:
                wait_time = exponential_backoff(attempts)
                logging.info(f"Waiting {wait_time:.2f} seconds before retrying image_to_markdown...")
                time.sleep(wait_time)


def parse_pdf(pdf_path, filename, index, url):
    global total_pages_processed
    try:
        logging.info(f"Parsing PDF: {filename}")

        # Get the number of pages without converting all pages at once
        pdf_info = pdfinfo_from_path(pdf_path)
        num_pages = pdf_info["Pages"]
        logging.info(f"{pdf_path} contains {num_pages} pages.")

        markdown_outputs = []
        previous_markdown = ""
        first_summary = ""
        page_summaries = []
        raw_text = ""

        # Process one page at a time to save memory
        for i in range(1, num_pages + 1):
            # Convert only the current page
            images = convert_from_path(pdf_path, dpi=300, first_page=i, last_page=i)
            if not images:
                logging.error(f"Failed to extract image for page {i} of {filename}. Skipping.")
                page_summaries.append('<error>')
                continue
            
            # Retry logic for JSON extraction
            markdown = ""
            json_response = None
            failed_markdown = ""
            for attempt in range(MAX_RETRIES):
                markdown = image_to_markdown(images[0], previous_markdown, first_summary, index, failed_markdown, attempt)
                
                if not markdown.strip() or "No markdown extracted." in markdown:
                    logging.error(f"Couldn't generate markdown for page {i} of {filename}. Aborting entire PDF processing.")
                    return [], 0, {}
                else:
                    try:
                        markdown = markdown[markdown.find('{') : markdown.rfind('}') + 1]
                        json_response = json.loads(markdown, strict=False)
                        break  # Successfully parsed JSON; exit retry loop
                    except json.JSONDecodeError as e:
                        failed_markdown = markdown
                        logging.error(f"JSON decode error for {filename} on page {i}: {e}")
                
                time.sleep(exponential_backoff(attempt))  # Exponential backoff before retrying

            # If JSON parsing failed after retries, handle accordingly
            if json_response is None:
                logging.error(f"Page {i} of {filename} failed after {MAX_RETRIES} attempts.")
                failed_collection.insert_one({
                    "doc_id": filename,
                    "page": i,
                    "error": failed_markdown,
                    "timestamp": datetime.now(),
                    "url": url
                })
                # Abort the entire PDF if the first page fails
                logging.error("Aborting entire PDF processing.")
                return [], 0, {}

            if i == 1:
                first_summary = json_response['summary']
            previous_markdown = markdown
            markdown_outputs.append((i, json_response))
            page_summaries.append(json_response["summary"])

            # Append text to raw_text
            raw_text += f"\n\nPage: {i}"
            for chunk in json_response["chunks"]:
                raw_text += f"\n{chunk['text']}"

            # Free memory after processing the page
            del images  

        page_summary_text = "\n\n".join(page_summaries)
        metadata = generate_metadata(filename, raw_text, page_summary_text, index, num_pages, url)
        return markdown_outputs, num_pages, metadata

    except Exception as e:
        logging.error(f"Parsing failed for {pdf_path}: {e}")
        return [], 0, {}



def parse_image(image_path, filename, index, url):
    global total_pages_processed
    try:
        logging.info(f"Parsing image: {filename}")
        image = Image.open(image_path)
        failed_markdown = ""
        for attempt in range(MAX_RETRIES):
            markdown = image_to_markdown(image, "", "", index, failed_markdown, attempt)
            
            if not markdown.strip() or "No markdown extracted." in markdown:
                logging.error(f"Couldn't generate markdown for page 1 of {filename}. Aborting Image processing.")
                return [], 0, {}
            else:
                try:
                    markdown = markdown[markdown.find('{') : markdown.rfind('}') + 1]
                    json_response = json.loads(markdown, strict=False)
                    break  # Successfully parsed JSON; exit retry loop
                except json.JSONDecodeError as e:
                    failed_markdown = markdown
                    logging.error(f"JSON decode error for {filename} on page 1: {e}")
            
            time.sleep(exponential_backoff(attempt))
        
        raw_text = ""
        for chunk in json_response["chunks"]:
            raw_text += chunk["text"] + "\n"
        if not markdown.strip() or "No markdown extracted." in markdown:
            logging.error(f"Image {image_path} failed to parse. Aborting.")
            return [], 0, {}
        metadata = generate_metadata(filename, raw_text, json_response["summary"], index, 1, url)
        
        return [(1, json_response)], 1, metadata
    except Exception as e:
        logging.error(f"Parsing failed for image {image_path}: {e}")
        return [], 0, {}

def process_file(filename, index, url):
    try:
        file_path = os.path.join(download_dir, filename)
        logging.info(f"Processing file: {filename}")
        if filename.lower().endswith('.pdf'):
            chunks, num_pages, metadata = parse_pdf(file_path, filename, index, url)
        elif filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
            chunks, num_pages, metadata = parse_image(file_path, filename, index, url)
        else:
            logging.warning(f"Unsupported file type for {filename}, skipping parsing.")
            os.remove(file_path)
            download_semaphore.release()
            return
        if num_pages > 0:
            logging.info(f"Parsed {filename} successfully.")
            parsed_queue.put((filename, chunks, metadata))
            os.remove(file_path)
            logging.info(f"Deleted processed file: {filename}")
        else:
            os.remove(file_path)
            logging.info(f"Deleted unprocessed file: {filename}")
        download_semaphore.release()
    except Exception as e:
        logging.error(f"Processing failed for file {filename}: {e}")
        download_semaphore.release()

def start_parse():
    while not (download_done and download_queue.empty()):
        try:
            filename, index, url = download_queue.get(timeout=5)
            logging.debug(f"Submitting process_file task for {filename}")
            process_file(filename, index, url)
            download_queue.task_done()
        except Empty:
            continue

def get_embedding(text):
    global last_embedding_time
    retries = 5
    retry_delay = 5
    for attempt in range(retries + 1):
        with embedding_lock:
            current_time = time.time()
            elapsed = current_time - last_embedding_time
            if elapsed < request_interval:
                time.sleep(request_interval - elapsed)
            last_embedding_time = time.time()
        try:
            response = client.models.embed_content(
                model="text-embedding-004",
                contents=[text],
                config=EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
            )
            embedding = response.embeddings[0].values
            return embedding
        except Exception as e:
            if attempt < retries:
                logging.warning(f"Embedding error (attempt {attempt+1}/{retries+1}): {e}. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logging.error(f"Embedding failed after {retries+1} attempts: {e}")
                raise

def get_embeddings_batch(texts):
    global last_embedding_time
    retries = 5
    retry_delay = 5
    for attempt in range(retries + 1):
        with embedding_lock:
            current_time = time.time()
            elapsed = current_time - last_embedding_time
            if elapsed < request_interval:
                time.sleep(request_interval - elapsed)
            last_embedding_time = time.time()
        try:
            response = client.models.embed_content(
                model="text-embedding-004",
                contents=texts,
                config=EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
            )
            embeddings = [embedding.values for embedding in response.embeddings]
            return embeddings
        except Exception as e:
            if attempt < retries:
                logging.warning(f"Embedding batch error (attempt {attempt+1}/{retries+1}): {e}. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logging.error(f"Embedding batch failed after {retries+1} attempts: {e}")
                raise

def extract_words(text):
    text = text.replace('\n', ' ')
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
    pattern = r'\b(?=\S*[A-Za-z])([A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*)\b'
    words_found = re.findall(pattern, text)
    return words_found

def vectorize(chunks, doc):
    try:
        summary_embedding = get_embedding(doc["summary"])
        min_word_chunk = 50
        result_chunks = []  
        page_summaries = []
        for page, page_chunks in chunks:
            if len(page_summaries) < page:
                page_summaries.append(page_chunks["summary"])
            for chunk in page_chunks["chunks"]:
                current_text = chunk["text"].strip()
                if ("optional_summary" in chunk and chunk["optional_summary"] and chunk["optional_summary"] is not None
                    and chunk["optional_summary"].strip().lower() not in ["null", "none"]):
                    if re.match(r"^\s*\|", current_text):
                        current_text = chunk["optional_summary"] + "\n" + current_text
                    embedding_text = chunk["optional_summary"] + "\n" + page_chunks["summary"]
                    new_chunk = (page, current_text, embedding_text, True, chunk["optional_summary"])
                else:
                    embedding_text = current_text  # Summary will be appended after merging.
                    new_chunk = (page, current_text, embedding_text, False, "")
                words = extract_words(current_text)
                if (not new_chunk[3]) and (len(words) < min_word_chunk) and result_chunks and (result_chunks[-1][0] == page):
                    prev_page, prev_text, prev_embedding, prev_is_table, prev_summary = result_chunks[-1]
                    combined_text = prev_text + " " + current_text
                    combined_embedding = combined_text + "\n" + page_chunks["summary"]
                    result_chunks[-1] = (prev_page, combined_text, combined_embedding, prev_is_table, prev_summary)
                else:
                    if not new_chunk[3]:
                        new_chunk = (page, current_text, current_text + "\n" + page_chunks["summary"], False, "")
                    result_chunks.append(new_chunk)
        for i in range(1, len(result_chunks)):
            prev_page, prev_text, _, prev_is_table, prev_summary = result_chunks[i-1]
            curr_page, curr_text, curr_embedding, curr_is_table, curr_summary = result_chunks[i]
            if curr_page == prev_page and not prev_is_table:
                prev_words = prev_text.split()
                if prev_words:
                    overlap_count = max(1, len(prev_words) // 4)
                    overlap_text = " ".join(prev_words[-overlap_count:])
                    new_curr_text = overlap_text + "\n\n" + curr_text
                    new_curr_embedding = overlap_text + "\n\n" + curr_embedding
                    result_chunks[i] = (curr_page, new_curr_text, new_curr_embedding, curr_is_table, curr_summary)
        unique_chunks = []
        seen_texts = set()
        for chunk in result_chunks:
            chunk_text = chunk[1]
            if chunk_text not in seen_texts:
                unique_chunks.append(chunk)
                seen_texts.add(chunk_text)
        result_chunks = unique_chunks
        num_chunks = len(result_chunks)
        chunk_embeddings = [None] * num_chunks
        batch_size = 10
        for i in range(0, num_chunks, batch_size):
            batch_end = min(i + batch_size, num_chunks)
            batch_texts = [result_chunks[j][2] for j in range(i, batch_end)]
            batch_indices = list(range(i, batch_end))
            batch_embeddings = get_embeddings_batch(batch_texts)
            for idx, embedding in zip(batch_indices, batch_embeddings):
                chunk_embeddings[idx] = embedding
        document_entry = {
            **doc,
            "summary_embedding": summary_embedding,
            "page_summaries": page_summaries
        }
        result = documents_collection.insert_one(document_entry)
        inserted_id = result.inserted_id
        chunk_entries = []
        for i, (page, chunk_text, _, tabular, table_summary) in enumerate(result_chunks):
            chunk_entry = {
                "chunk_id": f"{doc['doc_id']}_chunk_{i+1}",
                "doc_id": inserted_id,
                "text": chunk_text,
                "embedding": chunk_embeddings[i],
                "page": int(page),
                "chunk_num": i + 1,
                "tabular": tabular
            }
            if table_summary:
                chunk_entry["table_summary"] = table_summary
            chunk_entries.append(chunk_entry)
        chunks_collection.insert_many(chunk_entries)
        logging.info("Vectorization completed successfully.")
        return True
    except Exception as e:
        logging.error(f"Vectorization completely failed for {doc['doc_id']}: {e}")
        if 'inserted_id' in locals() and inserted_id:
            documents_collection.delete_one({"_id": inserted_id})
        raise

def start_vector():
    while not (parse_done and parsed_queue.empty()):
        try:
            filename, chunks, metadata = parsed_queue.get(timeout=5)
            logging.info(f"Vectorizing: {filename}")
            try:
                vectorize(chunks, metadata)
                logging.info(f"Successfully vectorized: {filename}")
            except Exception as e:
                logging.error(f"Vectorization process failed for {filename}: {e}. No data stored.")
                failed_collection.insert_one({
                    **metadata
                })
                continue
        except Empty:
            continue

download_queue = Queue()
parsed_queue = Queue()
parsing_thread = threading.Thread(target=start_parse)
parsing_thread.start()
vectorizing_thread = threading.Thread(target=start_vector)
vectorizing_thread.start()
processed_indexes = set()
try:
    fetched_links = set(documents_collection.distinct("Link"))
    fetched_titles_dates = set(
        (doc["Title"].strip().casefold(), doc["Publish Date"].strftime("%d-%m-%Y")) for doc in documents_collection.find({}, {"Title": 1, "Publish Date": 1})
    )
    logging.info(f"Already processed links: {len(fetched_links)}")
    session = requests.Session()
    for index, row in data.iterrows():
        link_value = str(row["Link"]).strip()
        if not link_value:
            link_value = "https://www.imsnsit.org/imsnsit/notifications.php" + " | " + str(row["Title"]).strip().lower()
        if link_value in fetched_links and "drive.google.com/drive/folders" not in link_value:
            logging.info(f"Skipping {row['Title']} as it's already processed.")
            continue
        if (row["Title"].strip().casefold(), row["Date"]) in fetched_titles_dates and "drive.google.com/drive/folders" not in link_value:
            logging.info(f"Skipping {row['Title']} as it's already processed.")
        download_file(row["Link"], session, headers, index, fetched_links)
    download_done = True
    parsing_thread.join()
    parse_done = True
    vectorizing_thread.join()
    logging.info(f"Total pages processed: {total_pages_processed}")
    logging.info("🎉 All processes completed successfully!")
except KeyboardInterrupt:
    logging.warning("Process interrupted by user. Cleaning up...")
    download_done = True
    parse_done = True
    parsing_thread.join()
    vectorizing_thread.join()
    exit()
except Exception as e:
    logging.error(f"Main process error: {e}")
    exit()
