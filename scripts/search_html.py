with open('scratch_tela_repasses.html', encoding='utf-8') as f:
    html = f.read()

import re
matches = re.findall(r'<script\b[^>]*src=[\'"]([^\'"]*)[\'"]', html)
for m in matches:
    if any(k in m.lower() for k in ['repasse', 'ivv', 'custom', 'app', 'js']):
        print('Script src:', m)

for line in html.splitlines():
    if 'imprimir' in line.lower():
        print('Line with imprimir:', line.strip())
