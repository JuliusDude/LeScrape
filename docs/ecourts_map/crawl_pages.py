"""Read-only crawl of the eCourts District Courts portal menus, pages and JS (GET only, ~1 req/s).
No searches are submitted and no CAPTCHA is solved."""
import os, re, time, json, requests
from urllib.parse import urljoin, urlparse
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
B = "https://services.ecourts.gov.in/ecourtindia_v6/"
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
                  "Accept-Language": "en-US,en;q=0.9"})
def get(url):
    time.sleep(1.1)
    for i in range(3):
        try:
            r = s.get(url, timeout=40); return r
        except Exception as e:
            print("retry", url, e); time.sleep(3)
def fname(url):
    q = urlparse(url)
    n = (q.path.strip("/").replace("/", "_") + ("_" + q.query if q.query else "")) or "root"
    return re.sub(r"[^A-Za-z0-9_.=-]", "_", n)[:150]
home = get(B); open(os.path.join(OUT, "home.html"), "w", encoding="utf-8").write(home.text)
links = sorted(set(re.findall(r'href=["\'](\?p=[^"\'#]+|[^"\']*ecourtindia_v6/\?p=[^"\'#]+)', home.text)))
print(len(links), "menu links"); print("\n".join(links))
pages = {}
for l in links:
    url = urljoin(B, l.replace("&amp;", "&"))
    r = get(url); f = fname(url) + ".html"
    open(os.path.join(OUT, f), "w", encoding="utf-8").write(r.text); pages[url] = {"status": r.status_code, "file": f, "len": len(r.text)}
    print(r.status_code, len(r.text), url)
js = set()
for f in ["home.html"] + [v["file"] for v in pages.values()]:
    js |= set(re.findall(r'<script[^>]+src=["\']([^"\']+)', open(os.path.join(OUT, f), encoding="utf-8").read()))
jsmeta = {}
for src in sorted(js):
    url = urljoin(B, src)
    if "ecourts.gov.in" not in url: continue
    r = get(url); f = "js_" + fname(url).split("_v=")[0] + ".js"
    open(os.path.join(OUT, f), "w", encoding="utf-8").write(r.text); jsmeta[url] = {"status": r.status_code, "file": f, "len": len(r.text)}
    print(r.status_code, len(r.text), url)
json.dump({"pages": pages, "js": jsmeta}, open(os.path.join(OUT, "crawl_index.json"), "w"), indent=1)
