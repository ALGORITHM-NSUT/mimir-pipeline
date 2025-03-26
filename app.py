from flask import Flask, jsonify, request, render_template
import subprocess
import os
import logging
import threading
from datetime import datetime
from flask_apscheduler import APScheduler

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