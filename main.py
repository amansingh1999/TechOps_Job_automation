import os
import re
import requests
from bs4 import BeautifulSoup
from docx import Document
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import base64

# -------- CONFIG -------- #
TEMPLATE_PATH = "resume_template.docx"
OUTPUT_DIR = "output"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Secrets from environment (GitHub Actions)
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# GitHub Secrets for credentials JSON
GMAIL_CREDS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON")
DRIVE_CREDS_JSON = os.getenv("DRIVE_CREDENTIALS_JSON")

# -------- WRITE CREDENTIALS FILES AT RUNTIME -------- #
if GMAIL_CREDS_JSON:
    with open("credentials_gmail.json", "w") as f:
        f.write(GMAIL_CREDS_JSON)

if DRIVE_CREDS_JSON:
    with open("credentials_drive.json", "w") as f:
        f.write(DRIVE_CREDS_JSON)

# -------- 1. FETCH LATEST TECHOPS EMAIL -------- #
def fetch_latest_email():
    creds = None
    if os.path.exists('token_gmail.json'):
        creds = Credentials.from_authorized_user_file('token_gmail.json', SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file('credentials_gmail.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token_gmail.json', 'w') as token:
            token.write(creds.to_json())

    service = build('gmail', 'v1', credentials=creds)
    results = service.users().messages().list(userId='me', q='subject:"TechOps Examples" is:unread').execute()
    messages = results.get('messages', [])
    if not messages:
        print("No new TechOps emails found.")
        return None

    msg = service.users().messages().get(userId='me', id=messages[0]['id'], format='full').execute()
    payload = msg['payload']
    parts = payload.get('parts', [])
    body_data = ""
    if parts:
        for part in parts:
            if part['mimeType'] == 'text/html':
                body_data = part['body']['data']
                break
    else:
        body_data = payload['body']['data']

    email_text = base64.urlsafe_b64decode(body_data).decode()
    return email_text

# -------- 2. PARSE REMOTE JOBS (FIXED) -------- #
def parse_remote_jobs(email_text):
    # Convert HTML to plain text
    soup = BeautifulSoup(email_text, "html.parser")
    text = soup.get_text(separator="\n")

    jobs = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        if "hiring" in line.lower():
            job = {}
            # Extract company and title
            m = re.match(r"(.*?) is hiring (a|an)? ?(.*)", line, re.IGNORECASE)
            if m:
                job['company'] = m.group(1).strip()
                job['title'] = m.group(3).strip()
            else:
                job['company'] = "Unknown"
                job['title'] = line

            # Look ahead for remote location
            job['location'] = "Remote"
            for j in range(i + 1, min(i + 5, len(lines))):
                if "remote" in lines[j].lower():
                    job['location'] = lines[j]
                    break

            # Optional: extract URL
            job['link'] = None
            url_match = re.search(r'(https?://\S+)', line)
            if url_match:
                job['link'] = url_match.group(1)

            jobs.append(job)

    # Debug: print found jobs
    if jobs:
        print(f"Detected {len(jobs)} remote job(s):")
        for j in jobs:
            print(f" - {j['title']} at {j['company']} ({j['location']}) Link: {j['link']}")
    return jobs

# -------- 3. FETCH JOB DESCRIPTION -------- #
def fetch_jd(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, "Login required or page inaccessible"
