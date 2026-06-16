import os

def merge_frontend():
    with open("terminal/index.html", "r") as f:
        html = f.read()
    with open("terminal/style.css", "r") as f:
        css = f.read()
    with open("terminal/app.js", "r") as f:
        js = f.read()

    # Replace link with style
    html = html.replace('<link rel="stylesheet" href="/terminal/style.css?v=7">', f"<style>\n{css}\n</style>")
    # Replace script with inline script
    html = html.replace('<script src="/terminal/app.js?v=7"></script>', f"<script>\n{js}\n</script>")

    with open("dashboard.py", "r") as f:
        db_content = f.read()

    # Find the _index function
    import re
    # We will replace the _index function and the add_static line
    new_index_func = f'''
_FRONTEND_HTML = """{html}"""

async def _index(_request: web.Request) -> web.Response:
    return web.Response(text=_FRONTEND_HTML, content_type="text/html")
'''

    # Substitute _index
    db_content = re.sub(
        r'async def _index.*?return web\.FileResponse[^\n]+\n', 
        new_index_func.strip() + "\n\n", 
        db_content, 
        flags=re.DOTALL
    )

    # Remove the terminal static route
    db_content = re.sub(r'    app\.router\.add_static\("/terminal"[^\n]+\n', '', db_content)
    
    # Update print statement
    db_content = db_content.replace(
        'print(f"Terminal  → http://localhost:{args.port}/terminal/index.html")',
        'print(f"Terminal  → http://localhost:{args.port}/")'
    )

    with open("dashboard.py", "w") as f:
        f.write(db_content)

if __name__ == "__main__":
    merge_frontend()
