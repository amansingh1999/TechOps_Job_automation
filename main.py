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
import platform
import logging

# -------- CONFIG -------- #
TEMPLATE_PATH = "resume_template.docx"
OUTPUT_DIR = "output"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Secrets from environment (GitHub Actions or local env)
EMAIL_USER = os.getenv("EMAIL_USER")  # Your Gmail
EMAIL_PASS = os.getenv("EMAIL_PASS")  # 16-char App Password
EMAIL_TO = os.getenv("EMAIL_TO") or "your.email@example.com"  # fallback recipient
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Make EMAIL_TO a list if multiple recipients
if EMAIL_TO:
    EMAIL_TO = [e.strip() for e in EMAIL_TO.split(",")]
else:
    EMAIL_TO = []

GMAIL_CREDS_JSON = os.getenv("GMAIL_CREDS_JSON")
DRIVE_CREDS_JSON = os.getenv("DRIVE_CREDS_JSON")

# -------- LOGGING CONFIG -------- #
LOG_FILE = "techops_job_log.txt"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

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
        logging.info("No new TechOps emails found.")
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

# -------- 2. PARSE REMOTE JOBS -------- #
def parse_remote_jobs(email_text):
    soup = BeautifulSoup(email_text, "html.parser")
    text = soup.get_text(separator="\n")

    jobs = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        if "hiring" in line.lower():
            job = {}
            m = re.match(r"(.*?) is hiring (a|an)? ?(.*)", line, re.IGNORECASE)
            if m:
                job['company'] = m.group(1).strip()
                job['title'] = m.group(3).strip()
            else:
                job['company'] = "Unknown"
                job['title'] = line

            job['location'] = "Remote"
            for j in range(i + 1, min(i + 5, len(lines))):
                if "remote" in lines[j].lower():
                    job['location'] = lines[j]
                    break

            job['link'] = None
            url_match = re.search(r'(https?://\S+)', line)
            if url_match:
                job['link'] = url_match.group(1)

            jobs.append(job)

    if jobs:
        print(f"Detected {len(jobs)} remote job(s):")
        for j in jobs:
            print(f" - {j['title']} at {j['company']} ({j['location']}) Link: {j['link']}")
        logging.info(f"Detected {len(jobs)} remote job(s).")
    return jobs

# -------- 3. FETCH JOB DESCRIPTION -------- #
def fetch_jd(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, "Login required or page inaccessible"

        soup = BeautifulSoup(response.text, 'html.parser')
        jd_div = soup.find('div', class_='job-description') or soup.find('div', id='job-desc')
        if not jd_div:
            return None, "Job description not found"

        jd_text = jd_div.get_text(separator="\n")
        return jd_text, None

    except Exception as e:
        return None, str(e)

# -------- 4. EXTRACT KEYWORDS -------- #
def extract_keywords(jd_text):
    skills_list = ["AWS", "Azure", "Terraform", "Kubernetes", "Docker", "CI/CD", "Jenkins", "Python", "Ansible"]
    return [skill for skill in skills_list if skill.lower() in jd_text.lower()]

# -------- 5. GENERATE ATS RESUME -------- #
def generate_resume(job_title, company, keywords):
    doc = Document(TEMPLATE_PATH)
    for p in doc.paragraphs:
        if "[SKILLS_PLACEHOLDER]" in p.text:
            p.text = ", ".join(keywords)
        if "[EXP_PLACEHOLDER]" in p.text:
            p.text = f"Experience working with {', '.join(keywords)} in DevOps, cloud, and CI/CD projects."
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"{OUTPUT_DIR}/{company}_{job_title.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.docx"
    doc.save(filename)
    return filename

# -------- 6. UPLOAD TO GOOGLE DRIVE -------- #
def upload_to_drive(filepath):
    gauth = GoogleAuth()
    gauth.LoadClientConfigFile("credentials_drive.json")
    gauth.LocalWebserverAuth()
    drive = GoogleDrive(gauth)
    file_drive = drive.CreateFile({'title': os.path.basename(filepath)})
    file_drive.SetContentFile(filepath)
    file_drive.Upload()
    return file_drive['alternateLink']

# -------- 7. SEND NOTIFICATIONS (Cross-platform safe) -------- #
def notify(job, resume_path, drive_link=None, error=None):
    subject = f"TechOps Job Alert: {job['title']} at {job['company']}"
    body = f"""New Remote Job Found
Company: {job['company']}
Role: {job['title']}
Location: {job['location']}
Job Link: {job.get('link','N/A')}
Resume: {drive_link if drive_link else resume_path}
Error: {error if error else 'None'}
"""

    # Platform-safe symbols
    if platform.system() == "Windows":
        success_symbol = "[SUCCESS]"
        fail_symbol = "[FAILED]"
        warning_symbol = "[WARNING]"
    else:
        success_symbol = "\u2705"  # ✅
        fail_symbol = "\u274c"     # ❌
        warning_symbol = "\u26A0"  # ⚠️

    # Email
    if EMAIL_TO:
        recipients = EMAIL_TO
        try:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_USER
            msg['To'] = ", ".join(recipients)
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            if resume_path and os.path.exists(resume_path):
                with open(resume_path, "rb") as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(resume_path))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(resume_path)}"'
                    msg.attach(part)

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(EMAIL_USER, EMAIL_PASS)
                server.sendmail(EMAIL_USER, recipients, msg.as_string())

            print(f"{success_symbol} Email sent to: {', '.join(recipients)}")
            logging.info(f"Email sent for job '{job['title']}' at '{job['company']}' to {', '.join(recipients)}")

        except Exception as e:
            print(f"{fail_symbol} Failed to send email: {e}")
            logging.error(f"Failed to send email for job '{job['title']}' at '{job['company']}': {e}")
    else:
        print(f"{warning_symbol} No recipient email set; skipping email notification.")
        logging.warning(f"Skipped email notification for job '{job['title']}' at '{job['company']}' — no recipient set")

    # Telegram
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
