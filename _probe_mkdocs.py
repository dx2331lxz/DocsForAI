import re
import urllib.request

url = "https://scrapling.readthedocs.io/en/latest/"
with urllib.request.urlopen(url) as r:
    html = r.read().decode("utf-8")

# Extract all nav links
links = re.findall(r'class="md-nav__link[^"]*"[^>]*href="([^"]+)"', html)
links += re.findall(r'href="([^"]+)"[^>]*class="md-nav__link', html)

seen = set()
for l in links:
    if l not in seen and not l.startswith("#") and not l.startswith("http"):
        seen.add(l)
        print(l)

print("\nTotal:", len(seen))
