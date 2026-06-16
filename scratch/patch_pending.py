import sys

def patch_main():
    with open("main.py", "r") as f:
        content = f.read()
        
    # Replace the missing variable with the passed argument
    content = content.replace("pending_book_moves[:] = still_pending", "if pending_book_moves is not None:\n                        pending_book_moves[:] = still_pending")
    
    with open("main.py", "w") as f:
        f.write(content)
        
patch_main()
