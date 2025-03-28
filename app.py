from flask import Flask, jsonify, request, render_template
import subprocess
import os
import logging
import threading
from datetime import datetime
from flask_apscheduler import APScheduler
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from werkzeug.utils import secure_filename
import pandas as pd
import queue

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename='flask_app.log',
    filemode='a'
)

app = Flask(__name__)
scheduler = APScheduler()

# Track job status
job_status = {
    "scraping": "idle",
    "processing": "idle",
    "last_run": None,
    "next_run": None,
    "error": None,
    "auto_mode": False
}

# Create a queue for pending uploads
upload_queue = queue.Queue()

# Google Drive configuration
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'mimir-455115-bc19e23d8f38.json'  # Path to your service account key
FOLDER_ID = '1zE8tVN5yYsqQBLf5XQqfG5fhPm4DiY_g'  # Replace with your folder ID
ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png'}

def allowed_file(filename):
    return filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Function to authenticate with Google Drive
def get_drive_service():
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=credentials)
        return service
    except Exception as e:
        logging.error(f"Error authenticating with Google Drive: {str(e)}")
        return None

# Function to upload file to Google Drive
def upload_to_drive(file_path, filename):
    try:
        service = get_drive_service()
        if not service:
            return None
            
        file_metadata = {
            'name': filename,
            'mimeType': 'application/pdf',
            'parents': [FOLDER_ID]
        }
        
        media = MediaFileUpload(file_path, mimetype='application/pdf')
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        # Make the file publicly accessible
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }
        service.permissions().create(
            fileId=file.get('id'),
            body=permission
        ).execute()
        
        # Return the direct link to the file
        file_id = file.get('id')
        download_link = f"https://drive.google.com/uc?id={file_id}"
        
        logging.info(f"File uploaded successfully to Google Drive with ID: {file_id}")
        return download_link
    
    except Exception as e:
        logging.error(f"Error uploading to Google Drive: {str(e)}")
        return None

# Function to add entry to output.csv
def add_to_csv(date, link, title, published_by):
    try:
        # Check if file exists
        if os.path.exists('output.csv'):
            df = pd.read_csv('output.csv')
        else:
            df = pd.DataFrame(columns=["Date", "Link", "Title", "Published By"])
        
        # Add new row
        new_row = pd.DataFrame({
            "Date": [date],
            "Link": [link],
            "Title": [title],
            "Published By": [published_by]
        })
        
        df = pd.concat([new_row, df], ignore_index=True)
        df.to_csv('output.csv', index=False)
        logging.info(f"Added entry to CSV: {title}")
        return True
    
    except Exception as e:
        logging.error(f"Error adding to CSV: {str(e)}")
        return False

# Function to process the upload queue
def process_upload_queue():
    global upload_queue
    
    while not upload_queue.empty():
        upload_data = upload_queue.get()
        
        link = upload_data['link']
        filename = upload_data['filename']
        date = upload_data['date']
        title = upload_data['title']
        published_by = upload_data['published_by']
        
        
        if link:
            # Add to CSV
            add_to_csv(date, link, title, published_by)
            
        upload_queue.task_done()


def run_scraper():
    """Run the web scraper script"""
    global job_status
    try:
        job_status["scraping"] = "running"
        job_status["error"] = None
        logging.info("Starting web scraper...")
        
        result = subprocess.run(
            ["python", "new.py"], 
            check=True,
            capture_output=True,
            text=True
        )
        
        logging.info(f"Scraper completed: {result.stdout}")
        job_status["scraping"] = "completed"
        process_upload_queue()
        # Start the pipeline processing
        run_pipeline()
    except subprocess.CalledProcessError as e:
        error_msg = f"Scraper failed: {e.stderr}"
        logging.error(error_msg)
        job_status["scraping"] = "failed"
        job_status["error"] = error_msg
    except Exception as e:
        error_msg = f"Unexpected error in scraper: {str(e)}"
        logging.error(error_msg)
        job_status["scraping"] = "failed"
        job_status["error"] = error_msg

def run_pipeline():
    """Run the revamped pipeline script"""
    global job_status
    try:
        job_status["processing"] = "running"
        logging.info("Starting pipeline processing...")
        
        result = subprocess.run(
            ["python", "revamped_pipeline.py"], 
            check=True,
            capture_output=True,
            text=True
        )
        
        logging.info(f"Pipeline completed: {result.stdout}")
        os.remove("output.csv")
        job_status["processing"] = "completed"
        job_status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except subprocess.CalledProcessError as e:
        error_msg = f"Pipeline failed: {e.stderr}"
        logging.error(error_msg)
        job_status["processing"] = "failed"
        job_status["error"] = error_msg
    except Exception as e:
        error_msg = f"Unexpected error in pipeline: {str(e)}"
        logging.error(error_msg)
        job_status["processing"] = "failed"
        job_status["error"] = error_msg

