import urllib.request
import urllib.parse
import json
import http.cookiejar
import sys

sys.stdout.reconfigure(encoding='utf-8')

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. Login as admin
login_data = json.dumps({'username': 'admin', 'password': '123456'}).encode('utf-8')
req = urllib.request.Request('http://localhost:8000/api/auth/login', data=login_data, headers={'Content-Type': 'application/json'})
resp_body = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))
token = resp_body['token']
print('Login OK, token received.')

def search(q_text, base_url='http://localhost:8000'):
    q_enc = urllib.parse.quote(q_text)
    url = f'{base_url}/api/supplies?q={q_enc}'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    r = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))
    print(f"\nResults for '{q_text}' at {base_url} ({len(r['items'])} items):")
    for it in r['items']:
        print(f"  - [{it['code']}] {it['name']} ({it['category']})")

search('tê')
search('te')
search('comp')
search('3m')
search('chỉ')
search('chi')
