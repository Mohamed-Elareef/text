import urllib.request

def check_internet_connection():
    try:
        urllib.request.urlopen('http://www.google.com', timeout=1)
        return True
    except urllib.error.URLError:
        return False

if check_internet_connection():
    print("الإنترنت يعمل. يمكنك البدء في العمل.")
else:
    print("لا يمكن الاتصال بالإنترنت. الرجاء التحقق من اتصالك بالشبكة.")