def scheduled_job():
    """Function to be scheduled for automatic execution"""
    global job_status
    
    # Skip if a job is already running
    if job_status["scraping"] == "running" or job_status["processing"] == "running":
        logging.info("Scheduled job skipped - another job is already running")
        return
    
    logging.info("Running scheduled pipeline job")
    # Start the scraper in a separate thread to avoid blocking the scheduler
    thread = threading.Thread(target=run_scraper)
    thread.daemon = True
    thread.start()
    
    # Update next run time
    update_next_run_time()

def update_next_run_time():
    """Update the next scheduled run time in job_status"""
    global job_status
    next_job = scheduler.get_job('hourly_pipeline')
    if next_job and next_job.next_run_time:
        job_status["next_run"] = next_job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")

@app.route('/', methods=['GET'])
def index():
    """Render the main dashboard page"""
    return render_template('index.html', status=job_status)

@app.route('/status', methods=['GET'])
def get_status():
    """Get the current status of the pipeline jobs"""
    return jsonify(job_status)

@app.route('/start', methods=['POST'])
def start_pipeline():
    """Start the pipeline process manually"""
    global job_status
    
    # Check if a job is already running
    if job_status["scraping"] == "running" or job_status["processing"] == "running":
        return jsonify({
            "status": "error",
            "message": "A job is already running"
        }), 409
    
    # Reset status
    job_status["scraping"] = "idle"
    job_status["processing"] = "idle"
    job_status["error"] = None
    
    # Start the scraper in a separate thread
    thread = threading.Thread(target=run_scraper)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "status": "success",
        "message": "Pipeline started manually"
    })

@app.route('/toggle-auto', methods=['POST'])
def toggle_auto_mode():
    """Toggle automatic scheduling on/off"""
    global job_status
    
    if job_status["auto_mode"]:
        # Turn off automatic mode
        scheduler.pause_job('hourly_pipeline')
        job_status["auto_mode"] = False
        job_status["next_run"] = None
        message = "Automatic scheduling disabled"
    else:
        # Turn on automatic mode
        scheduler.resume_job('hourly_pipeline')
        job_status["auto_mode"] = True
        update_next_run_time()
        message = "Automatic scheduling enabled"
    
    logging.info(message)
    return jsonify({
        "status": "success",
        "message": message,
        "auto_mode": job_status["auto_mode"],
        "next_run": job_status["next_run"]
    })

@app.route('/health', methods=['GET'])
def health_check():
    """Simple health check endpoint"""
    return jsonify({"status": "healthy"})

@app.route('/logs', methods=['GET'])
def get_logs():
    """Get the latest logs for display in the dashboard"""
    try:
        with open('flask_app.log', 'r') as f:
            # last 50 lines of the log file
            lines = f.readlines()[-50:]
            return ''.join(lines)
    except Exception as e:
        logging.error(f"Error reading log file: {str(e)}")
        return "Error reading logs"

@app.route('/upload-pdf', methods=['POST'])
def upload_pdf():
    """Handle PDF file uploads"""
    # Check if a file was submitted
    if 'file' not in request.files:
        return jsonify({
            "status": "error",
            "message": "No file part"
        }), 400
    
    file = request.files['file']
    
    # Check if file was selected
    if file.filename == '':
        return jsonify({
            "status": "error",
            "message": "No selected file"
        }), 400
    
    # Get form data
    date = request.form.get('date', datetime.now().strftime("%d-%m-%Y"))
    title = request.form.get('title', file.filename)
    published_by = request.form.get('published_by', 'Manual Upload')
    # Check if file type is allowed
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        upload_dir = os.path.join(app.root_path, 'uploads')
        
        # Create uploads directory if it doesn't exist
        os.makedirs(upload_dir, exist_ok=True)
        
        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)

        link = upload_to_drive(file_path, filename)
        if not link:
            return jsonify({
                "status": "error",
                "message": "Error Uploading File"
            }), 400
        os.remove(file_path)
        # Check if scraper or pipeline is running
        if job_status["scraping"] == "running" or job_status["processing"] == "running":
            # Add to queue for later processing
            upload_queue.put({
                'link': link,
                'filename': filename,
                'date': date,
                'title': title,
                'published_by': published_by
            })
            
            return jsonify({
                "status": "queued",
                "message": "File upload queued for processing"
            })
        # else:
        #     # Process immediately
        #     if link:
        #         # Add to CSV
        #         add_to_csv(date, link, title, published_by)
                
        #         # Remove the temporary file
        #         os.remove(file_path)
                
        #         
        #     else:
        #         return jsonify({
        #             "status": "error",
        #             "message": "Failed to upload file to Google Drive"
        #         }), 500
    return jsonify({
            "status": "success",
            "message": "File uploaded successfully",
            "link": link
        })
    

if __name__ == '__main__':
    
    os.makedirs("downloads", exist_ok=True)
    
    # Ensure templates directory exists
    os.makedirs("templates", exist_ok=True)
    
    # Configure the scheduler
    app.config['SCHEDULER_API_ENABLED'] = True
    scheduler.init_app(app)
    
   
    scheduler.add_job(
        id='hourly_pipeline',
        func=scheduled_job,
        trigger='interval',
        hours=1,
        next_run_time=None
    )
    
  
    scheduler.start()

    app.run(host='0.0.0.0', port=5000, debug=False) 