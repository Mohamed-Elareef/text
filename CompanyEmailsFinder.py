import itertools
import requests
from bs4 import BeautifulSoup
import os
import re

def is_domain_working(domain_name):
    url = "http://" + domain_name
    try:
        response = requests.get(url)
        return response.status_code == 200
    except requests.ConnectionError:
        return False

def save_to_file(filename, data):
    with open(filename, 'a') as file:
        file.write(data + '\n')

def read_domains_from_file(filename):
    if not os.path.exists(filename):
        return set()
    
    with open(filename, 'r') as file:
        return set(line.strip() for line in file)

def read_emails_from_file(filename):
    if not os.path.exists(filename):
        return set()
    
    with open(filename, 'r') as file:
        return set(line.strip() for line in file)

def extract_emails_from_text(text):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    return re.findall(pattern, text)

def is_image_response(response):
    content_type = response.headers.get('Content-Type', '').lower()
    return content_type.startswith('image/')

# ...

def generate_domains(max_attempts, working_filename, not_working_filename):
    characters = 'abcdefghijklmnopqrstuvwxyz'
    domain = '.at'

    working_domains = read_domains_from_file(working_filename)
    not_working_domains = read_domains_from_file(not_working_filename)

    attempts = 0
    for length in range(1, len(characters) + 1):
        for combination in itertools.product(characters, repeat=length):
            domain_name = ''.join(combination) + domain

            # التحقق من أن الدومين لم يتم فحصه مسبقًا
            if domain_name not in working_domains and domain_name not in not_working_domains:
                yield domain_name

                attempts += 1
                if attempts >= max_attempts:
                    break

    # حفظ النطاقات العاملة وغير العاملة بعد كل جولة من جولات التكرار
    save_to_file(working_filename, '\n'.join(working_domains))
    save_to_file(not_working_filename, '\n'.join(not_working_domains))

# ...

def crawl_and_extract_emails(domain_name, working_filename, not_working_filename, emails_filename):
    if not is_domain_working(domain_name):
        print(f"{domain_name} - Not Working")
        save_to_file(not_working_filename, domain_name)  # تحديث ملف الدومينات التي لا تعمل
        return

    print(f"{domain_name} - Working")

    homepage_url = "http://" + domain_name
    homepage_content = requests.get(homepage_url).text

    # بحث عن البريد الإلكتروني في الصفحة الرئيسية
    emails_homepage = extract_emails_from_text(homepage_content)
    existing_emails = read_emails_from_file(emails_filename)

    for email in emails_homepage:
        if email.lower().endswith('.pt') and email not in existing_emails:
            print(f"Email found on homepage: {email}")
            save_to_file(emails_filename, email)
            existing_emails.add(email)

    soup = BeautifulSoup(homepage_content, 'html.parser')
    links = [a['href'] for a in soup.find_all('a', href=True) if a['href'].startswith('http')]

    links_examined_count = 0  # تعيين عدد الروابط التي تم فحصها إلى صفر

    for link in links:
        try:
            response = requests.get(link)

            # التحقق من أن الملف ليس صورة
            if not is_image_response(response):
                link_content = response.text
                emails = extract_emails_from_text(link_content)

                for email in emails:
                    if email.lower().endswith('.pt') and email not in existing_emails:
                        print(f"Email found: {email}")
                        save_to_file(emails_filename, email)
                        existing_emails.add(email)

                links_examined_count += 1  # زيادة عدد الروابط التي تم فحصها

        except requests.exceptions.RequestException as e:
            print(f"Error accessing link: {link} - {e}")

    # حفظ الدومين الذي نجح في العمل وتحديث ملف الدومينات التي تعمل
    save_to_file(working_filename, domain_name)

    # طباعة عدد الروابط التي تم فحصها
    print(f"Links examined in {domain_name}: {links_examined_count}")

# ...


# تحديد عدد المحاولات المطلوبة
max_attempts = 1000

# تحديد اسمي الملفين
working_domains_file = 'working_domains.txt'
not_working_domains_file = 'not_working_domains.txt'
emails_filename = 'discovered_emails.txt'

# اختبار البرنامج بطباعة أسماء النطاقات والتحقق من عملها
for domain_name in generate_domains(max_attempts, working_domains_file, not_working_domains_file):
    crawl_and_extract_emails(domain_name, working_domains_file, not_working_domains_file, emails_filename)
