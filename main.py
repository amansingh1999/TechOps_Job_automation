import os, re, requests
from datetime import datetime
from docx import Document
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# -------- CONFIG -------- #
TEMPLATE_PATH = "resume_template.docx"
OUTPUT_DIR = "output"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# -------- 1. FETCH EMAIL -------- #
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

    import base64
    email_text = base64.urlsafe_b64decode(body_data).decode()
    return email_text

# -------- 2. PARSE REMOTE JOBS -------- #
def parse_remote_jobs(email_text):
    pattern = r"(.*?) is hiring a (.*?)\n\nRemote Location: (.*)"
    matches = re.findall(pattern, email_text)
    jobs = []
    for company, title, location in matches:
        jobs.append({
            "company": company.strip(),
            "title": title.strip(),
            "location": location.strip(),
            "link": None  # Optional: If link is in email, extract similarly
        })
    return jobs

# -------- 3. KEYWORDS & GENERIC RESUME -------- #
def extract_keywords_from_title(title):
    keywords_map = {
        "Site Reliability Engineer": ["Kubernetes","Terraform","CI/CD","AWS","Monitoring"],
        "Platform Engineer": ["Docker","AWS","CI/CD","Terraform","Python"],
        "DevOps Engineer": ["AWS","Azure","Kubernetes","Terraform","Jenkins","CI/CD"]
    }
    return keywords_map.get(title, ["DevOps","Cloud","CI/CD","Linux"])

def generate_resume(job, keywords):
    doc = Document(TEMPLATE_PATH)
    for p in doc.paragraphs:
        if "[SKILLS_PLACEHOLDER]" in p.text:
            p.text = ", ".join(keywords)
        if "[EXP_PLACEHOLDER]" in p.text:
            p.text = f"Experience in {', '.join(keywords)} in real-time deployments, automation, and CI/CD pipelines."
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"{OUTPUT_DIR}/{job['company']}_{job['title'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.docx"
    doc.save(filename)
    return filename

# -------- 4. UPLOAD TO DRIVE (Account B) -------- #
def upload_to_drive(filepath):
    gauth = GoogleAuth()
    gauth.LoadClientConfigFile("credentials_drive.json")
    gauth.LocalWebserverAuth()
    drive = GoogleDrive(gauth)
    file_drive = drive.CreateFile({'title': os.path.basename(filepath)})
    file_drive.SetContentFile(filepath)
    file_drive.Upload()
    return file_drive['alternateLink']

# -------- 5. NOTIFICATIONS -------- #
def notify(job, resume_path, drive_link=None):
    subject = f"TechOps Job Alert: {job['title']} at {job['company']}"
    body = f"""📢 New Remote Job Found
Company: {job['company']}
Role: {job['title']}
Location: {job['location']}
Job Link: {job.get('link','N/A')}
Resume: {drive_link if drive_link else resume_path}
"""

    # Email
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = EMAIL_USER, EMAIL_TO, subject
    msg.attach(MIMEText(body, 'plain'))
    with open(resume_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=os.path.basename(resume_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(resume_path)}"'
        msg.attach(part)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())

    # Telegram
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": body})

# -------- MAIN -------- #
def main():
    email_text = fetch_latest_email()
    if not email_text:
        return
    jobs = parse_remote_jobs(email_text)
    if not jobs:
        print("No remote jobs found in email.")
        return
    for job in jobs:
        keywords = extract_keywords_from_title(job['title'])
        resume_path = generate_resume(job, keywords)
        drive_link = upload_to_drive(resume_path)
        notify(job, resume_path, drive_link)
    print(f"Processed {len(jobs)} jobs successfully.")

if __name__ == "__main__":
    main()
