import sys

lines = open('dashboard.py').readlines()
out = []
in_html = False
for line in lines:
    if line.startswith('<div id="wrap">'):
        in_html = True
    if not in_html:
        out.append(line)
    if in_html and line.startswith('"""'):
        in_html = False

open('dashboard.py', 'w').writelines(out)
