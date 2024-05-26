import subprocess
import re
import itertools
import urllib.request



import shutil
from datetime import datetime
import os


def backup_and_update_file(file_path, backup_path):
    # التأكد من وجود الملف الأساسي
    if not os.path.exists(file_path):
        print(f"File {file_path} does not exist.")
        return
    
    # قراءة محتوى الملف الأساسي
    with open(file_path, 'r') as file:
        content = file.read()
    
    # التحقق إذا كان الملف يحتوي على نص أو أرقام
    if re.search(r'\S', content):
        # إذا كان يحتوي، إنشاء نسخة احتياطية
        shutil.copy(file_path, backup_path)
        print(f"Backup created at {backup_path}.")
    else:
        # إذا كان لا يحتوي، استعادة النسخة الاحتياطية
        if os.path.exists(backup_path):
            shutil.copy(backup_path, file_path)
            print(f"File {file_path} updated with backup content from {backup_path}.")
        else:
            print(f"Backup file {backup_path} does not exist.")

# استخدام الدالة
file_path = "program_state.txt"
backup_path = "program_state_backup.txt"
backup_and_update_file(file_path, backup_path)



def check_internet_connection():
    try:
        urllib.request.urlopen('http://www.google.com', timeout=1)
        return True
    except urllib.error.URLError:
        return False

def copy_file_with_datetime(original_file, destination_directory):
    # استخراج اسم الملف وامتداده
    file_name, file_extension = original_file.split('.')

    # التاريخ والوقت الحاليين
    current_datetime = datetime.now().strftime("%Y%m%d%H%M%S")

    # إنشاء اسم الملف الجديد مع التاريخ والوقت
    new_file_name = f"{file_name}_{current_datetime}.{file_extension}"

    # إنشاء المسار الكامل للملف الجديد
    new_file_path = f"{destination_directory}/{new_file_name}"

    try:
        # نسخ الملف
        shutil.copy(original_file, new_file_path)
        print(f"تم نسخ الملف بنجاح إلى: {new_file_path}")
    except FileNotFoundError:
        print(f"خطأ: الملف {original_file} غير موجود.")
    except PermissionError:
        print(f"خطأ: ليس لديك إذن للوصول إلى الملف {original_file}.")
    except Exception as e:
        print(f"حدث خطأ غير متوقع: {e}")

# استخدام الدالة
original_file_path = "discovered_emails.txt"
destination_directory = "/"
#copy_file_with_datetime(original_file_path, destination_directory)

#--------------

def run_whois(domain):
    try:
        result = subprocess.check_output(['whois', domain], universal_newlines=True)
        return result
    except subprocess.CalledProcessError as e:
        return f"Error running whois: {e}"

def extract_emails(whois_result):
    # Use regex to extract email addresses
    email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    emails = email_pattern.findall(whois_result)
    return emails

def write_to_file(emails, file_path):
    # Read existing emails from file to avoid duplicates
    existing_emails = set()
    try:
        with open(file_path, "r") as file:
            existing_emails = {line.strip() for line in file}
    except FileNotFoundError:
        pass  # File doesn't exist yet, no problem

    # Add new emails to the set
    new_emails = set(emails) - existing_emails

    # Write unique emails to file
    with open(file_path, "a") as file:
        for email in new_emails:
            file.write(f"{email}\n")

def read_state(file_path):
    try:
        with open(file_path, "r") as file:
            lines = file.readlines()
            if len(lines) == 2:
                return int(lines[0].strip()), lines[1].strip()
    except FileNotFoundError:
        pass  # File doesn't exist yet, no problem
    return 1, ''  # Default values if file doesn't exist

def write_state(counter, domain_state, file_path):
    with open(file_path, "w") as file:
        file.write(f"{counter}\n{domain_state}")

# Specify the file paths
file_path = "discovered_emails.txt"
state_file_path = "program_state.txt"

# Read the counter and domain state from the file
counter, domain_state = read_state(state_file_path)

# Domain generation with precise stopping point
characters = 'abcdefghijklmnopqrstuvwxyz0123456789-'
domain = '.eu'

# Calculate the total number of attempts based on the length of characters
total_attempts = sum(len(characters) ** i for i in range(1, len(characters) + 1))

# Iterate through all domains based on total attempts
#for attempt in range(counter, total_attempts + 1):
#for attempt in range(counter, counter + 50): 
for attempt in range(counter, total_attempts + 1):
    remainder = attempt
    for length in range(1, len(characters) + 1):
        possibilities = len(characters) ** length
        if remainder <= possibilities:
            break
        remainder -= possibilities

    # Generate the domain based on the current length and remainder
    combination = itertools.product(characters, repeat=length)
    domain_name = ''.join(next(itertools.islice(combination, remainder - 1, None))) + domain

    # Run whois for the current domain
    whois_result = run_whois(domain_name)

    # Extract and print email addresses
    emails = extract_emails(whois_result)
    print(f"Email addresses for {domain_name}:")
    for email in emails:
        print(email)

    # Write unique emails to file while considering occurrence count
    if emails:
        write_to_file(emails, file_path)
        print(f"Unique emails for {domain_name} written to 'discovered_emails.txt'")

    # Save the current state (counter and domain) after each iteration
    if check_internet_connection():    
        write_state(attempt + 1, domain_name, state_file_path)
