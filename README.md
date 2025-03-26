# Mimir Pipeline

This project contains scripts for scraping university notifications and processing them into a searchable database.

## Project Structure

- `app.py` - Flask server that orchestrates the pipeline
- `new.py` - Web scraper for university notifications
- `revamped_pipeline.py` - Main processing pipeline for documents
- `prompt.py` - Contains prompts used by the pipeline

## How to Run

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Set up environment variables:
   - Create a `.env` file with:
     ```
     GOOGLE_API_KEY=your_gemini_api_key
     MONGO_URI=your_mongodb_connection_string
     DRIVE_API=your_google_drive_api_key
     ```

3. Start the Flask server:
   ```
   python app.py
   ```

4. Use the API:
   - `GET /health` - Check if the server is running
   - `GET /status` - Check the status of the pipeline
   - `POST /start` - Start the pipeline process

## API Endpoints

- `GET /health` - Health check endpoint
- `GET /status` - Get the current status of the pipeline
- `POST /start` - Start the pipeline process "# mimir-pipeline" 
