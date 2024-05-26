import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import datetime

# قائمة من الحسابات وكلمات المرور
accounts = [
    {"gmail_user": "mohamed.elareef.pt@gmail.com", "gmail_password": "podr mhln ocdw etxn", "usage_count": 0},
    {"gmail_user": "mo.elareef@gmail.com", "gmail_password": "viax ugbh qlic gauq", "usage_count": 0},
    #{"gmail_user": "mohamed.elareef.pt2@gmail.com", "gmail_password": "bnpz iuxo jfmh ujlq", "usage_count": 0},
    {"gmail_user": "3reef.fx@gmail.com", "gmail_password": "edbu glqo mxjl kryt", "usage_count": 0},
    {"gmail_user": "mohamed.elareef.pt3@gmail.com", "gmail_password": "cjex qbis wurf vpuv", "usage_count": 0}
]

MAX_DAILY_OPERATIONS = 500

def get_current_date():
    return datetime.datetime.now().date()

def read_daily_operations_count(file_path):
    try:
        with open(file_path, "r") as file:
            return int(file.read().strip())
    except FileNotFoundError:
        return 0

def update_daily_operations_count(file_path, count):
    with open(file_path, "w") as file:
        file.write(str(count))

def read_current_date(file_path):
    try:
        with open(file_path, "r") as file:
            return datetime.datetime.strptime(file.read().strip(), "%Y-%m-%d").date()
    except FileNotFoundError:
        return None

def write_current_date(file_path, date):
    with open(file_path, "w") as file:
        file.write(date.strftime("%Y-%m-%d"))

def send_email(subject, body, to_address, sent_emails_file, delay_seconds):
    # Set up SMTP connection with Gmail using SSL
    port = 465
    context = ssl.create_default_context()

    # Check if the address is already in the file before sending
    if to_address in get_sent_emails(sent_emails_file):
        print(f"Email already sent to: {to_address}")
        return False

    daily_operations_count_file = "daily_operations_count.txt"
    current_date_file = "current_date.txt"

    current_date = get_current_date()
    last_date = read_current_date(current_date_file)
    if last_date and last_date != current_date:
        print("Resetting daily operations count for the new day.")
        update_daily_operations_count(daily_operations_count_file, 0)

    if read_daily_operations_count(daily_operations_count_file) >= MAX_DAILY_OPERATIONS:
        print("Maximum daily operations exceeded. Please try again later.")
        exit()

    # اختيار حساب بناءً على الاستخدام الأقل
    least_used_account = min(accounts, key=lambda x: x["usage_count"])

    # تحديث عداد الاستخدام
    least_used_account["usage_count"] += 1

    # الحصول على المعلومات
    gmail_user = least_used_account["gmail_user"]
    gmail_password = least_used_account["gmail_password"]

    # Set up the email
    sender_address = gmail_user

    # Set up the email content
    message = MIMEMultipart()
    message["From"] = sender_address
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    # طباعة المعلومات
    print("-----------------------------------------------")
    print("Gmail User:", gmail_user)
    print("least_used_account : ", least_used_account["usage_count"])

    # Check if the address is already in the file before sending
    if to_address in get_sent_emails(sent_emails_file):
        print(f"Email already sent to: {to_address}")
        return False

    with smtplib.SMTP_SSL("smtp.gmail.com", port, context=context) as server:
        server.login(gmail_user, gmail_password)

        # Set the "To" header
        message["To"] = to_address

        try:
            server.sendmail(sender_address, to_address, message.as_string())
            print(f"Email sent to: {to_address}")

            # Store the address in the file after successful sending
            store_sent_email(to_address, sent_emails_file)

            update_daily_operations_count(daily_operations_count_file, read_daily_operations_count(daily_operations_count_file) + 1)

            # Update the current date file
            write_current_date(current_date_file, current_date)
        except smtplib.SMTPDataError as e:
            print(f"Failed to send email to: {to_address}")
            print(f"Error: {e}")

    print("Email sent successfully.")
    time.sleep(delay_seconds)
    return True

def get_sent_emails(sent_emails_file):
    try:
        with open(sent_emails_file, "r") as file:
            return [line.strip() for line in file.readlines()]
    except FileNotFoundError:
        return []

def store_sent_email(email, sent_emails_file):
    with open(sent_emails_file, "a") as file:
        file.write(email + "\n")

def read_email_content(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return ""

# Use the function to read the email content from the file
email_content_file_path = "Email-Content-EN.txt"
email_content = read_email_content(email_content_file_path)

if email_content:
    print("Email Content:")
    print(email_content)
else:
    print("Failed to read email content.")

# Use the function with the new data
#subject_example = "Consulta sobre Oportunidades para Desenvolvedor Web"
subject_example = "Question about opportunities for Software Engineer"
body_example = email_content
#to_addresses_example = "mo.elareef@gmail.com,hazem.hassan545173137@gmail.com,m.a.elareef@gmail.com"
to_addresses_example = ",".join([line.strip() for line in open("discovered_emails.txt", "r")])
sent_emails_file_example = "sent_emails.txt"
delay_seconds_example = 1  # Set the time delay between sends

# Set the number of emails to send
num_emails_to_send = 8  # Set the desired number of emails to send

# Send the email to each email address up to the specified number
sent_emails_count = 0
for to_address in to_addresses_example.split(","):
    if sent_emails_count < num_emails_to_send:
        if send_email(subject_example, body_example, to_address, sent_emails_file_example, delay_seconds_example):
            sent_emails_count += 1
    else:
        print("Stop sending emails once the specified number is reached...")
        break  # Stop sending emails once the specified number is reached
