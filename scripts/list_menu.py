with open('scratch_tela_repasses.html', encoding='utf-8') as f:
    html = f.read()

import re
links = re.findall(r'<a\b[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', html, re.DOTALL)
seen = set()
for h, t in links:
    clean_t = ' '.join(re.sub(r'<[^>]+>', ' ', t).split())
    if h not in seen and not h.startswith('#') and not h.startswith('javascript'):
        seen.add(h)
        print(f'{clean_t:<35} -> {h}')
