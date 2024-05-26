import itertools
import requests
from bs4 import BeautifulSoup
import os
import re
import random
import time
from concurrent.futures import ThreadPoolExecutor
import concurrent.futures
import httpx




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

def generate_domains(max_attempts, working_filename, not_working_filename, processing_filename):
    characters = 'abcdefghijklmnopqrstuvwxyz0123456789-'
    characters = 'abcdefghijklmnopqrstuvwxyz'
    characters = ''.join(random.sample(characters, len(characters)))
    print("characters : "+characters)
    domain = '.at'

    working_domains = read_domains_from_file(working_filename)
    not_working_domains = read_domains_from_file(not_working_filename)
    processing_domains = read_domains_from_file(processing_filename)

    attempts = 0
    for length in range(1, len(characters) + 1):
        for combination in itertools.product(characters, repeat=length):
            domain_name = ''.join(combination) + domain

            # التحقق من أن الدومين لم يتم فحصه مسبقًا
            if domain_name not in working_domains and domain_name not in not_working_domains and domain_name not in processing_domains:
                # قم بإضافة الدومين إلى ملف المعالجة المؤقتة
                save_to_file(processing_filename, domain_name)
                yield domain_name

                attempts += 1
                if attempts >= max_attempts:
                    break

    # حذف الدومين من ملف المعالجة المؤقتة عند الانتهاء من الجولات
    save_to_file(processing_filename, '')
    
    # حفظ الدومينات العاملة وغير العاملة بعد كل جولة من جولات التكرار
    save_to_file(working_filename, '\n'.join(working_domains))
    save_to_file(not_working_filename, '\n'.join(not_working_domains))





def fetch_homepage_content(url):
    try:
        response = requests.get(url, timeout=MAX_TIME_THRESHOLD)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Error accessing link: {url} - {e}")
        return None

   

# ...

MAX_LINKS_THRESHOLD = 10  # يمكنك تعيين الحد الأقصى لعدد الروابط
MAX_TIME_THRESHOLD = 1  # يمكنك تعيين الحد الأقصى للوقت المستغرق لجلب محتوى الرابط الرئيسي (بالثواني)


def crawl_and_extract_emails(domain_name, working_filename, not_working_filename, emails_filename, processing_filename):
    if not is_domain_working(domain_name):
        print(f"{domain_name} - Not Working")
        save_to_file(not_working_filename, domain_name)  # تحديث ملف الدومينات التي لا تعمل
        return

    print(f"{domain_name} - Working")

    homepage_url = "http://" + domain_name

    # قياس الوقت الذي يستغرقه جلب محتوى الرابط الأساسي
    start_time = time.time()

    # استخدام ThreadPoolExecutor لإرسال الطلبات بشكل متزامن
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(fetch_homepage_content, homepage_url)
        homepage_content = future.result()

    elapsed_time = time.time() - start_time

    if homepage_content is None:
        print(f"Skipping {domain_name} due to exceeding time limit")
        return

    print(f"Time elapsed for {domain_name}: {elapsed_time} seconds")

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

    # حفظ عدد الروابط قبل محاولة فحصها
    total_links_count = len(links)
    links_examined_count = 0

    # التحقق من أن عدد الروابط لا يتجاوز الحد الأقصى
    if total_links_count <= MAX_LINKS_THRESHOLD:
        for link in links:
            try:
                response = requests.get(link, timeout=MAX_TIME_THRESHOLD)

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

    # إذا كان عدد الروابط أكبر من الحد الأقصى، فلا تقم بحفظ الدومين في ملف العمل
    if total_links_count <= MAX_LINKS_THRESHOLD:
        # حفظ الدومين الذي نجح في العمل وحذفه من ملف المعالجة المؤقتة
        save_to_file(working_filename, domain_name)
        #remove_domain_from_file(processing_filename, domain_name)

    # طباعة عدد الروابط التي تم فحصها
    print(f"Total links in {domain_name}: {total_links_count}")
    print(f"Links examined in {domain_name}: {links_examined_count}")

# ...


def remove_domain_from_file(filename, domain_name):
    existing_domains = read_domains_from_file(filename)
    existing_domains.discard(domain_name)

    # حذف محتوى الملف
    open(filename, 'w').close()

    # إعادة كتابة الملف بشكل صحيح
    with open(filename, 'a') as file:
        for existing_domain in existing_domains:
            # التأكد من أن كل دومين ينتهي بـ ".pt" وكتابته في سطر منفصل
            if existing_domain.strip().endswith(".pt"):
                file.write(existing_domain.strip() + '\n')

# ...





# تحديد عدد المحاولات المطلوبة
max_attempts = 100000000

# تحديد اسمي الملفين
working_domains_file = 'working_domains.txt'
not_working_domains_file = 'not_working_domains.txt'
emails_filename = 'discovered_emails.txt'
processing_domains_file = 'processing_domains.txt'


# اختبار البرنامج بطباعة أسماء النطاقات والتحقق من عملها
for domain_name in generate_domains(max_attempts, working_domains_file, not_working_domains_file, processing_domains_file):
    crawl_and_extract_emails(domain_name, working_domains_file, not_working_domains_file, emails_filename, processing_domains_file)
